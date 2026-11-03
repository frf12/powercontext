#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0.
"""Run isolated, paired Gateway code-injection trials with the official SWE-bench Pro grader.

Run with the integrated PowerContext virtualenv. Each arm receives a new task
container, Core database, code cache, Gateway, and Codex home. Only code injection
differs. This experimental runner does not install any agent plugins.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import ipaddress
import json
import os
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE / "evaluation/coding/swebench_pro/src"))

PILOT_INDICES = [0, 463, 101, 457, 214, 559, 498, 402, 357, 1, 472, 450]
MODEL = "gpt-5.6-sol"
REASONING = "medium"
IMAGE_SEMAPHORE = threading.Semaphore(2)
INDEX_SEMAPHORE = threading.Semaphore(2)
GRADE_SEMAPHORE = threading.Semaphore(2)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = 600,
    log: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True, timeout=timeout, check=False)  # noqa: S603 - operator-controlled argument vectors; no shell
    if log:
        log.write_bytes(result.stdout + b"\nSTDERR:\n" + result.stderr)
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} exited {result.returncode}; see private arm logs")  # noqa: TRY003 - script diagnostic omits private command output
    return result


def json_request(url: str, body: Any = None, token: str = "") -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)  # noqa: S310 - caller constructs the local Core HTTP endpoint
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
        return json.load(response)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def start_service(argv: list[str], env: dict[str, str], directory: Path, name: str) -> subprocess.Popen:
    with (directory / f"{name}.log").open("ab") as log:
        return subprocess.Popen(argv, cwd=directory, env=env, stdout=log, stderr=log, start_new_session=True)  # noqa: S603 - configured service executable and argument vector; no shell


def stop_service(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def wait_ready(url: str, process: subprocess.Popen, token: str = "") -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("service exited before readiness; see private service log")  # noqa: TRY003 - script diagnostic points to private evidence
        try:
            json_request(url, token=token)
            return  # noqa: TRY300 - retain the measured readiness loop's control flow
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.5)
    raise TimeoutError("service readiness timed out")  # noqa: TRY003 - bounded startup diagnostic


def write_env(path: Path, env: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(env.items())))
    path.chmod(0o600)


def load_env(path: Path) -> dict[str, str]:
    from powercontext.cli.env_file import read_environment_file

    return read_environment_file(path)


def provider_usage(raw_body: object) -> dict[str, int] | None:
    """Extract only documented numeric usage; omitted measurements remain unknown."""
    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else None
    except ValueError:
        return None
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return None
    values = {key: usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")}
    for section, key in (("input_tokens_details", "cached_tokens"), ("output_tokens_details", "reasoning_tokens")):
        details = usage.get(section)
        if isinstance(details, dict):
            values[key] = details.get(key)
    return {key: value for key, value in values.items() if type(value) is int and value >= 0} or None


def valid_treatment(evidence: dict[str, Any], arm: str) -> bool:
    return (
        evidence.get("allowed_sends", 0) > 0
        and evidence.get("allowed_releases", 0) > 0
        and evidence.get("prepare_calls", 0) > 0
        and evidence.get("all_prepares_memory_disabled") is True
        and (
            evidence.get("allowed_code_sends", 0) > 0
            if arm == "on"
            else evidence.get("declared_prepared_blocks", 0) == 0
        )
    )


class CoreEvidenceTap:
    """Transparent local Core hop retaining counters, never model payloads or bindings."""

    def __init__(self, upstream: str, output: Path):
        self.upstream = upstream
        self.output = output
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.code_bindings: set[str] = set()
        tap = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.forward()

            def do_POST(self) -> None:
                self.forward()

            def forward(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in {"host", "connection", "content-length", "transfer-encoding"}
                }
                request = urllib.request.Request(  # noqa: S310 - forward only to the runner's local Core HTTP endpoint
                    tap.upstream + self.path,
                    data=body if self.command == "POST" else None,
                    headers=headers,
                    method=self.command,
                )
                try:
                    response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=120)
                except urllib.error.HTTPError as error:
                    response = error
                except (urllib.error.URLError, TimeoutError):
                    self.send_error(502, "Core unavailable")
                    return
                with response:
                    payload = response.read()
                    status = response.status
                    content_type = response.headers.get("Content-Type", "application/json")
                tap.record(self.path, body, payload, status)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError):
                    self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def record(self, path: str, body: bytes, payload: bytes, status: int) -> None:
        if path not in {"/v1/context/prepare", "/v1/context/check"}:
            return
        try:
            request, response = json.loads(body), json.loads(payload)
        except (ValueError, TypeError):
            return
        with self.lock:
            event: dict[str, Any] = {"operation": path.rsplit("/", 1)[1], "http_status": status}
            if path.endswith("/prepare"):
                context = response.get("context", response)
                binding = (response.get("reuse") or {}).get("binding")
                code_only = request.get("include_code") is True and request.get("assembly", {}).get("sections") == []
                ready = context.get("status") == "ready" and context.get("content_bytes", 0) > 0
                if code_only and ready and isinstance(binding, str):
                    self.code_bindings.add(hashlib.sha256(binding.encode()).hexdigest())
                event.update(
                    code_only=code_only,
                    ready=ready,
                    content_bytes=context.get("content_bytes", 0),
                    has_reuse=isinstance(binding, str),
                    historical_sections=request.get("assembly", {}).get("sections"),
                )
            else:
                declarations = request.get("prepared_contexts", [])
                code_declarations = sum(
                    hashlib.sha256(item.get("binding", "").encode()).hexdigest() in self.code_bindings
                    for item in declarations
                )
                if request.get("phase") == "before_release":
                    event["provider_usage"] = provider_usage(request.get("payload", {}).get("body"))
                event.update(
                    phase=request.get("phase"),
                    decision=response.get("decision"),
                    reason_codes=response.get("reason_codes", []),
                    prepared_count=len(declarations),
                    code_prepared_count=code_declarations,
                )
            self.events.append(event)
            write_json(self.output, self.events)

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return self.summarize_events(self.events)

    @staticmethod
    def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
        releases = [event for event in events if event.get("phase") == "before_release"]
        totals: dict[str, int] = {}
        for event in releases:
            for key, value in (event.get("provider_usage") or {}).items():
                totals[key] = totals.get(key, 0) + value
        return {
            "provider_usage_attempts": [event.get("provider_usage") for event in releases],
            "provider_usage_totals_known": totals or None,
            "provider_usage_unknown_attempts": sum(event.get("provider_usage") is None for event in releases),
            "allowed_sends_without_release_check": max(
                0,
                sum(event.get("phase") == "before_send" and event.get("decision") == "allow" for event in events)
                - len(releases),
            ),
            "all_prepares_memory_disabled": all(
                event.get("historical_sections") == [] for event in events if event["operation"] == "prepare"
            ),
            "prepare_calls": sum(event["operation"] == "prepare" for event in events),
            "code_ready_prepares": sum(
                event.get("code_only", False) and event.get("ready", False) and event.get("has_reuse", False)
                for event in events
            ),
            "allowed_code_sends": sum(
                event.get("phase") == "before_send"
                and event.get("decision") == "allow"
                and event.get("code_prepared_count", 0) > 0
                for event in events
            ),
            "allowed_sends": sum(
                event.get("phase") == "before_send" and event.get("decision") == "allow" for event in events
            ),
            "allowed_releases": sum(
                event.get("phase") == "before_release" and event.get("decision") == "allow" for event in events
            ),
            "declared_prepared_blocks": sum(event.get("prepared_count", 0) for event in events),
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class Admission:
    """Quota pause and adjustable active-task limit; already running turns continue."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.active = 0
        self.lock = threading.Lock()

    def wait_unpaused(self) -> None:
        while True:
            try:
                age = time.time() - self.args.quota_file.stat().st_mtime
                quota = json.loads(self.args.quota_file.read_text())
                fresh = isinstance(quota, dict) and bool(quota) and 0 <= age <= 90
            except (OSError, ValueError):
                fresh = False
            if fresh and not self.args.pause_file.exists():
                return
            time.sleep(2)

    @contextlib.contextmanager
    def slot(self):
        while True:
            self.wait_unpaused()
            limit = self.args.concurrency
            if self.args.limit_file.exists():
                try:  # noqa: SIM105 - retain the measured admission loop's fallback control flow
                    limit = max(1, min(self.args.max_concurrency, int(self.args.limit_file.read_text().strip())))
                except (OSError, ValueError):
                    pass
            with self.lock:
                if self.active < limit:
                    self.active += 1
                    break
            time.sleep(1)
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1


def ensure_image(args: argparse.Namespace, image: str, directory: Path) -> None:
    with IMAGE_SEMAPHORE:
        present = subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False)  # noqa: S603, S607 - operator-provided Docker PATH; pinned image passed without a shell
        if present.returncode == 0:
            return
        local_image = args.registry.rstrip("/") + "/" + image
        command(["docker", "pull", local_image], cwd=directory, timeout=900, log=directory / "image-pull.log")
        command(["docker", "tag", local_image, image], cwd=directory)


def initialize_workspace(instance: Any, directory: Path, name: str) -> Path:
    from powercontext_eval_swebench_pro.powercontext_sut import trim_future_history
    from powercontext_eval_swebench_pro.process import ProcessRunner

    workspace = directory / "workspace"
    workspace.mkdir()
    with IMAGE_SEMAPHORE:
        # The official task image contains its checked-out base tree and installed dependencies.
        command(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name + "-init",
                "--network",
                "none",
                "--mount",
                f"type=bind,src={workspace},dst=/workspace",
                "--entrypoint",
                "/bin/cp",
                instance.task_image,
                "-a",
                "/app/.",
                "/workspace",
            ],
            cwd=directory,
            timeout=600,
            log=directory / "workspace-init.log",
        )
    # BusyBox task images support -a but not GNU's --no-preserve=ownership.
    # Normalize only this new private tree; never change global Git safe.directory.
    command(["chown", "-R", "--no-dereference", f"{os.geteuid()}:{os.getegid()}", str(workspace)], cwd=directory)
    head = command(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.decode().strip()
    if head != instance.base_commit:
        raise RuntimeError("task image HEAD differs from the pinned dataset base commit")  # noqa: TRY003 - script reports the violated dataset invariant
    trim_future_history(ProcessRunner(), workspace)
    return workspace


def core_environment(args: argparse.Namespace, directory: Path, code: dict[str, Any]) -> dict[str, str]:
    env = load_env(args.core_env)
    # Never allow inherited nested overrides to point an arm at another database/code tree.
    for key in list(env):
        if key.startswith(("POWERCONTEXT_SERVER_DATABASE_", "POWERCONTEXT_SERVER_CODE_", "POWERCONTEXT_SERVER_HTTP_")):
            del env[key]
    env.update({
        "POWERCONTEXT_HOME": str(directory / "pc-home"),
        "POWERCONTEXT_SERVER_DATABASE": json.dumps({
            "kind": "sqlite",
            "url": f"sqlite+aiosqlite:///{directory / 'core.db'}",
        }),
        "POWERCONTEXT_SERVER_CODE": json.dumps(code),
    })
    return env


def codex_config(host: str, port: int) -> str:
    return f'''model = "{MODEL}"
model_reasoning_effort = "{REASONING}"
model_provider = "gateway_benchmark"
web_search = "disabled"
[memories]
generate_memories = false
use_memories = false
[features]
plugins = false
shell_snapshot = false
[model_providers.gateway_benchmark]
name = "Isolated code-only Gateway benchmark"
base_url = "http://{host}:{port}/gateway/v1"
env_key = "PC_BENCH_GATEWAY_KEY"
wire_api = "responses"
request_max_retries = 3
stream_max_retries = 3
'''


def parse_codex(path: Path) -> dict[str, Any]:
    usage: dict[str, int] = {}
    turns = 0
    terminal_event = None
    error_events = 0
    events = 0
    with path.open() as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            events += 1
            if event.get("type") == "turn.completed":
                turns += 1
                for key, value in (event.get("usage") or {}).items():
                    if isinstance(value, int):
                        usage[key] = usage.get(key, 0) + value
            if event.get("type") in {"turn.failed", "turn.completed"}:
                terminal_event = event["type"]
            error_events += event.get("type") == "error"
    return {
        "usage": usage or None,
        "completed_turns": turns,
        "failed_event": terminal_event == "turn.failed",
        "terminal_event": terminal_event,
        "error_events": error_events,
        "recovered_error_events": error_events if terminal_event == "turn.completed" else 0,
        "event_count": events,
    }


def grade_arm(args: argparse.Namespace, instance: Any, arm: str, directory: Path, outcome: dict[str, Any]) -> None:
    from powercontext_eval_swebench_pro.evaluator import OfficialEvaluator
    from powercontext_eval_swebench_pro.gold_overrides import select_gold_validation
    from powercontext_eval_swebench_pro.prediction import encode_predictions
    from powercontext_eval_swebench_pro.process import ProcessRunner
    from powercontext_eval_swebench_pro.runner import _evaluator_test_requirements

    patch = (directory / "patch.diff").read_text()
    outcome["grading_attempts"] = outcome.get("grading_attempts", 0) + 1
    raw = directory / "sample.jsonl"
    required_fail, required_pass = _evaluator_test_requirements(instance, evaluation_year=datetime.now(UTC).year)
    evaluator_row = instance.official_row()
    evaluator_row.update(
        FAIL_TO_PASS=list(required_fail),
        PASS_TO_PASS=list(required_pass),
        fail_to_pass=json.dumps(required_fail),
        pass_to_pass=json.dumps(required_pass),
    )
    selection = select_gold_validation(instance.instance_id, instance.patch, instance.selected_test_files_to_run)
    if selection.evaluator_selected_test_files_to_run is not None:
        evaluator_row["selected_test_files_to_run"] = selection.evaluator_selected_test_files_to_run
    raw.write_text(json.dumps(evaluator_row, ensure_ascii=False) + "\n")
    prediction = directory / "prediction.json"
    prediction.write_text(encode_predictions(instance.instance_id, patch, f"code-{arm}"))
    with GRADE_SEMAPHORE:
        evaluation_started = time.monotonic()
        evaluation = OfficialEvaluator(ProcessRunner(), python_executable=str(args.harness_python)).evaluate(
            harness_root=args.harness_root,
            raw_sample_path=raw,
            prediction_path=prediction,
            output_dir=directory / "official",
            instance_id=instance.instance_id,
            required_fail_to_pass=required_fail,
            required_pass_to_pass=required_pass,
        )
        outcome["evaluation_seconds"] = time.monotonic() - evaluation_started
    outcome["resolved"] = evaluation.resolved
    outcome["fail_to_pass"] = dataclasses.asdict(evaluation.fail_to_pass)
    outcome["pass_to_pass"] = dataclasses.asdict(evaluation.pass_to_pass)
    valid = valid_treatment(outcome.get("code_treatment_evidence", {}), arm)
    outcome["code_treatment_valid"] = valid
    outcome["status"] = "completed" if valid else "invalid_code_treatment"
    outcome.pop("phase", None)
    outcome.pop("error_type", None)


def recover_completed_codex(
    args: argparse.Namespace, instance: Any, arm: str, directory: Path, outcome: dict[str, Any]
) -> bool:
    """Handle a saved successful CLI turn misclassified during retries; never call a model.

    Return True when recovery handled the attempt. Grading success or failure is
    reflected in the updated outcome.json, so a grader failure can be resumed.
    """
    if outcome.get("phase") != "codex" or outcome.get("codex_exit_code") != 0:
        return False
    event_file = directory / "codex.jsonl"
    if not event_file.is_file() or not (directory / "workspace/.git").is_dir():
        return False
    parsed = parse_codex(event_file)
    if parsed["terminal_event"] != "turn.completed" or not parsed["completed_turns"]:
        return False
    outcome.update(parsed)
    workspace = directory / "workspace"
    command(["git", "add", "-N", "."], cwd=workspace)
    patch = command(["git", "diff", "--binary", "HEAD"], cwd=workspace).stdout.decode()
    (directory / "patch.diff").write_text(patch)
    outcome.update(
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        patch_bytes=len(patch.encode()),
        recovered_completed_codex=True,
        status="grading",
        phase="official_evaluation",
    )
    evidence_file = directory / "core-evidence.json"
    if evidence_file.is_file():
        outcome["code_treatment_evidence"] = CoreEvidenceTap.summarize_events(json.loads(evidence_file.read_text()))
    write_json(directory / "outcome.json", outcome)
    try:
        grade_arm(args, instance, arm, directory, outcome)
    except Exception as error:
        outcome.update(status="infrastructure_error", phase="official_evaluation", error_type=type(error).__name__)
        (directory / "error.txt").write_text(str(error) + "\n")
    write_json(directory / "outcome.json", outcome)
    return True


def run_arm(args: argparse.Namespace, instance: Any, arm: str, directory: Path, admission: Admission) -> dict[str, Any]:
    directory.mkdir(parents=True, mode=0o700)
    name = "pc-code-bench-" + hashlib.sha256(str(directory).encode()).hexdigest()[:16]
    started = time.monotonic()
    phase = "prepare"
    core = gateway = None
    tap = None
    container_created = False
    outcome: dict[str, Any] = {
        "instance_id": instance.instance_id,
        "arm": arm,
        "model": MODEL,
        "reasoning_effort": REASONING,
        "memory_enabled": False,
        "code_enabled": arm == "on",
    }
    try:
        ensure_image(args, instance.task_image, directory)
        workspace = initialize_workspace(instance, directory, name)
        core_port, gateway_port = free_port(), free_port()
        core_url = f"http://127.0.0.1:{core_port}"
        core_file = directory / "core.env"
        code: dict[str, Any] = {"enabled": True, "repositories": {}, "cache_dir": str(directory / "code-cache")}
        env = core_environment(args, directory, code)
        write_env(core_file, env)
        service_env = {k: v for k, v in os.environ.items() if not k.startswith("POWERCONTEXT_SERVER_")}
        core_argv = [
            str(args.core_binary),
            "server",
            "run",
            "--env-file",
            str(core_file),
            "--host",
            "127.0.0.1",
            "--port",
            str(core_port),
            "--role",
            "all",
        ]
        core = start_service(core_argv, service_env, directory, "core")
        core_token = env.get("PC_GATEWAY_CORE_TOKEN", "")
        wait_ready(core_url + "/health/ready", core, core_token)
        scope = json_request(
            core_url + "/v1/scopes",
            {
                "title": f"Code-only benchmark {instance.instance_id[-50:]} {arm}",
                "summary": "Isolated SWE-bench Pro arm; no historical memory or conversation capture.",
                "idempotency_key": name,
            },
            core_token,
        )["scope_id"]
        outcome["scope_id"] = scope
        stop_service(core)
        code["repositories"] = {scope: {"root": str(workspace), "source_roots": ["src", "."]}}
        env = core_environment(args, directory, code)
        write_env(core_file, env)
        if arm == "on":
            phase = "code_index"
            index_started = time.monotonic()
            with INDEX_SEMAPHORE:
                result = command(
                    [str(args.core_binary), "code", "index", "--scope", scope, "--env-file", str(core_file)],
                    cwd=directory,
                    env=service_env,
                    timeout=900,
                    log=directory / "code-index.log",
                )
            outcome["index_seconds"] = time.monotonic() - index_started
            outcome["index"] = json.loads(result.stdout)
        core = start_service(core_argv, service_env, directory, "core")
        wait_ready(core_url + "/health/ready", core, core_token)
        tap = CoreEvidenceTap(core_url, directory / "core-evidence.json")
        gateway_env = {**service_env, **load_env(args.gateway_env)}
        gateway_key = secrets.token_urlsafe(24)
        gateway_env.update({
            "PC_GATEWAY_LISTEN": f"{args.gateway_host}:{gateway_port}",
            "PC_GATEWAY_CORE_BASE": tap.url,
            "PC_GATEWAY_DEFAULT_SCOPE": scope,
            "PC_GATEWAY_UPSTREAM_PROFILE": args.profile,
            "PC_GATEWAY_API_KEY": gateway_key,
            "PC_GATEWAY_INJECTION": "on",
            "PC_GATEWAY_INJECTION_RESPONSES": "on",
            "PC_GATEWAY_INJECTION_MEMORY": "off",
            "PC_GATEWAY_INJECTION_CODE": arm,
            "PC_GATEWAY_SEDIMENT": "off",
            "PC_GATEWAY_DEGRADE": "pause",
            "PC_GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS": "128000",
            "PC_GATEWAY_MAX_CONCURRENT": "8",
        })
        gateway_env.pop("PC_GATEWAY_CORE_UDS", None)
        gateway_env.pop("PC_GATEWAY_STEERING_FILE", None)
        gateway = start_service([str(args.gateway_binary)], gateway_env, directory, "gateway")
        wait_ready(f"http://{args.gateway_host}:{gateway_port}/healthz", gateway)
        codex_home = directory / "codex-home"
        codex_home.mkdir(mode=0o700)
        (codex_home / "config.toml").write_text(codex_config(args.gateway_host, gateway_port))
        private_env = directory / "container.env"
        private_env.write_text("PC_BENCH_GATEWAY_KEY=" + gateway_key + "\n")
        private_env.chmod(0o600)
        docker_argv = [
            "docker",
            "run",
            "-d",
            "--init",
            "--name",
            name,
            "--label",
            "powercontext.benchmark=code-reuse",
            "--network",
            args.docker_network,
            "--cpus",
            args.cpus,
            "--memory",
            args.memory,
            "--pids-limit",
            "512",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
            "--mount",
            f"type=bind,src={workspace},dst=/app",
            "--mount",
            f"type=bind,src={codex_home},dst=/root/.codex",
            "--mount",
            f"type=bind,src={args.codex_binary.resolve()},dst=/tools/codex,readonly",
            "--env-file",
            str(private_env),
            "--env",
            "CODEX_HOME=/root/.codex",
            "--workdir",
            "/workspace",
            "--entrypoint",
            "/bin/sleep",
            instance.task_image,
            "infinity",
        ]
        command(docker_argv, cwd=directory)
        container_created = True
        version = command(["docker", "exec", name, "/tools/codex", "--version"], cwd=directory).stdout.decode().strip()
        if "0.145.0" not in version:
            raise RuntimeError("unexpected Codex version")  # noqa: TRY003, TRY301 - preserve the measured arm failure boundary
        outcome["codex_version"] = version
        admission.wait_unpaused()
        phase = "codex"
        inference_started = time.monotonic()
        codex_argv = [
            "docker",
            "exec",
            "-i",
            name,
            "/tools/codex",
            "exec",
            "--ephemeral",
            "--ignore-rules",
            "--json",
            "--disable",
            "plugins",
            "--disable",
            "shell_snapshot",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--model",
            MODEL,
            "-c",
            f'model_reasoning_effort="{REASONING}"',
            "-C",
            "/workspace",
            "-",
        ]
        (directory / "prompt.txt").write_text(instance.codex_prompt())
        with (directory / "codex.jsonl").open("wb") as stdout, (directory / "codex.stderr.log").open("wb") as stderr:
            result = subprocess.run(  # noqa: S603 - fixed Docker/Codex argument vector; task prompt is a file
                codex_argv,
                cwd=directory,
                input=instance.codex_prompt().encode(),
                stdout=stdout,
                stderr=stderr,
                timeout=args.task_timeout,
                check=False,
            )
        outcome["agent_seconds"] = time.monotonic() - inference_started
        outcome.update(parse_codex(directory / "codex.jsonl"))
        outcome["codex_exit_code"] = result.returncode
        if result.returncode or outcome["failed_event"] or not outcome["completed_turns"]:
            raise RuntimeError("Codex turn did not complete; see private JSONL and stderr")  # noqa: TRY003, TRY301 - preserve the measured arm failure boundary
        command(["git", "add", "-N", "."], cwd=workspace)
        patch = command(["git", "diff", "--binary", "HEAD"], cwd=workspace).stdout.decode()
        (directory / "patch.diff").write_text(patch)
        outcome["patch_sha256"] = hashlib.sha256(patch.encode()).hexdigest()
        outcome["patch_bytes"] = len(patch.encode())
        stop_service(gateway)
        gateway = None
        stop_service(core)
        core = None
        command(["docker", "rm", "-f", name], cwd=directory)
        container_created = False
        phase = "official_evaluation"
        outcome["code_treatment_evidence"] = tap.summary()
        outcome.update(status="grading", phase=phase)
        write_json(directory / "outcome.json", outcome)
        grade_arm(args, instance, arm, directory, outcome)
    except Exception as error:
        outcome.update(status="infrastructure_error", phase=phase, error_type=type(error).__name__)
        # Exception messages from subprocesses may contain private configuration; do not publish them.
        (directory / "error.txt").write_text(str(error) + "\n")
    finally:
        if container_created:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60, check=False)  # noqa: S603, S607 - trusted Docker PATH and runner-generated container name; no shell
        stop_service(gateway)
        stop_service(core)
        if tap is not None:
            outcome["code_treatment_evidence"] = tap.summary()
            tap.close()
        outcome["elapsed_seconds"] = time.monotonic() - started
        write_json(directory / "outcome.json", outcome)
    return outcome


def run_pair(args: argparse.Namespace, catalog: Any, source_index: int, admission: Admission) -> dict[str, Any]:  # noqa: C901 - keep the measured pair selection and bounded recovery together
    instance = catalog.require(catalog.instance_ids[source_index])
    directory = args.output / f"case-{source_index:04d}"
    directory.mkdir(exist_ok=True, mode=0o700)
    pair = {"source_index": source_index, "instance_id": instance.instance_id, "arms": {}}
    with admission.slot():
        # Alternate order across the selected cohort to avoid always giving one arm the earlier slot.
        order = ["off", "on"] if args.indices.index(source_index) % 2 == 0 else ["on", "off"]
        pair["order"] = order
        for arm in order:
            admission.wait_unpaused()
            for attempt in range(1, args.attempts + 1):
                arm_dir = directory / f"{arm}-{attempt}"
                if arm_dir.exists():
                    saved = arm_dir / "outcome.json"
                    outcome = (
                        json.loads(saved.read_text())
                        if saved.exists()
                        else {
                            "instance_id": instance.instance_id,
                            "arm": arm,
                            "model": MODEL,
                            "reasoning_effort": REASONING,
                            "memory_enabled": False,
                            "code_enabled": arm == "on",
                        }
                    )
                    if outcome.get("status") in {"completed", "invalid_code_treatment"}:
                        break
                    if recover_completed_codex(args, instance, arm, arm_dir, outcome):
                        break
                    if not outcome.get("status"):
                        outcome.update(status="interrupted", phase="interrupted")
                        evidence_file = arm_dir / "core-evidence.json"
                        if evidence_file.is_file():
                            outcome["code_treatment_evidence"] = CoreEvidenceTap.summarize_events(
                                json.loads(evidence_file.read_text())
                            )
                        write_json(saved, outcome)
                    if (arm_dir / "patch.diff").is_file():
                        try:
                            grade_arm(args, instance, arm, arm_dir, outcome)
                        except Exception as error:
                            outcome.update(
                                status="infrastructure_error",
                                phase="official_evaluation",
                                error_type=type(error).__name__,
                            )
                            (arm_dir / "error.txt").write_text(str(error) + "\n")
                        write_json(saved, outcome)
                        break
                    continue
                outcome = run_arm(args, instance, arm, arm_dir, admission)
                if (
                    outcome["status"] in {"completed", "invalid_code_treatment"}
                    or outcome.get("phase") == "official_evaluation"
                ):
                    break
                admission.wait_unpaused()
            pair["arms"][arm] = outcome
            write_json(directory / "pair.json", pair)
            print(
                json.dumps({
                    "source_index": source_index,
                    "arm": arm,
                    "status": outcome["status"],
                    "resolved": outcome.get("resolved"),
                }),
                flush=True,
            )
    return pair


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--dataset", type=Path, required=True)
    cli.add_argument("--indices", type=lambda value: [int(item) for item in value.split(",")], default=PILOT_INDICES)
    cli.add_argument("--core-binary", type=Path, required=True)
    cli.add_argument("--core-env", type=Path, required=True)
    cli.add_argument("--gateway-binary", type=Path, required=True)
    cli.add_argument("--gateway-env", type=Path, required=True)
    cli.add_argument("--profile", default="codex-benchmark")
    cli.add_argument("--codex-binary", type=Path, default=Path("/data/powercontext-eval/bin/codex"))
    cli.add_argument("--harness-root", type=Path, default=Path("/data/powercontext-eval/cache/swebench-pro.git"))
    cli.add_argument(
        "--harness-python", type=Path, default=Path("/data/powercontext-eval/venvs/swebench-pro-ca10a60/bin/python")
    )
    cli.add_argument("--registry", default="127.0.0.1:5000")
    cli.add_argument("--docker-network", default="powercontext-codegraph-bench")
    cli.add_argument("--gateway-host", type=lambda value: str(ipaddress.ip_address(value)), default="172.31.240.1")
    cli.add_argument("--concurrency", type=int, default=8)
    cli.add_argument("--max-concurrency", type=int, default=16)
    cli.add_argument("--pause-file", type=Path)
    cli.add_argument("--quota-file", type=Path, help="Required guard heartbeat JSON; admission waits if older than 90s")
    cli.add_argument("--limit-file", type=Path)
    cli.add_argument("--attempts", type=int, default=2)
    cli.add_argument("--task-timeout", type=int, default=2400)
    cli.add_argument("--cpus", default="2")
    cli.add_argument("--memory", default="6g")
    cli.add_argument(
        "--plan-only", action="store_true", help="Validate catalog and emit a non-secret run manifest only"
    )
    return cli


def main() -> None:
    args = parser().parse_args()
    from powercontext_eval_swebench_pro.catalog import SweBenchProCatalog

    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.pause_file = args.pause_file or args.core_env.resolve().parent / "PAUSE"
    args.limit_file = args.limit_file or args.output / "CONCURRENCY"
    args.quota_file = args.quota_file or args.pause_file.parent / "quota.json"
    catalog = SweBenchProCatalog.load(args.dataset)
    if len(set(args.indices)) != len(args.indices) or any(
        index < 0 or index >= len(catalog.instance_ids) for index in args.indices
    ):
        raise ValueError("indices must be unique source indices in the pinned dataset")  # noqa: TRY003 - operator-facing argument validation
    if not 1 <= args.concurrency <= args.max_concurrency <= 32 or not 1 <= args.attempts <= 3:
        raise ValueError("invalid concurrency or attempt budget")  # noqa: TRY003 - operator-facing argument validation
    if (SOURCE / "SOURCE_REVISION").is_file():
        revision = (SOURCE / "SOURCE_REVISION").read_text().strip()
        diff = (SOURCE / "BENCHMARK_PATCH.diff").read_bytes()
    else:
        revision = command(["git", "rev-parse", "HEAD"], cwd=SOURCE).stdout.decode().strip()
        diff = command(["git", "diff", "HEAD"], cwd=SOURCE).stdout
    manifest = {
        "source_commit": revision,
        "uncommitted_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "model": MODEL,
        "reasoning_effort": REASONING,
        "dataset_sha256": catalog.dataset_sha256,
        "indices": args.indices,
        "instance_ids": [catalog.instance_ids[index] for index in args.indices],
        "memory": False,
        "plugins": False,
        "sedimentation": False,
        "steering": False,
        "arms": ["off", "on"],
        "concurrency": args.concurrency,
        "max_concurrency": args.max_concurrency,
        "docker_network": args.docker_network,
        "gateway_host": args.gateway_host,
        "pause_file": str(args.pause_file),
        "quota_file": str(args.quota_file),
        "limit_file": str(args.limit_file),
        "experimental_opaque_admission": True,
        "production_security_acceptance": False,
    }
    write_json(args.output / "manifest.json", manifest)
    (args.output / "source.patch").write_bytes(diff)
    if args.plan_only:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    if os.geteuid() != 0:
        raise RuntimeError("run the isolated runner with sudo so its Docker-based official grader can access Docker")  # noqa: TRY003 - operator-facing setup prerequisite
    admission = Admission(args)
    pairs = []
    with ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
        futures = {executor.submit(run_pair, args, catalog, index, admission): index for index in args.indices}
        for future in as_completed(futures):
            try:
                pairs.append(future.result())
            except Exception as error:
                pairs.append({
                    "source_index": futures[future],
                    "status": "runner_error",
                    "error_type": type(error).__name__,
                })
            write_json(args.output / "results.json", sorted(pairs, key=lambda item: item["source_index"]))


if __name__ == "__main__":
    main()
