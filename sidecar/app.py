"""Helper lifecycle composition."""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import threading
from pathlib import Path

from .adapters import FakeAdapter, OmarchyAdapter
from .constants import HELPER_BUILD_ID, LOOPBACK_HOST, LOOPBACK_PORT, SERVICE_BUILD_ID
from .control import ControlServer
from .core import SidecarCore
from .inbox import InboxManager
from .routes import FakeRouteController, RouteError, TailscaleRouteController
from .server import SidecarHttpServer
from .store import DeviceStore
from .util import atomic_json_write, json_safe_log, private_directory


def xdg_state_home() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    return Path(configured) if configured else Path.home() / ".local" / "state"


def xdg_runtime_dir() -> Path:
    configured = os.environ.get("XDG_RUNTIME_DIR")
    if not configured:
        raise RuntimeError("XDG_RUNTIME_DIR is required for the private Sidecar control channel")
    return Path(configured)


def set_parent_death_signal() -> None:
    try:
        libc = ctypes.CDLL(None)
        if libc.prctl(1, signal.SIGTERM) != 0:
            raise OSError
    except Exception:
        print(json_safe_log("parent-death-signal-unavailable", code="lifecycle-guard"), flush=True)


class Application:
    def __init__(self, arguments: argparse.Namespace):
        self.arguments = arguments
        self.stop_event = threading.Event()
        self.runtime_root = private_directory(xdg_runtime_dir() / "omarchy-sidecar")
        self.status_path = self.runtime_root / "status.json"
        self.socket_path = self.runtime_root / "control.sock"
        self.store = DeviceStore(xdg_state_home())
        self.adapter = FakeAdapter() if arguments.fake_adapters else OmarchyAdapter()
        self.route = FakeRouteController(port=arguments.port) if arguments.fake_route else TailscaleRouteController(loopback_port=arguments.port, https_port=arguments.https_port)
        self.core = SidecarCore(
            self.store,
            self.adapter,
            self.route,
            arguments.service_build_id,
            listener_host=LOOPBACK_HOST,
            listener_port=arguments.port,
            https_port=arguments.https_port,
            inbox=InboxManager(home=Path.home(), announce=not arguments.fake_adapters),
        )
        web_root = Path(arguments.plugin_root).resolve() / "web" / "dist"
        self.http = SidecarHttpServer((LOOPBACK_HOST, arguments.port), self.core, web_root)
        self.http_thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.1}, name="sidecar-http", daemon=True)
        self.control = ControlServer(self.socket_path, self.core, self.request_stop)

    def _write_status(self) -> None:
        status = self.core.local_status()
        redacted = {
            "version": status["version"],
            "protocol": status["protocol"],
            "builds": status["builds"],
            "bootId": status["bootId"],
            "state": status["state"],
            "paused": status["paused"],
            "locked": status["locked"],
            "routeState": status["route"]["state"],
            "deviceCount": status["deviceCount"],
            "onlineCount": status["onlineCount"],
            "adapter": status["adapter"],
            "errorCode": (status["lastError"] or {}).get("code", ""),
            "pid": os.getpid(),
            "listener": f"{LOOPBACK_HOST}:{self.arguments.port}",
            "securityReceipt": status["securityReceipt"],
        }
        atomic_json_write(self.status_path, redacted)

    def run(self) -> int:
        self.http_thread.start()
        try:
            try:
                origin = self.route.start()
                self.core.set_origin(origin)
            except RouteError as error:
                self.core.set_route_error(error.code, error.message)
            self.core.start()
            self.control.start()
            self._write_status()
            print(json_safe_log("helper-ready", build=HELPER_BUILD_ID, protocol=1, state=self.core.local_status()["state"]), flush=True)
            while not self.stop_event.wait(1):
                self._write_status()
                if hasattr(self.route, "healthy") and self.core.route_error is None and not self.route.healthy():
                    self.core.set_route_error(
                        "tailscale-route-stopped",
                        "The exact Sidecar Serve route disappeared. Sidecar stopped pairing and will retry automatically.",
                    )
            return 0
        finally:
            self.shutdown()

    def request_stop(self) -> None:
        self.stop_event.set()

    def shutdown(self) -> None:
        if self.stop_event.is_set() and not self.http_thread.is_alive() and not self.socket_path.exists():
            return
        self.stop_event.set()
        self.core.stop()
        self.control.stop()
        self.http.shutdown()
        self.http.server_close()
        if self.http_thread.is_alive():
            self.http_thread.join(timeout=2)
        self.route.stop()
        try:
            self.status_path.unlink()
        except FileNotFoundError:
            pass
        print(json_safe_log("helper-stopped", build=HELPER_BUILD_ID, state="stopped"), flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Omarchy Sidecar helper")
    result.add_argument("--plugin-root", required=True)
    result.add_argument("--service-build-id", default=SERVICE_BUILD_ID)
    result.add_argument("--port", type=int, default=LOOPBACK_PORT)
    result.add_argument("--https-port", type=int, default=48719)
    result.add_argument("--fake-adapters", action="store_true")
    result.add_argument("--fake-route", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() == 0:
        raise SystemExit(
            "Security invariant failed: Sidecar refuses to run as root. "
            "Start it from the non-root Omarchy session."
        )
    arguments = parser().parse_args(argv)
    if not (1024 <= arguments.port <= 65535 and 1024 <= arguments.https_port <= 65535):
        raise SystemExit("Sidecar ports must be between 1024 and 65535")
    plugin_root = Path(arguments.plugin_root).resolve()
    if not (plugin_root / "manifest.json").is_file() or not (plugin_root / "web" / "dist").is_dir():
        raise SystemExit("Sidecar plugin root is incomplete")
    set_parent_death_signal()
    application = Application(arguments)
    signal.signal(signal.SIGTERM, lambda _number, _frame: application.request_stop())
    signal.signal(signal.SIGINT, lambda _number, _frame: application.request_stop())
    return application.run()


if __name__ == "__main__":
    raise SystemExit(main())
