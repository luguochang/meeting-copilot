#!/usr/bin/env python3
"""Controllable local service used to exercise the NEXT-006 soak executor."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any


SCHEMA_VERSION = "meeting_copilot.next006_fault_fixture.v1"


class FixtureState:
    def __init__(self, *, data_dir: Path, latency_ms: float, queue_depth: int) -> None:
        self.data_dir = data_dir
        self.latency_ms = latency_ms
        self.queue_depth = queue_depth
        self.network_available = True
        self.disk_available = True
        self.request_count = 0
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.request_count += 1
            return {
                "schema_version": SCHEMA_VERSION,
                "queue_depth": self.queue_depth,
                "latency_ms": self.latency_ms,
                "request_count": self.request_count,
                "network_available": self.network_available,
                "disk_available": self.disk_available,
            }

    def set_available(self, fault: str, enabled: bool) -> None:
        with self._lock:
            if fault == "network":
                self.network_available = enabled
            elif fault == "disk":
                self.disk_available = enabled
            else:
                raise ValueError(f"unsupported fault: {fault}")

    def availability(self, fault: str) -> bool:
        with self._lock:
            if fault == "network":
                return self.network_available
            if fault == "disk":
                return self.disk_available
        raise ValueError(f"unsupported fault: {fault}")


class FixtureServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: FixtureState) -> None:
        super().__init__(address, FixtureHandler)
        self.state = state


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok", "schema_version": SCHEMA_VERSION})
            return
        if self.path == "/metrics":
            self._json(200, self.server.state.snapshot())
            return
        if self.path == "/network/dependency":
            if self.server.state.availability("network"):
                self._json(200, {"available": True})
            else:
                self._json(
                    503, {"available": False, "error": "simulated_network_disconnect"}
                )
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path in {"/control/network", "/control/disk"}:
            fault = self.path.rsplit("/", 1)[-1]
            body = self._read_json()
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                self._json(400, {"error": "enabled_must_be_boolean"})
                return
            self.server.state.set_available(fault, enabled)
            self._json(200, {"fault": fault, "enabled": enabled})
            return
        if self.path == "/disk/write":
            if not self.server.state.availability("disk"):
                self._json(
                    507, {"written": False, "error": "simulated_disk_write_failure"}
                )
                return
            body = self._read_json()
            self.server.state.data_dir.mkdir(parents=True, exist_ok=True)
            target = self.server.state.data_dir / "disk-probe.json"
            target.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
            self._json(200, {"written": True})
            return
        self._json(404, {"error": "not_found"})

    def _read_json(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        raw = self.rfile.read(max(0, min(size, 1024 * 1024)))
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the controllable NEXT-006 fault fixture."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--latency-ms", type=float, default=12.5)
    parser.add_argument("--queue-depth", type=int, default=2)
    args = parser.parse_args(argv)

    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.latency_ms < 0:
        parser.error("--latency-ms must be non-negative")
    if args.queue_depth < 0:
        parser.error("--queue-depth must be non-negative")

    state = FixtureState(
        data_dir=args.data_dir.resolve(),
        latency_ms=args.latency_ms,
        queue_depth=args.queue_depth,
    )
    server = FixtureServer((args.host, args.port), state)
    if args.ready_file is not None:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.write_text(
            json.dumps(
                {
                    "host": args.host,
                    "port": server.server_port,
                    "ready_at_unix": time.time(),
                }
            )
            + "\n",
            encoding="utf-8",
        )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
