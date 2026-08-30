#!/usr/bin/env python3
"""Measured helper idle and sequential-action release probe."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import socket
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile_value))))
    return ordered[index]


def proc_metrics(pid: int) -> tuple[float, int, int]:
    fields = (Path("/proc") / str(pid) / "stat").read_text().split()
    cpu_seconds = (int(fields[13]) + int(fields[14])) / os.sysconf("SC_CLK_TCK")
    status = (Path("/proc") / str(pid) / "status").read_text().splitlines()
    values = {line.split(":", 1)[0]: line.split()[1] for line in status if ":" in line and line.split()[1:2]}
    return cpu_seconds, int(values["VmRSS"]), int(values["VmHWM"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle-seconds", type=int, default=300)
    parser.add_argument("--actions", type=int, default=500)
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    runtime = root / "runtime"
    state = root / "state"
    home = root / "home"
    runtime.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    environment = dict(
        os.environ,
        HOME=str(home),
        XDG_DOWNLOAD_DIR=str(home / "Downloads"),
        XDG_RUNTIME_DIR=str(runtime),
        XDG_STATE_HOME=str(state),
    )
    process = subprocess.Popen(
        [
            str(PROJECT / "helper" / "sidecard"),
            "--plugin-root", str(PROJECT), "--fake-route", "--fake-adapters", "--port", str(port),
        ],
        cwd=PROJECT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    def control(*arguments: str) -> dict:
        return json.loads(subprocess.check_output(
            [str(PROJECT / "helper" / "sidecarctl"), *arguments],
            cwd=PROJECT, env=environment, text=True, timeout=5,
        ))

    credential = ""

    def request(method: str, path: str, body: dict | None = None) -> dict:
        headers: dict[str, str] = {}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers.update({"Content-Type": "application/json", "Origin": origin})
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        with urllib.request.urlopen(
            urllib.request.Request(origin + path, data=data, headers=headers, method=method), timeout=5,
        ) as response:
            return json.load(response)

    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("helper exited during startup")
            try:
                request("GET", "/health")
                break
            except urllib.error.URLError:
                time.sleep(0.05)
        else:
            raise RuntimeError("helper startup timed out")

        opened = control("pair-open")
        secret = opened["pairUrl"].split("#", 1)[1]
        pending = request(
            "POST", "/api/v1/pair/request",
            {"secret": secret, "device": {"name": "Performance Phone", "platform": "lab", "clientVersion": "0.2.1", "protocol": 1}},
        )
        secret = ""
        control(
            "pair-approve", pending["requestId"], "--scopes",
            "read:desktop,control:workspace,control:window-focus,control:window-move,control:media,control:theme,control:lock,write:inbox",
        )
        delivered = request(
            "POST", "/api/v1/pair/status",
            {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
        )
        credential = delivered["credential"]

        cpu_start, _, _ = proc_metrics(process.pid)
        idle_start = time.monotonic()
        time.sleep(args.idle_seconds)
        idle_elapsed = time.monotonic() - idle_start
        cpu_end, idle_rss_kib, high_water_kib = proc_metrics(process.pid)
        idle_cpu_percent = (cpu_end - cpu_start) * 100 / idle_elapsed

        snapshot_latencies: list[float] = []
        snapshot = {}
        for _ in range(10):
            started = time.perf_counter()
            snapshot = request("GET", "/api/v1/snapshot")
            snapshot_latencies.append((time.perf_counter() - started) * 1000)
            time.sleep(0.25)

        action_latencies: list[float] = []
        next_action = time.monotonic()
        for index in range(args.actions):
            next_action += 0.25
            target = "ws_2" if index % 2 == 0 else "ws_1"
            started = time.perf_counter()
            result = request(
                "POST", "/api/v1/actions",
                {
                    "requestId": f"req_perf_{index:04d}",
                    "action": "workspace.focus",
                    "parameters": {"workspaceId": target},
                    "expectedSeq": snapshot.get("seq", 0),
                },
            )
            if result.get("status") != "completed":
                raise RuntimeError("action did not complete")
            action_latencies.append((time.perf_counter() - started) * 1000)
            remaining = next_action - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

        _, upload_rss_before_kib, upload_hwm_before_kib = proc_metrics(process.pid)
        upload_body = b"x" * (25 * 1024 * 1024)
        upload_digest = hashlib.sha256(upload_body).hexdigest()
        intent = request(
            "POST", "/api/v1/inbox/intents",
            {
                "requestId": "drop_perf_maximum",
                "files": [{
                    "name": "performance-fixture.txt", "mediaType": "text/plain",
                    "size": len(upload_body), "sha256": upload_digest,
                }],
            },
        )
        upload = intent["files"][0]
        upload_started = time.perf_counter()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request(
            "POST", "/api/v1/inbox/uploads/" + upload["uploadId"], body=upload_body,
            headers={
                "Authorization": f"Bearer {credential}",
                "Content-Type": "text/plain",
                "Content-Length": str(len(upload_body)),
                "Origin": origin,
            },
        )
        upload_response = connection.getresponse()
        upload_result = json.load(upload_response)
        upload_response.close()
        connection.close()
        upload_latency_ms = (time.perf_counter() - upload_started) * 1000
        upload_receipt = upload_result.get("receipt", {})
        if upload_receipt.get("sha256") != upload_digest or upload_receipt.get("size") != len(upload_body):
            raise RuntimeError("maximum-size upload did not commit exactly")
        del upload_body
        _, final_rss_kib, final_high_water_kib = proc_metrics(process.pid)
        report = {
            "build": "sidecard-v1011",
            "python": platform.python_version(),
            "machine": platform.machine(),
            "idleSeconds": round(idle_elapsed, 3),
            "idleCpuPercent": round(idle_cpu_percent, 3),
            "idleRssKiB": idle_rss_kib,
            "peakRssKiB": max(high_water_kib, final_high_water_kib),
            "finalRssKiB": final_rss_kib,
            "warmSnapshotMedianMs": round(statistics.median(snapshot_latencies), 3),
            "warmSnapshotP95Ms": round(percentile(snapshot_latencies, 0.95), 3),
            "actions": len(action_latencies),
            "actionMedianMs": round(statistics.median(action_latencies), 3),
            "actionP95Ms": round(percentile(action_latencies, 0.95), 3),
            "ratePerSecond": 4,
            "uploadBytes": 25 * 1024 * 1024,
            "uploadLatencyMs": round(upload_latency_ms, 3),
            "uploadRssDeltaKiB": final_rss_kib - upload_rss_before_kib,
            "uploadPeakDeltaKiB": max(0, final_high_water_kib - upload_hwm_before_kib),
            "uploadSha256": upload_digest,
        }
        print(json.dumps(report, separators=(",", ":")))
        return 0
    finally:
        credential = ""
        if process.poll() is None:
            try:
                control("shutdown")
                process.wait(timeout=5)
            except Exception:
                process.terminate()
                process.wait(timeout=5)
        process.communicate(timeout=2)
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
