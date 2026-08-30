"""Exact foreground ownership for the stable Tailscale Serve route."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Any

from .constants import LOOPBACK_HOST, LOOPBACK_PORT, TAILSCALE_HTTPS_PORT


class RouteError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _parent_death_signal() -> None:
    libc = ctypes.CDLL(None)
    pr_set_pdeathsig = 1
    if libc.prctl(pr_set_pdeathsig, signal.SIGTERM) != 0:
        raise OSError("prctl(PR_SET_PDEATHSIG) failed")


def _contains_port(value: Any, port: int) -> bool:
    needle = str(port)
    if isinstance(value, dict):
        return any(str(key) == needle or _contains_port(item, port) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_port(item, port) for item in value)
    if isinstance(value, str):
        return bool(re.search(rf"(?:^|[:=]){port}(?:$|[/,\s])", value))
    return value == port


def _contains_exact_value(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_exact_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_value(item, expected) for item in value)
    return value == expected


def _contains_exact_route(value: Any, port: int, target: str) -> bool:
    """Match a port and target within one Serve entry, never across siblings."""
    if isinstance(value, dict):
        if any(
            _contains_port(str(key), port) and _contains_exact_value(item, target)
            for key, item in value.items()
        ):
            return True
        scalar_port_field = any(
            not isinstance(item, (dict, list)) and _contains_port(item, port)
            for item in value.values()
        )
        if scalar_port_field and _contains_exact_value(value, target):
            return True
        return any(_contains_exact_route(item, port, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_route(item, port, target) for item in value)
    return False


class FakeRouteController:
    def __init__(self, host: str = LOOPBACK_HOST, port: int = LOOPBACK_PORT):
        self.endpoint = f"http://{host}:{port}"
        self.state = "ready"
        self.error_code = ""
        self.error_message = ""

    def start(self) -> str:
        return self.endpoint

    def stop(self) -> None:
        self.state = "stopped"

    def child_pid(self) -> int | None:
        return None


class TailscaleRouteController:
    def __init__(
        self,
        loopback_port: int = LOOPBACK_PORT,
        https_port: int = TAILSCALE_HTTPS_PORT,
        *,
        startup_timeout: float = 6.0,
        health_interval: float = 5.0,
    ):
        self.loopback_port = loopback_port
        self.https_port = https_port
        self.startup_timeout = startup_timeout
        self.health_interval = health_interval
        self.endpoint = ""
        self.state = "starting"
        self.error_code = ""
        self.error_message = ""
        self._child: subprocess.Popen[str] | None = None
        self._output_tail = ""
        self._output_lock = threading.Lock()
        self._drain_threads: list[threading.Thread] = []
        self._last_health_check = 0.0
        self._last_health_result = False
        self._lock = threading.RLock()
        self._tailscale_path = ""

    def _resolve_tailscale(self) -> str:
        if self._tailscale_path:
            return self._tailscale_path
        resolved = shutil.which("tailscale")
        if resolved is None:
            raise RouteError(
                "tailscale-unavailable",
                "Sidecar needs Tailscale. Open Omarchy Install → Service → Tailscale, finish setup, then click Try again. Sidecar made no system changes.",
            )
        self._tailscale_path = os.path.realpath(resolved)
        return self._tailscale_path

    def _run_json(self, argv: list[str], allow_empty: bool = False) -> Any:
        if not argv or argv[0] != "tailscale":
            raise RouteError("tailscale-unavailable", "Sidecar refused an unexpected transport command.")
        command = [self._resolve_tailscale(), *argv[1:]]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RouteError("tailscale-unavailable", "Tailscale could not be reached.") from error
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip().lower()
            if "permission" in message or "access denied" in message:
                raise RouteError("tailscale-permission-denied", "Tailscale did not allow Sidecar to create a Serve route.")
            raise RouteError("tailscale-disconnected", "Tailscale is not connected.")
        if allow_empty and not result.stdout.strip():
            return {}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RouteError("tailscale-status-invalid", "Tailscale returned an unreadable route status. Sidecar did not change it.") from error

    def _preflight(self) -> str:
        self._resolve_tailscale()
        status = self._run_json(["tailscale", "status", "--json"])
        if not isinstance(status, dict) or status.get("BackendState") != "Running":
            raise RouteError("tailscale-disconnected", "Tailscale is not connected. Connect it, then reopen Sidecar.")
        own = status.get("Self") if isinstance(status.get("Self"), dict) else {}
        dns_name = str(own.get("DNSName", "")).rstrip(".")
        valid_dns = re.fullmatch(
            r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}",
            dns_name,
        )
        if not valid_dns or not dns_name.lower().endswith(".ts.net"):
            raise RouteError("tailscale-https-unavailable", "Tailscale HTTPS is not ready for this device.")
        serve = self._run_json(["tailscale", "serve", "status", "--json"], allow_empty=True)
        if "funnel" in json.dumps(serve, sort_keys=True).lower():
            raise RouteError("tailscale-route-conflict", "Tailscale reports Funnel configuration. Sidecar did not change it; remove the conflict yourself before reopening Sidecar.")
        if _contains_port(serve, self.https_port):
            raise RouteError("tailscale-route-conflict", f"A Tailscale Serve route already uses port {self.https_port}. Sidecar did not change it.")
        return dns_name

    def _owned_route_visible(self, serve: Any) -> bool:
        target = f"http://{LOOPBACK_HOST}:{self.loopback_port}"
        return _contains_exact_route(serve, self.https_port, target)

    def _record_output(self, child: subprocess.Popen[str], stream: Any) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                with self._output_lock:
                    if child is self._child:
                        self._output_tail = (self._output_tail + line)[-4096:]
        except (OSError, ValueError):
            return

    def _start_output_drains(self, child: subprocess.Popen[str]) -> None:
        self._drain_threads = []
        for name, stream in (("stdout", child.stdout), ("stderr", child.stderr)):
            thread = threading.Thread(
                target=self._record_output,
                args=(child, stream),
                name=f"sidecar-tailscale-{name}",
                daemon=True,
            )
            thread.start()
            self._drain_threads.append(thread)

    def _terminate_child(self, child: subprocess.Popen[str] | None) -> None:
        if child is None or child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=2)

    def _start_error(self, default_code: str = "tailscale-route-failed") -> RouteError:
        with self._output_lock:
            output = self._output_tail.lower()
        if "permission" in output or "access denied" in output:
            return RouteError("tailscale-permission-denied", "Tailscale did not allow Sidecar to create its Serve route.")
        if "https" in output or "certificate" in output or "login.tailscale.com" in output:
            return RouteError(
                "tailscale-https-unavailable",
                "Tailscale did not activate Sidecar’s HTTPS route. Enable HTTPS certificates in Tailscale Admin → DNS, then choose Try again.",
            )
        return RouteError(default_code, "Sidecar could not activate its exact Tailscale Serve route.")

    def _fail_start(self, error: RouteError) -> None:
        child = self._child
        self._terminate_child(child)
        self._child = None
        self.state = "unavailable"
        self.error_code = error.code
        self.error_message = error.message
        self._last_health_result = False
        raise error

    def start(self) -> str:
        with self._lock:
            self._terminate_child(self._child)
            self._child = None
            with self._output_lock:
                self._output_tail = ""
            self.state = "starting"
            dns_name = self._preflight()
            argv = [
                self._resolve_tailscale(), "serve", "--yes", f"--https={self.https_port}",
                f"http://{LOOPBACK_HOST}:{self.loopback_port}",
            ]
            try:
                self._child = subprocess.Popen(
                    argv, text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True, preexec_fn=_parent_death_signal,
                )
            except OSError as error:
                raise RouteError("tailscale-route-failed", "Sidecar could not start its Tailscale Serve route.") from error
            child = self._child
            self._start_output_drains(child)
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    self._fail_start(self._start_error())
                try:
                    serve = self._run_json(["tailscale", "serve", "status", "--json"], allow_empty=True)
                except RouteError as error:
                    self._fail_start(error)
                if "funnel" in json.dumps(serve, sort_keys=True).lower():
                    self._fail_start(RouteError(
                        "tailscale-route-conflict",
                        "Tailscale reports Funnel configuration. Sidecar stopped its child and changed no unrelated route.",
                    ))
                if self._owned_route_visible(serve):
                    break
                if _contains_port(serve, self.https_port):
                    self._fail_start(RouteError(
                        "tailscale-route-conflict",
                        f"Tailscale activated port {self.https_port} for a different target. Sidecar stopped its child.",
                    ))
                time.sleep(0.2)
            else:
                self._fail_start(RouteError(
                    "tailscale-https-unavailable",
                    "Tailscale did not activate Sidecar’s HTTPS route. Enable HTTPS certificates in Tailscale Admin → DNS, then choose Try again.",
                ))
            self.endpoint = f"https://{dns_name}:{self.https_port}"
            self.state = "ready"
            self.error_code = ""
            self.error_message = ""
            self._last_health_check = time.monotonic()
            self._last_health_result = True
            return self.endpoint

    def stop(self) -> None:
        with self._lock:
            child = self._child
            self._child = None
            self.state = "stopping"
        self._terminate_child(child)
        self.state = "stopped"

    def child_pid(self) -> int | None:
        with self._lock:
            return self._child.pid if self._child is not None and self._child.poll() is None else None

    def healthy(self) -> bool:
        with self._lock:
            child = self._child
            if self.state != "ready" or child is None or child.poll() is not None:
                self._last_health_result = False
                return False
            now = time.monotonic()
            if now - self._last_health_check < self.health_interval:
                return self._last_health_result
            self._last_health_check = now
            try:
                serve = self._run_json(["tailscale", "serve", "status", "--json"], allow_empty=True)
                self._last_health_result = (
                    "funnel" not in json.dumps(serve, sort_keys=True).lower()
                    and self._owned_route_visible(serve)
                )
            except RouteError:
                self._last_health_result = False
            if not self._last_health_result:
                self.state = "unavailable"
            return self._last_health_result
