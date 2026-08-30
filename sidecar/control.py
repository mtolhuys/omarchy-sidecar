"""Peer-verified local management socket."""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
from pathlib import Path
from typing import Any, Callable

from .constants import MAX_CONTROL_REQUEST
from .core import ApiError, SidecarCore
from .util import private_directory, strict_json_loads


class ControlServer:
    def __init__(self, socket_path: Path, core: SidecarCore, shutdown: Callable[[], None]):
        self.socket_path = socket_path
        self.core = core
        self.shutdown_callback = shutdown
        self._socket: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, name="sidecar-control", daemon=True)
        self._stop = threading.Event()
        self._connection_slots = threading.BoundedSemaphore(8)

    def start(self) -> None:
        private_directory(self.socket_path.parent)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        self._socket = server
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._connection_slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self._handle_bounded, args=(connection,), daemon=True).start()

    def _handle_bounded(self, connection: socket.socket) -> None:
        try:
            self._handle(connection)
        finally:
            self._connection_slots.release()

    def _handle(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(2)
            try:
                credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                _pid, uid, _gid = struct.unpack("3i", credentials)
                if uid != os.geteuid():
                    return
                chunks = bytearray()
                while len(chunks) <= MAX_CONTROL_REQUEST:
                    part = connection.recv(min(4096, MAX_CONTROL_REQUEST + 1 - len(chunks)))
                    if not part:
                        break
                    chunks.extend(part)
                    if b"\n" in part:
                        break
                if len(chunks) > MAX_CONTROL_REQUEST or b"\n" not in chunks:
                    raise ValueError("control request is too large or incomplete")
                request = strict_json_loads(bytes(chunks).split(b"\n", 1)[0])
                response = {"ok": True, "result": self._dispatch(request)}
            except (ValueError, KeyError, ApiError) as error:
                response = {"ok": False, "error": getattr(error, "message", str(error)) or "Request failed"}
            except Exception:
                response = {"ok": False, "error": "Local management request failed"}
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            try:
                connection.sendall(encoded)
            except OSError:
                pass

    def _dispatch(self, request: Any) -> Any:
        if not isinstance(request, dict) or set(request) != {"op", "args"} or not isinstance(request["op"], str) or not isinstance(request["args"], dict):
            raise ValueError("invalid local request")
        operation = request["op"]
        arguments = request["args"]
        if operation == "status" and not arguments:
            return self.core.local_status()
        if operation == "pair.open" and not arguments:
            return self.core.open_pairing()
        if operation == "pair.cancel" and not arguments:
            self.core.cancel_pairing()
            return self.core.local_status()
        if operation == "pair.approve" and set(arguments) == {"requestId", "scopes"}:
            if not isinstance(arguments["scopes"], list):
                raise ValueError("scopes must be a list")
            return self.core.approve(arguments["requestId"], arguments["scopes"])
        if operation == "pair.deny" and set(arguments) == {"requestId"}:
            self.core.deny(arguments["requestId"])
            return self.core.local_status()
        if operation == "capability.approve" and set(arguments) == {"requestId"}:
            return self.core.approve_capabilities(arguments["requestId"])
        if operation == "capability.deny" and set(arguments) == {"requestId"}:
            self.core.deny_capabilities(arguments["requestId"])
            return self.core.local_status()
        if operation == "device.rename" and set(arguments) == {"deviceId", "name"}:
            return self.core.store.rename(arguments["deviceId"], arguments["name"])
        if operation == "device.rescope" and set(arguments) == {"deviceId", "scopes"} and isinstance(arguments["scopes"], list):
            return self.core.rescope_device(arguments["deviceId"], arguments["scopes"])
        if operation == "device.revoke" and set(arguments) == {"deviceId"}:
            return self.core.revoke(arguments["deviceId"])
        if operation == "pause.set" and set(arguments) == {"paused"} and isinstance(arguments["paused"], bool):
            return self.core.set_paused(arguments["paused"])
        if operation == "diagnostics" and not arguments:
            return self.core.diagnostics()
        if operation == "route.retry" and not arguments:
            return self.core.retry_route()
        if operation == "inbox.open" and not arguments:
            return self.core.open_inbox_local()
        if operation == "shutdown" and not arguments:
            threading.Thread(target=self.shutdown_callback, daemon=True).start()
            return {"status": "stopping"}
        raise ValueError("unknown local management operation")
