#!/usr/bin/env python3
"""Guard benchmark dispatch using Codex's account usage endpoint.

Read-only unless --allow-reset is supplied. A reset's durable request ID is
written before sending; an ambiguous result is reconciled by reads, never by
automatically sending another consume request. This script never buys credits.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE_URL = "https://chatgpt.com/backend-api/wham"
PAUSE_OWNER = "benchmark-quota-guard\n"


class GuardError(Exception):
    """Sanitized operational failure: never retain provider bodies or credentials."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GuardError("redirect_rejected")


def write_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class QuotaGuard:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = args.root
        self.auth_path = args.auth_file or self.root / "credentials/codex-auth.json"
        self.pause_path = self.root / "PAUSE"
        self.quota_path = self.root / "quota.json"
        self.state_path = self.root / ".quota-guard-reset.json"
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": args.proxy, "https": args.proxy}), NoRedirect()
        )
        self.last_status: str | None = None
        self.snapshot: dict[str, Any] = {}

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            tokens = json.loads(self.auth_path.read_text())["tokens"]
            token = tokens["access_token"]
            account = tokens.get("account_id")
            if not isinstance(token, str) or not token:
                raise ValueError
            headers = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli"}
            if isinstance(account, str) and account:
                headers["ChatGPT-Account-ID"] = account
            data = None
            if payload is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(payload).encode()
            request = urllib.request.Request(f"{BASE_URL}/{path}", data=data, headers=headers)
            with self.opener.open(request, timeout=self.args.timeout) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            if value.get("account_id") is not None and account and value["account_id"] != account:
                raise GuardError("account_mismatch")
            return value
        except GuardError:
            raise
        except urllib.error.HTTPError as error:
            raise GuardError(f"http_{error.code}") from None
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            raise GuardError("request_failed") from None

    def read_usage(self) -> tuple[dict[str, Any], bool]:
        value = self.request("usage")
        limits = [value.get("rate_limit")]
        for additional in value.get("additional_rate_limits") or []:
            if not isinstance(additional, dict):
                raise GuardError("usage_invalid")
            if (
                additional.get("limit_name") == self.args.model
                or additional.get("normal_model_slug") == self.args.model
                or additional.get("metered_feature") in self.args.bucket
            ):
                limits.append(additional.get("rate_limit"))
        windows = []
        allowed = True
        for limit in limits:
            if not isinstance(limit, dict) or not isinstance(limit.get("allowed"), bool):
                raise GuardError("usage_unavailable")
            allowed = allowed and limit["allowed"] and limit.get("limit_reached") is not True
            for name in ("primary_window", "secondary_window"):
                window = limit.get(name)
                if window is None:
                    continue
                if not isinstance(window, dict):
                    raise GuardError("usage_invalid")
                used = window.get("used_percent")
                reset_at = window.get("reset_at")
                if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or used < 0:
                    raise GuardError("usage_invalid")
                if reset_at is not None and (isinstance(reset_at, bool) or not isinstance(reset_at, int)):
                    raise GuardError("usage_invalid")
                windows.append({"used_percent": used, "remaining_percent": max(0, 100 - used), "reset_at": reset_at})
        if not windows:
            raise GuardError("usage_unavailable")
        credits = value.get("rate_limit_reset_credits")
        count = credits.get("available_count") if isinstance(credits, dict) else None
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise GuardError("usage_invalid")
        return {
            "used_percent": max(window["used_percent"] for window in windows),
            "remaining_percent": min(window["remaining_percent"] for window in windows),
            "windows": windows,
            "available_reset_credits": count,
        }, allowed

    def pause(self, status: str) -> None:
        if not self.pause_path.exists():
            try:
                with self.pause_path.open("x") as stream:
                    stream.write(PAUSE_OWNER + status + "\n")
            except FileExistsError:
                pass

    def unpause(self) -> None:
        if self.pause_path.exists() and self.pause_path.read_text().startswith(PAUSE_OWNER):
            self.pause_path.unlink(missing_ok=True)

    def publish(self, status: str) -> None:
        write_json(self.quota_path, {**self.snapshot, "status": status})
        if status != self.last_status:
            print(f"quota_guard status={status}", flush=True)
            self.last_status = status

    def state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text())
            if not isinstance(value, dict) or value.get("status") not in {
                "pending",
                "unknown",
                "awaiting_refresh",
                "nothing_to_reset",
                "no_credit",
                "complete",
            }:
                raise ValueError
            return value
        except (OSError, ValueError):
            raise GuardError("reset_state_invalid") from None

    def apply_snapshot(self, allowed: bool, state: dict[str, Any]) -> bool:
        healthy = allowed and self.snapshot["remaining_percent"] > self.args.threshold
        if healthy:
            if state and state.get("status") != "complete":
                # Recovery is based on a fresh allowed usage read, not a POST acknowledgement.
                state["status"] = "complete"
                write_json(self.state_path, state)
            self.unpause()
            self.publish("ready")
            return True
        self.pause("low_remaining" if allowed else "usage_blocked")
        return False

    def poll(self) -> None:
        self.snapshot, allowed = self.read_usage()
        state = self.state()
        if self.apply_snapshot(allowed, state):
            return
        pending = state.get("status") in {"pending", "unknown", "awaiting_refresh"}
        if pending:
            previous_count = state.get("credits_before")
            count = self.snapshot["available_reset_credits"]
            if isinstance(previous_count, int) and isinstance(count, int) and count < previous_count:
                state["status"] = "awaiting_refresh"
                write_json(self.state_path, state)
            self.publish("reset_awaiting_verification")
            return
        count = self.snapshot["available_reset_credits"]
        if not self.args.allow_reset:
            self.publish("paused_readonly")
            return
        if self.snapshot["remaining_percent"] > self.args.threshold:
            self.publish("paused_usage_blocked")
            return
        if not isinstance(count, int) or count < 1:
            self.publish("paused_no_reset_credit")
            return
        if state.get("status") == "nothing_to_reset":
            self.publish("paused_nothing_to_reset")
            return
        if state.get("status") == "no_credit" and count <= state.get("credits_before", count):
            self.publish("paused_no_reset_credit")
            return
        if self.args.active_unit:
            try:
                active = any(
                    subprocess.run(
                        ["systemctl", "is-active", "--quiet", unit],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    ).returncode
                    == 0
                    for unit in self.args.active_unit
                )
            except (OSError, subprocess.TimeoutExpired):
                active = False
            if not active:
                self.publish("paused_no_active_benchmark")
                return
        state = {"redeem_request_id": str(uuid.uuid4()), "credits_before": count, "status": "pending"}
        write_json(self.state_path, state)
        try:
            response = self.request(
                "rate-limit-reset-credits/consume", {"redeem_request_id": state["redeem_request_id"]}
            )
            code = response.get("code")
            state["status"] = {
                "reset": "awaiting_refresh",
                "already_redeemed": "awaiting_refresh",
                "nothing_to_reset": "nothing_to_reset",
                "no_credit": "no_credit",
            }.get(code, "unknown")
        except GuardError:
            state["status"] = "unknown"
        write_json(self.state_path, state)
        # A timeout can follow a successful consumption. Re-read and never retry the POST blindly.
        self.snapshot, allowed = self.read_usage()
        if not self.apply_snapshot(allowed, state):
            self.publish("reset_awaiting_verification")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/data/0/powercontext-code-reuse-bench"))
    parser.add_argument("--auth-file", type=Path)
    parser.add_argument("--proxy", default="http://127.0.0.1:18390")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--bucket", action="append", default=[], help="Additional relevant metered_feature ID")
    parser.add_argument(
        "--active-unit", action="append", default=[], help="Only consume a reset while any listed system unit is active"
    )
    parser.add_argument("--threshold", type=float, default=10)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--allow-reset", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.threshold < 100 or not 0 < args.interval <= 60 or not 0 < args.timeout <= 60:
        parser.error("invalid threshold, interval, or timeout")
    args.root.mkdir(parents=True, exist_ok=True)
    guard = QuotaGuard(args)
    with (args.root / ".quota-guard.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("quota_guard status=already_running", flush=True)
            return 1
        guard.pause("initializing")
        while True:
            try:
                guard.poll()
            except GuardError as error:
                guard.pause("read_failed")
                guard.publish(str(error))
            except (OSError, ValueError, TypeError):
                guard.pause("guard_failed")
                guard.publish("guard_failed")
            if args.once:
                return 0 if guard.last_status == "ready" else 1
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
