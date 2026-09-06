#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Bounded R8 Topic Memory product-chain acceptance harness."""

# ruff: noqa: TRY003

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from pydantic import AnyHttpUrl, SecretStr
from starlette.middleware import Middleware

from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryEvolveOutput,
    TopicMemoryGlobalOutput,
    TopicMemoryPlannerOutput,
    TopicMemoryProbeOutput,
    TopicMemoryReconcileOutput,
    TopicMemoryTemporaryOutput,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    BearerAuthConfig,
    DashboardConfig,
    DashboardScopeConfig,
    McpConfig,
    ServerSettings,
)
from tests.e2e.topic_memory_product.common import (
    AccessTimeline,
    AccessTimelineMiddleware,
    ArtifactIdentity,
    PreparedContextAudit,
    PreparedContextAuditMiddleware,
    ProductChainError,
    digest_text,
    exercise_http_mcp_prepared_web_chain,
    run_e0,
    start_loopback_server,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SELECTOR = "powercontext@powercontext"
E1_SCOPE_ID = "project:r8-real-codex"
E1_CANARY = "TOPAZ-R8-REAL-CODEX-CANARY"
DEFAULT_CODEX_MODEL = "gpt-5.6-luna"
DEFAULT_GENERATION_MODEL = "gpt-5.6-luna"


class _WorkerFailureCapture(logging.Handler):
    """Capture only the worker's content-free failure classification."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.failures: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) != "artifact_processing.worker.failed":
            return
        self.failures.append({
            "stage": str(getattr(record, "stage", "unknown")),
            "error_code": str(getattr(record, "error_code", "unknown")),
            "exception_type": str(getattr(record, "exception_type", "unknown")),
            "failure_count": int(getattr(record, "failure_count", 0)),
        })


@dataclass(slots=True)
class _RealCodexChatBridge:
    """Expose bounded real Codex generations through an OpenAI-compatible loopback API."""

    codex_home: Path
    fixture: Path
    environment: Mapping[str, str]
    model: str
    timeout: float
    max_calls: int = 4
    calls: list[dict[str, object]] = field(default_factory=list)
    _external_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> dict[str, object]:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="invalid generation request")
            if "response_format" not in payload and "tools" not in payload:
                with self._lock:
                    self.calls.append({"stage": "readiness", "external_generation": False})
                return _chat_completion("READY", model=self.model, call_id="readiness")
            with self._lock:
                call_number = self._external_calls + 1
                if call_number > self.max_calls:
                    raise HTTPException(status_code=429, detail="R8 generation call budget exhausted")
                self._external_calls = call_number
            started = time.monotonic()
            try:
                content = await asyncio.to_thread(self._generate, payload, call_number)
            except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
                with self._lock:
                    self.calls.append({
                        "call": call_number,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "outcome": "failure",
                        "exception_type": type(exc).__name__,
                        "request_fields": sorted(payload),
                        "response_format_present": "response_format" in payload,
                        "response_format_shape": _mapping_shape(payload.get("response_format")),
                        "tool_count": len(payload.get("tools", [])) if isinstance(payload.get("tools"), list) else 0,
                    })
                raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
            duration = round(time.monotonic() - started, 3)
            with self._lock:
                self.calls.append({
                    "call": call_number,
                    "duration_seconds": duration,
                    "structured_output": True,
                    "output_bytes": len(content.encode()),
                })
            return _chat_completion(content, model=self.model, call_id=str(call_number))

        return app

    def _generate(self, payload: Mapping[str, object], call_number: int) -> str:
        schema = _generation_schema(payload)
        output_path = self.codex_home / f"generation-output-{call_number}.json"
        prompt = (
            "Act as a bounded structured-generation backend for a synthetic PowerContext acceptance run. "
            "Follow the supplied messages exactly, use no tools, and return only the JSON value required by "
            "the output schema, with no Markdown fence or explanation. The output schema is:\n"
            + json.dumps(schema, ensure_ascii=False)
            + "\nThe request is:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            _run(
                (
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--json",
                    "--color",
                    "never",
                    "--sandbox",
                    "read-only",
                    "--model",
                    self.model,
                    "--config",
                    'model_reasoning_effort="low"',
                    "--output-last-message",
                    str(output_path),
                    "--cd",
                    str(self.fixture),
                    "-",
                ),
                cwd=self.fixture,
                env={**self.environment, "CODEX_HOME": str(self.codex_home)},
                timeout=self.timeout,
                input_data=prompt,
            )
            output = output_path.read_text(encoding="utf-8").strip()
            decoded = json.loads(output)
            if not isinstance(decoded, dict):
                raise TypeError("real Codex generation output was not a JSON object")
            return json.dumps(decoded, separators=(",", ":"), ensure_ascii=False)
        finally:
            output_path.unlink(missing_ok=True)

    def redacted_calls(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(call) for call in self.calls]


def _chat_completion(content: str, *, model: str, call_id: str) -> dict[str, object]:
    return {
        "id": f"r8-real-codex-generation-{call_id}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _generation_schema(payload: Mapping[str, object]) -> Mapping[str, object]:
    response_format = payload.get("response_format")
    if isinstance(response_format, dict):
        json_schema = response_format.get("json_schema")
        schema = json_schema.get("schema") if isinstance(json_schema, dict) else None
        if isinstance(schema, dict):
            return cast(Mapping[str, object], schema)
    tools = payload.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            parameters = function.get("parameters") if isinstance(function, dict) else None
            if isinstance(parameters, dict):
                return cast(Mapping[str, object], parameters)
    rendered_messages = json.dumps(payload.get("messages"), ensure_ascii=False)
    stage_schemas = (
        ("Identify up to 20 durable topic probes", TopicMemoryProbeOutput),
        ("Evolve the whole Window into at most 20 topics", TopicMemoryGlobalOutput),
        ("Plan historical matches for each Topic probe", TopicMemoryPlannerOutput),
        ("Evolve one Topic probe against its candidate set", TopicMemoryEvolveOutput),
        ("Draft temporary Topics for unresolved evidence", TopicMemoryTemporaryOutput),
        ("Reconcile Topic proposals into one publication set", TopicMemoryReconcileOutput),
    )
    for marker, output_type in stage_schemas:
        if marker in rendered_messages:
            return cast(Mapping[str, object], output_type.model_json_schema())
    raise ValueError("generation request omitted a machine-readable output schema")


def _mapping_shape(value: object, *, depth: int = 0) -> object:
    if not isinstance(value, dict):
        return type(value).__name__
    if depth >= 2:
        return sorted(str(key) for key in value)
    return {str(key): _mapping_shape(child, depth=depth + 1) for key, child in value.items()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layers",
        default="e0,e1,e2,e3,e4",
        help="Comma-separated layers. E0 is always run and cannot be omitted.",
    )
    parser.add_argument("--output", type=Path, default=Path(".artifacts/topic-memory-r8"))
    parser.add_argument("--codex-timeout", type=float, default=180.0)
    parser.add_argument("--generation-timeout", type=float, default=240.0)
    parser.add_argument("--codex-model", default=os.environ.get("POWERCONTEXT_R8_CODEX_MODEL", DEFAULT_CODEX_MODEL))
    parser.add_argument(
        "--generation-model",
        default=os.environ.get("POWERCONTEXT_R8_GENERATION_MODEL", DEFAULT_GENERATION_MODEL),
    )
    return parser.parse_args()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(value, indent=2, sort_keys=True)}\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    input_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=dict(env),
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
        input=input_data,
        stdin=subprocess.DEVNULL if input_data is None else None,
    )


def _codex_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    if not events:
        raise ProductChainError("Codex emitted no machine-readable JSONL events")
    return events


def _walk_mappings(value: object) -> list[Mapping[str, object]]:
    found: list[Mapping[str, object]] = []
    if isinstance(value, dict):
        found.append(cast(Mapping[str, object], value))
        for child in value.values():
            found.extend(_walk_mappings(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_mappings(child))
    return found


def _codex_summary(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    event_types = [value for event in events if isinstance((value := event.get("type")), str)]
    tools: list[str] = []
    for event in events:
        for item in _walk_mappings(event):
            for key in ("tool", "tool_name", "name"):
                value = item.get(key)
                if value in {"search_topic_memory", "get_topic_memory"} and value not in tools:
                    tools.append(str(value))
    return {
        "event_count": len(events),
        "event_types": sorted(set(event_types)),
        "mcp_tools_in_observed_order": tools,
        "raw_jsonl_retained": False,
        "full_model_output_retained": False,
    }


def _write_redacted_codex_jsonl(
    path: Path,
    events: Sequence[Mapping[str, object]],
    *,
    exact_ref: ArtifactIdentity | None = None,
) -> dict[str, object]:
    """Write event topology and allowed identity/tool facts, never event payloads."""

    exact_artifact_id = None if exact_ref is None else exact_ref.artifact_id
    records: list[dict[str, object]] = []
    for sequence, event in enumerate(events, start=1):
        event_type = event.get("type")
        tools: list[str] = []
        for item in _walk_mappings(event):
            for key in ("tool", "tool_name", "name"):
                value = item.get(key)
                if value in {"search_topic_memory", "get_topic_memory"} and value not in tools:
                    tools.append(str(value))
        record: dict[str, object] = {
            "sequence": sequence,
            "type": event_type if isinstance(event_type, str) else "unknown",
            "mcp_tools": tools,
        }
        if exact_artifact_id is not None:
            record["exact_artifact_id_observed"] = exact_artifact_id in json.dumps(event, sort_keys=True)
        records.append(record)
    path.write_text("".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records), encoding="utf-8")
    return {
        "file": path.name,
        "event_count": len(records),
        "prompt_or_model_text_retained": False,
        "tool_arguments_retained": False,
    }


def _run_codex(
    *,
    codex_home: Path,
    fixture: Path,
    environment: Mapping[str, str],
    prompt: str,
    model: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], float]:
    started = time.monotonic()
    completed = _run(
        (
            "codex",
            "exec",
            "--ephemeral",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "read-only",
            "--dangerously-bypass-hook-trust",
            "--model",
            model,
            "--config",
            'model_reasoning_effort="low"',
            "--cd",
            str(fixture),
            "-",
        ),
        cwd=fixture,
        env={**environment, "CODEX_HOME": str(codex_home)},
        timeout=timeout,
        input_data=prompt,
    )
    return _codex_events(completed.stdout), round(time.monotonic() - started, 3)


def _install_current_plugin(*, codex_home: Path, environment: Mapping[str, str], timeout: float) -> dict[str, object]:
    env = {**environment, "CODEX_HOME": str(codex_home)}
    marketplace = _run(
        ("codex", "plugin", "marketplace", "add", str(PROJECT_ROOT), "--json"),
        cwd=PROJECT_ROOT,
        env=env,
        timeout=timeout,
    )
    installed = _run(
        ("codex", "plugin", "add", PLUGIN_SELECTOR, "--json"),
        cwd=PROJECT_ROOT,
        env=env,
        timeout=timeout,
    )
    marketplace_result = json.loads(marketplace.stdout)
    installed_result = json.loads(installed.stdout)
    installed_path_value = installed_result.get("installedPath")
    if not isinstance(installed_path_value, str):
        raise ProductChainError("Codex plugin install did not return an installed path")
    installed_path = Path(installed_path_value).resolve()
    manifest_path = installed_path / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("name") != "powercontext" or not isinstance(manifest.get("version"), str):
        raise ProductChainError("installed Codex plugin manifest is invalid")
    return {
        "installed_path": installed_path,
        "marketplace_name": marketplace_result.get("name", "powercontext"),
        "plugin_name": manifest["name"],
        "plugin_version": manifest["version"],
        "plugin_manifest_sha256": _sha256_file(manifest_path),
    }


def _configure_installed_mcp(installed_path: Path, *, base_url: str) -> None:
    configuration_path = installed_path / ".mcp.json"
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    server = configuration["mcpServers"]["powercontext"]
    server["url"] = f"{base_url}/mcp"
    configuration_path.write_text(f"{json.dumps(configuration, indent=2)}\n", encoding="utf-8")


def _browser_python() -> Path:
    configured = os.environ.get("POWERCONTEXT_R8_PLAYWRIGHT_PYTHON")
    if configured:
        candidate = Path(configured)
    else:
        executable = shutil.which("playwright")
        if executable is None:
            raise ProductChainError("Playwright CLI is unavailable for E1 screenshot evidence")
        first_line = Path(executable).read_text(encoding="utf-8").splitlines()[0]
        if not first_line.startswith("#!"):
            raise ProductChainError("Playwright CLI has no discoverable Python interpreter")
        candidate = Path(first_line.removeprefix("#!"))
    if not candidate.is_file():
        raise ProductChainError("Playwright Python interpreter is unavailable")
    return candidate


def _browser_executable() -> Path:
    configured = os.environ.get("POWERCONTEXT_R8_BROWSER_EXECUTABLE")
    if configured:
        candidate = Path(configured)
    else:
        candidates = sorted((Path.home() / ".cache" / "ms-playwright").glob("chromium-*/chrome-linux64/chrome"))
        if not candidates:
            raise ProductChainError("no preinstalled Chromium executable is available")
        candidate = candidates[-1]
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ProductChainError("configured Chromium executable is unavailable")
    return candidate


def _capture_browser_evidence(
    *,
    directory: Path,
    base_url: str,
    token: str,
    artifact_ref: str,
    source_ref: str,
    environment: Mapping[str, str],
) -> dict[str, object]:
    desktop = directory / "topics-desktop-redacted.png"
    narrow = directory / "topics-narrow-redacted.png"
    audit = directory / "browser-audit.json"
    try:
        completed = _run(
            (
                str(_browser_python()),
                str(Path(__file__).with_name("browser_capture.py")),
                "--base-url",
                base_url,
                "--artifact-ref",
                artifact_ref,
                "--source-ref",
                source_ref,
                "--desktop",
                str(desktop),
                "--narrow",
                str(narrow),
                "--audit",
                str(audit),
            ),
            cwd=PROJECT_ROOT,
            env={
                **environment,
                "POWERCONTEXT_R8_BROWSER_TOKEN": token,
                "POWERCONTEXT_R8_BROWSER_EXECUTABLE": str(_browser_executable()),
            },
            timeout=90,
        )
    except subprocess.CalledProcessError as exc:
        raise ProductChainError(f"browser evidence failed: {exc.stderr[-2000:]}") from exc
    if completed.stdout or completed.stderr:
        # Browser output is intentionally discarded because it is not acceptance evidence.
        pass
    return {
        "status": "PASS",
        "desktop": desktop.name,
        "narrow": narrow.name,
        "audit": audit.name,
        "generated_content_redacted": True,
    }


def _prepare_codex_home(temp_root: Path) -> tuple[Path, dict[str, object]]:
    source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    source_auth = source_home / "auth.json"
    if not source_auth.is_file():
        raise ProductChainError("Codex auth.json is unavailable for isolated E1 execution")
    source_digest = _sha256_file(source_auth)
    codex_home = temp_root / "codex-home"
    codex_home.mkdir(mode=0o700)
    destination = codex_home / "auth.json"
    shutil.copyfile(source_auth, destination)
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    if stat.S_IMODE(destination.stat().st_mode) != 0o600:
        raise ProductChainError("isolated Codex auth copy is not mode 0600")
    provider_keys = _copy_codex_provider_config(source_home / "config.toml", codex_home / "config.toml")
    return codex_home, {
        "source_auth_sha256_before": source_digest,
        "source_auth_path_recorded": False,
        "temporary_auth_mode": "0600",
        "temporary_home_mode": oct(stat.S_IMODE(codex_home.stat().st_mode)),
        "provider_config_keys_copied": provider_keys,
        "unrelated_user_config_copied": False,
    }


def _copy_codex_provider_config(source: Path, destination: Path) -> list[str]:
    if not source.is_file():
        raise ProductChainError("Codex provider config is unavailable for isolated E1 execution")
    configuration = tomllib.loads(source.read_text(encoding="utf-8"))
    selected = configuration.get("model_provider")
    providers = configuration.get("model_providers")
    if selected is None:
        # Codex's built-in OpenAI provider needs no explicit provider block;
        # the isolated auth.json copy is sufficient and no unrelated config
        # should cross the acceptance boundary.
        destination.write_text("", encoding="utf-8")
        destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return []
    if not isinstance(selected, str) or not isinstance(providers, dict):
        raise ProductChainError("Codex selected provider config is invalid")
    provider = providers.get(selected)
    if not isinstance(provider, dict):
        raise ProductChainError("selected Codex provider definition is unavailable")
    allowed = ("name", "base_url", "wire_api", "requires_openai_auth", "env_key")
    values = {key: provider[key] for key in allowed if key in provider}
    if not isinstance(values.get("base_url"), str) or not isinstance(values.get("env_key"), str):
        raise ProductChainError("selected Codex provider is missing its endpoint or environment key")

    def encode(value: object) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, str):
            return json.dumps(value)
        raise ProductChainError("selected Codex provider contains an unsupported value")

    lines = [f"model_provider = {json.dumps(selected)}", "", f"[model_providers.{selected}]"]
    lines.extend(f"{key} = {encode(value)}" for key, value in values.items())
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return sorted(values)


def run_e1(  # noqa: C901
    directory: Path,
    *,
    codex_model: str,
    generation_model: str,
    codex_timeout: float,
    generation_timeout: float,
) -> dict[str, object]:
    """Run real Codex, plugin hook, MCP, generation, HTTP, and browser acceptance."""

    directory.mkdir(parents=True, exist_ok=True)
    if shutil.which("codex") is None:
        return _unavailable("E1", "Codex CLI is not installed")
    environment = dict(os.environ)
    # The installed hook pins ``uv run --frozen`` itself. UV rejects combining
    # that flag with UV_LOCKED, which callers commonly set for repository gates.
    environment.pop("UV_LOCKED", None)
    temporary_openai_key = False
    if not environment.get("OPENAI_API_KEY"):
        # Pydantic AI requires this variable while talking to our authenticated
        # loopback bridge. The bridge invokes the real provider through the
        # isolated Codex home/auth copy, so no provider credential belongs here.
        environment["OPENAI_API_KEY"] = "r8-loopback-only"
        os.environ["OPENAI_API_KEY"] = "r8-loopback-only"
        temporary_openai_key = True

    token = secrets.token_urlsafe(32)
    timeline = AccessTimeline()
    prepared_audit = PreparedContextAudit()
    server = None
    generation_server = None
    temp_root_value = ""
    source_auth: Path | None = None
    source_auth_digest = ""
    result: dict[str, object]
    failure_capture = _WorkerFailureCapture()
    worker_logger = logging.getLogger("powercontext.builtin.runtime.artifact_processing")
    worker_logger.addHandler(failure_capture)
    try:
        with tempfile.TemporaryDirectory(prefix="powercontext-r8-e1-") as temp_value:
            temp_root = Path(temp_value)
            temp_root_value = temp_value
            codex_home, auth_audit = _prepare_codex_home(temp_root)
            source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            source_auth = source_home / "auth.json"
            source_auth_digest = str(auth_audit["source_auth_sha256_before"])
            fixture = temp_root / "fixture"
            fixture.mkdir()
            _run(("git", "init", "--quiet"), cwd=fixture, env=environment, timeout=20)
            _run(("git", "config", "user.email", "r8@example.invalid"), cwd=fixture, env=environment, timeout=20)
            _run(("git", "config", "user.name", "R8 Fixture"), cwd=fixture, env=environment, timeout=20)
            fixture_instructions = (
                "# R8 synthetic acceptance fixture\n\n"
                f"When the complete user prompt is exactly `{E1_CANARY}`, use only the powercontext MCP server. "
                f"First call search_topic_memory with scope_id `{E1_SCOPE_ID}`, query `{E1_CANARY}`, and limit 8. "
                "Copy the first hit's artifact object exactly into get_topic_memory with the same scope_id. "
                "Finally reply only with that exact family, artifact_id, and revision. Do not use shell tools.\n"
            )
            (fixture / "AGENTS.md").write_text(fixture_instructions, encoding="utf-8")
            _run(("git", "add", "AGENTS.md"), cwd=fixture, env=environment, timeout=20)
            _run(
                ("git", "commit", "--quiet", "-m", "test: configure synthetic R8 fixture"),
                cwd=fixture,
                env=environment,
                timeout=20,
            )

            generation_root = temp_root / "generation"
            generation_root.mkdir()
            generation_home, generation_auth_audit = _prepare_codex_home(generation_root)
            generation_fixture = generation_root / "fixture"
            generation_fixture.mkdir()
            _run(("git", "init", "--quiet"), cwd=generation_fixture, env=environment, timeout=20)
            generation_bridge = _RealCodexChatBridge(
                codex_home=generation_home,
                fixture=generation_fixture,
                environment=environment,
                model=generation_model,
                timeout=codex_timeout,
            )
            generation_server = start_loopback_server(generation_bridge.app())
            generation_header = {"Authorization": SecretStr("Bearer r8-real-codex-loopback")}

            settings = ServerSettings(
                auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{temp_root / 'runtime.db'}"),
                runtime=RuntimeConfig(
                    topic_memory_source_window_limit=1,
                    artifact_processing_worker_timeout_seconds=generation_timeout,
                ),
                inference=InferenceConfig(
                    generation_model="openai-chat:r8-real-codex-generation",
                    generation_base_url=AnyHttpUrl(f"{generation_server.base_url}/v1"),
                    generation_headers=generation_header,
                    generation_timeout_seconds=generation_timeout,
                    generation_max_requests=1,
                ),
                mcp=McpConfig(enabled=True),
                dashboard=DashboardConfig(
                    enabled=True,
                    scopes=[DashboardScopeConfig(scope_id=E1_SCOPE_ID, display_name="R8 Real Codex")],
                ),
            )
            app = create_server_app(
                settings=settings,
                scheduler_path=temp_root / "scheduler.db",
                middleware=(
                    Middleware(AccessTimelineMiddleware, timeline=timeline),
                    Middleware(PreparedContextAuditMiddleware, audit=prepared_audit),
                ),
            )
            server = start_loopback_server(app, startup_timeout=60)
            plugin = _install_current_plugin(
                codex_home=codex_home,
                environment=environment,
                timeout=codex_timeout,
            )
            installed_path = plugin.pop("installed_path")
            if not isinstance(installed_path, Path):
                raise ProductChainError("installed plugin path is invalid")
            _configure_installed_mcp(installed_path, base_url=server.base_url)
            codex_environment = {
                **environment,
                "POWERCONTEXT_CODEX_AUTHORIZATION": f"Bearer {token}",
                "POWERCONTEXT_CODEX_SCOPE_ID": E1_SCOPE_ID,
                "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "true",
                "POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE": "false",
                "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "5",
                "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "12",
            }
            first_prompt = (
                f"Synthetic acceptance input. The durable release sentinel is {E1_CANARY}. "
                "Remember only this fabricated project decision. Reply exactly ACK and do not use tools."
            )
            first_events, first_duration = _run_codex(
                codex_home=codex_home,
                fixture=fixture,
                environment=codex_environment,
                prompt=first_prompt,
                model=codex_model,
                timeout=codex_timeout,
            )
            first_timeline = timeline.snapshot()
            first_paths = [entry.get("path") for entry in first_timeline]
            if "/v1/context/prepare" not in first_paths or "/v1/sources/content" not in first_paths:
                observed_paths = sorted({str(path) for path in first_paths})
                raise ProductChainError(
                    f"real Codex UserPromptSubmit hook did not prepare and capture; paths={observed_paths}"
                )

            try:
                chain = asyncio.run(
                    exercise_http_mcp_prepared_web_chain(
                        base_url=server.base_url,
                        token=token,
                        scope_id=E1_SCOPE_ID,
                        query=E1_CANARY,
                        source_id=None,
                        source_content=None,
                        expected_detail_marker=None,
                        timeline=timeline,
                        search_timeout_seconds=generation_timeout,
                    )
                )
            except ProductChainError as exc:
                raise ProductChainError(
                    f"{exc}; redacted_worker_failures={failure_capture.failures[-3:]}; "
                    f"redacted_generation_calls={generation_bridge.redacted_calls()}"
                ) from exc
            if not chain.source_ref.startswith("content:codex-user-prompt:"):
                raise ProductChainError("generated Topic did not retain the first Codex hook SourceRef")

            second_prompt = E1_CANARY
            prior_prepared_observations = len(prepared_audit.for_query(second_prompt))
            second_events, second_duration = _run_codex(
                codex_home=codex_home,
                fixture=fixture,
                environment=codex_environment,
                prompt=second_prompt,
                model=codex_model,
                timeout=codex_timeout,
            )
            second_summary = _codex_summary(second_events)
            if second_summary["mcp_tools_in_observed_order"] != [
                "search_topic_memory",
                "get_topic_memory",
            ]:
                raise ProductChainError("real Codex did not perform MCP search followed by exact get")
            encoded_second_events = json.dumps(second_events, sort_keys=True)
            if chain.exact_ref.artifact_id not in encoded_second_events:
                raise ProductChainError("real Codex MCP events did not reuse the exact Topic artifact ID")
            prepared_observations = prepared_audit.for_query(second_prompt)[prior_prepared_observations:]
            expected_ref = chain.exact_ref.as_dict()
            if not any(
                isinstance(observation.get("topic_refs"), list)
                and expected_ref in cast(list[object], observation["topic_refs"])
                and observation.get("full_detail_absent") is True
                for observation in prepared_observations
            ):
                raise ProductChainError(
                    "the second real Codex prompt did not receive the same compact Topic ref from its hook"
                )
            first_jsonl = _write_redacted_codex_jsonl(directory / "first-codex-redacted.jsonl", first_events)
            second_jsonl = _write_redacted_codex_jsonl(
                directory / "second-codex-redacted.jsonl",
                second_events,
                exact_ref=chain.exact_ref,
            )

            browser = _capture_browser_evidence(
                directory=directory,
                base_url=server.base_url,
                token=token,
                artifact_ref=chain.exact_ref.display(),
                source_ref=chain.source_ref,
                environment=environment,
            )
            server.stop()
            port_closed = server.port_is_closed()
            server = None
            if not port_closed:
                raise ProductChainError("E1 PowerContext port remained open after shutdown")
            generation_server.stop()
            generation_port_closed = generation_server.port_is_closed()
            generation_server = None
            if not generation_port_closed:
                raise ProductChainError("E1 generation bridge port remained open after shutdown")
            if source_auth is None or _sha256_file(source_auth) != source_auth_digest:
                raise ProductChainError("E1 changed the operator's original Codex auth file")

            result = {
                "schema": "powercontext.topic-memory-r8.e1.v1",
                "status": "PASS",
                "environment": {
                    "codex_cli": _version(("codex", "--version"), environment=environment),
                    "codex_model": codex_model,
                    "plugin": plugin,
                    "hooks_enabled": True,
                    "mcp_enabled": True,
                    "generation_provider": "real Codex provider through a content-free loopback protocol bridge",
                    "generation_model": generation_model,
                    "generation_calls": generation_bridge.redacted_calls(),
                    "database": "isolated temporary file SQLite",
                    "server": "loopback random port",
                    "authentication": "one-time bearer; value not retained",
                    "fixture": "isolated temporary Git repository",
                },
                "first_codex": {
                    "prompt_sha256": digest_text(first_prompt),
                    "prompt_retained": False,
                    "duration_seconds": first_duration,
                    **_codex_summary(first_events),
                    "hook_paths": ["/v1/context/prepare", "/v1/sources/content"],
                    "redacted_jsonl": first_jsonl,
                },
                "chain": chain.as_dict(),
                "second_codex": {
                    "prompt_sha256": digest_text(second_prompt),
                    "prompt_retained": False,
                    "duration_seconds": second_duration,
                    **second_summary,
                    "exact_ref": chain.exact_ref.as_dict(),
                    "redacted_jsonl": second_jsonl,
                    "prepared_context": {
                        "observations": list(prepared_observations),
                        "same_exact_ref": True,
                        "full_detail_absent": True,
                    },
                },
                "browser": browser,
                "auth_audit": auth_audit,
                "generation_auth_audit": generation_auth_audit,
                "redaction": {
                    "prompt_content_recorded": False,
                    "full_model_output_recorded": False,
                    "credentials_recorded": False,
                    "provider_body_recorded": False,
                    "screenshots_mask_generated_content": True,
                },
                "cleanup": {
                    "powercontext_port_closed": True,
                    "generation_bridge_port_closed": True,
                    "original_auth_unchanged": True,
                },
            }
        result["cleanup"]["temporary_tree_removed"] = not Path(temp_root_value).exists()  # type: ignore[index]
        if result["cleanup"]["temporary_tree_removed"] is not True:  # type: ignore[index]
            raise ProductChainError("E1 temporary tree was not removed")
        _write_json(directory / "e1-report.json", result)
        return result
    finally:
        worker_logger.removeHandler(failure_capture)
        if server is not None:
            server.stop()
        if generation_server is not None:
            generation_server.stop()
        if temporary_openai_key:
            os.environ.pop("OPENAI_API_KEY", None)


def _version(command: Sequence[str], *, environment: Mapping[str, str]) -> str:
    completed = _run(command, cwd=PROJECT_ROOT, env=environment, timeout=20)
    return completed.stdout.strip()


def _unavailable(layer: str, gap: str, *, checks: Sequence[str] = ()) -> dict[str, object]:
    return {
        "schema": f"powercontext.topic-memory-r8.{layer.casefold()}.v1",
        "status": "UNAVAILABLE",
        "gap": gap,
        "safe_checks_completed": list(checks),
        "passed": False,
    }


def _environment_layer_statuses() -> dict[str, dict[str, object]]:
    embedding_inputs = (
        "POWERCONTEXT_R8_EMBEDDING_MODEL",
        "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID",
        "POWERCONTEXT_R8_EMBEDDING_DIMENSION",
    )
    missing_embedding = [name for name in embedding_inputs if not os.environ.get(name)]
    e2_gap = (
        "missing explicit real embedding model/profile/dimension: " + ", ".join(missing_embedding)
        if missing_embedding
        else "real embedding execution is not selected by this E1-only harness invocation"
    )
    oceanbase_url = os.environ.get("POWERCONTEXT_R8_OCEANBASE_URL")
    seekdb_enabled = os.environ.get("POWERCONTEXT_R8_SEEKDB_ENABLED") == "1"
    return {
        "E2": _unavailable(
            "E2",
            e2_gap,
            checks=(
                "SQLite vector support is covered hermetically in E0",
                "no fake embedding result was promoted to real-provider PASS",
            ),
        ),
        "E3": _unavailable(
            "E3",
            "dedicated OceanBase URL/schema was not provided"
            if not oceanbase_url
            else "dedicated OceanBase environment was detected but E3 was not requested in this invocation",
            checks=("no shared/default OceanBase schema was touched",),
        ),
        "E4": _unavailable(
            "E4",
            "SeekDB runtime/dependency was not explicitly enabled"
            if not seekdb_enabled
            else "SeekDB environment was detected but E4 was not requested in this invocation",
            checks=("no implicit local SeekDB state was touched",),
        ),
    }


def main() -> int:
    args = _parse_args()
    requested = {value.strip().casefold() for value in args.layers.split(",") if value.strip()}
    requested.add("e0")
    args.output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "schema": "powercontext.topic-memory-r8.acceptance.v1",
        "baseline": "d5d7fa2acaa0e72a5122140b58856e18172d2e8d",
        "head": _version(("git", "rev-parse", "HEAD"), environment=os.environ),
        "requested_layers": sorted(requested),
        "layers": {},
        "limits": {
            "codex_timeout_seconds": args.codex_timeout,
            "generation_timeout_seconds": args.generation_timeout,
            "generation_max_requests_per_inference": 1,
            "polling_is_deadline_bounded": True,
        },
    }
    layers = report["layers"]
    assert isinstance(layers, dict)
    try:
        layers["E0"] = run_e0(args.output / "e0")
        if "e1" in requested:
            layers["E1"] = run_e1(
                args.output / "e1",
                codex_model=args.codex_model,
                generation_model=args.generation_model,
                codex_timeout=args.codex_timeout,
                generation_timeout=args.generation_timeout,
            )
        else:
            layers["E1"] = _unavailable("E1", "not requested in this invocation")
        environment_layers = _environment_layer_statuses()
        for name in ("E2", "E3", "E4"):
            layers[name] = environment_layers[name]
        layer_statuses = [value.get("status") for value in layers.values() if isinstance(value, dict)]
        if "FAIL" in layer_statuses:
            report["status"] = "FAIL"
        elif "UNAVAILABLE" in layer_statuses:
            report["status"] = "PARTIAL"
        else:
            report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        _write_json(args.output / "r8-report.json", report)
        raise
    _write_json(args.output / "r8-report.json", report)
    print(json.dumps({"status": report["status"], "report": str(args.output / "r8-report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
