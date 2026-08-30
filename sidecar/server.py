"""Bounded loopback HTTP server for the PWA and protocol v1."""

from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .constants import (
    BODY_TIMEOUT_SECONDS,
    EVENT_HEARTBEAT_SECONDS,
    HEADER_TIMEOUT_SECONDS,
    MAX_REQUEST_BODY,
    MAX_STREAMS,
    MAX_HTTP_CONNECTIONS,
    MAX_INBOX_FILE_BYTES,
    PROTOCOL,
)
from .core import ApiError, SidecarCore
from .util import canonical_json, json_safe_log, normalize_id, strict_json_loads

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=(), display-capture=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}

CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self'",
    "font-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
))


class SidecarHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    # A service hot reload must be able to reclaim this exact loopback port
    # while old client connections are in TIME_WAIT. This does not broaden the
    # bind address or permit a second live listener.
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], core: SidecarCore, web_root: Path):
        self.core = core
        self.web_root = web_root.resolve()
        self._connection_slots = threading.BoundedSemaphore(MAX_HTTP_CONNECTIONS)
        super().__init__(address, SidecarHandler, bind_and_activate=False)
        self.server_bind()
        self.server_activate()

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


class SidecarHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Sidecar"
    sys_version = ""

    @property
    def sidecar(self) -> SidecarHttpServer:
        return self.server  # type: ignore[return-value]

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(HEADER_TIMEOUT_SECONDS)

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if len(self.requestline) > 8192 or len(self.path) > 2048 or len(self.headers) > 64:
            self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
            return False
        if any(len(key) + len(value) > 8192 for key, value in self.headers.items()):
            self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
            return False
        singleton_headers = (
            "Host", "Authorization", "Origin", "Content-Type", "Content-Length",
            "Transfer-Encoding", "Content-Range", "Trailer", "Range", "Expect",
        )
        if any(len(self.headers.get_all(name, [])) > 1 for name in singleton_headers):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        if self.request_version == "HTTP/1.1" and len(self.headers.get_all("Host", [])) != 1:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        if any(self.headers.get_all(name, []) for name in ("Transfer-Encoding", "Content-Range", "Trailer", "Range", "Expect")):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        content_length = self.headers.get("Content-Length", "")
        if content_length and (not content_length.isdigit() or len(content_length) > 10):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        if self.command in {"GET", "HEAD", "OPTIONS"} and content_length:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        return True

    def handle_expect_100(self) -> bool:
        self.send_error(HTTPStatus.EXPECTATION_FAILED)
        return False

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Keep parser-level failures redacted and under the normal headers."""
        del message, explain
        status = int(code)
        body = canonical_json({"error": {"code": "bad_request", "message": "That request was not valid.", "retryable": False}})
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._base_headers()
        self.send_header("Content-Length", "0" if self.command == "HEAD" else str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _base_headers(self, cache_control: str = "no-store") -> None:
        self.send_header("Cache-Control", cache_control)
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)

    def _json(self, status: int, value: Any, extra_headers: dict[str, str] | None = None) -> None:
        body = canonical_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._base_headers()
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _binary(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self._base_headers("private, no-store")
        self.send_header("Content-Disposition", "inline")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _error(self, error: ApiError) -> None:
        headers = {"Retry-After": str(max(1, int(error.details.get("retryAfterMs", 1000) / 1000)))} if error.code == "rate_limited" else None
        self._json(error.status, error.envelope(), headers)

    def _split_path(self) -> tuple[str, str]:
        parsed = urlsplit(self.path)
        return parsed.path, parsed.query

    def _one_header(self, name: str) -> str:
        values = self.headers.get_all(name, [])
        if len(values) > 1:
            raise ApiError("bad_request", 400, message=f"Duplicate {name} headers are not accepted.")
        return values[0] if values else ""

    def _origin(self) -> None:
        expected = self.sidecar.core.origin
        supplied = self._one_header("Origin")
        if not expected or supplied != expected:
            raise ApiError("bad_request", 403, message="This request did not come from the Sidecar app origin.")

    def _json_body(self) -> Any:
        if self.headers.get_all("Transfer-Encoding", []):
            raise ApiError("bad_request", 400, message="Transfer encoding is not accepted.")
        content_type = self._one_header("Content-Type").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError("bad_request", 415, message="Sidecar accepts JSON requests only.")
        raw_length = self._one_header("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            raise ApiError("bad_request", 411)
        length = int(raw_length)
        if length < 0 or length > MAX_REQUEST_BODY:
            raise ApiError("bad_request", 413, message="That request is too large.")
        self.connection.settimeout(BODY_TIMEOUT_SECONDS)
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError("bad_request", 400)
        try:
            return strict_json_loads(body)
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise ApiError("bad_request", 400) from error

    def _authenticate(self) -> dict[str, Any]:
        authorization = self._one_header("Authorization")
        if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
            raise ApiError("not_authenticated", 401)
        return self.sidecar.core.authenticate(authorization[7:], self._source())

    def _source(self) -> str:
        return str(self.client_address[0])

    def do_GET(self) -> None:
        path, query = self._split_path()
        try:
            if path.startswith("/api/") and query:
                raise ApiError("bad_request")
            if path == "/health":
                self._json(200, {"status": "ok", "protocol": {"min": PROTOCOL, "max": PROTOCOL}})
                return
            if path == "/api/v1/snapshot":
                device = self._authenticate()
                self._json(200, self.sidecar.core.snapshot(device))
                return
            preview_prefix = "/api/v1/theme-previews/"
            if path.startswith(preview_prefix):
                device = self._authenticate()
                theme_id = path[len(preview_prefix):]
                if not theme_id or "/" in theme_id:
                    raise ApiError("bad_request", 404, message="Not found.")
                body, content_type = self.sidecar.core.theme_preview(device, theme_id)
                self._binary(body, content_type)
                return
            if path == "/api/v1/events":
                device = self._authenticate()
                self._events(device)
                return
            if path == "/":
                self.send_response(302)
                self._base_headers()
                self.send_header("Location", "/app/")
                self.send_header("Connection", "close")
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.close_connection = True
                return
            if path.startswith("/app"):
                self._static(path)
                return
            raise ApiError("bad_request", 404, message="Not found.")
        except ApiError as error:
            self._error(error)
        except ValueError:
            self._error(ApiError("bad_request", 400))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            print(json_safe_log("http-handler-failed", code=type(error).__name__), flush=True)
            self._error(ApiError("service_unavailable", 503))

    def do_POST(self) -> None:
        path, query = self._split_path()
        try:
            if query:
                raise ApiError("bad_request")
            if path == "/api/v1/pair/request":
                self._origin()
                body = self._json_body()
                self._json(200, self.sidecar.core.pair_request(body, self._source()))
                return
            if path == "/api/v1/pair/status":
                self._origin()
                body = self._json_body()
                self._json(200, self.sidecar.core.pair_status(body, self._source()))
                return
            if path == "/api/v1/actions":
                device = self._authenticate()
                self._origin()
                body = self._json_body()
                self._json(200, self.sidecar.core.perform_action(device, body))
                return
            if path == "/api/v1/inbox/intents":
                device = self._authenticate()
                self._origin()
                body = self._json_body()
                self._json(201, self.sidecar.core.create_inbox_intent(device, body))
                return
            if path == "/api/v1/inbox/cancel":
                device = self._authenticate()
                self._origin()
                body = self._json_body()
                self._json(200, self.sidecar.core.cancel_inbox_intent(device, body))
                return
            upload_prefix = "/api/v1/inbox/uploads/"
            if path.startswith(upload_prefix):
                device = self._authenticate()
                self._origin()
                upload_id = path[len(upload_prefix):]
                if not upload_id or "/" in upload_id:
                    raise ApiError("bad_request", 404, message="Not found.")
                self._raw_upload(device, upload_id)
                return
            if path == "/api/v1/capabilities/request":
                device = self._authenticate()
                self._origin()
                body = self._json_body()
                self._json(200, self.sidecar.core.request_capabilities(device, body))
                return
            if path == "/api/v1/capabilities/status":
                device = self._authenticate()
                self._origin()
                body = self._json_body()
                self._json(200, self.sidecar.core.capability_status(device, body))
                return
            if path == "/api/v1/session/unpair":
                device = self._authenticate()
                self._origin()
                body = self._json_body()
                if not isinstance(body, dict) or set(body) != {"requestId"}:
                    raise ApiError("bad_request")
                normalize_id(body["requestId"], "request id")
                self._json(200, self.sidecar.core.self_unpair(device))
                return
            if path == "/api/v1/session/identify":
                device = self._authenticate()
                self._origin()
                body = self._json_body()
                self._json(200, self.sidecar.core.bind_client_instance(device, body))
                return
            raise ApiError("bad_request", 404, message="Not found.")
        except ApiError as error:
            self._error(error)
        except ValueError:
            self._error(ApiError("bad_request", 400))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            print(json_safe_log("http-handler-failed", code=type(error).__name__), flush=True)
            self._error(ApiError("service_unavailable", 503))

    def do_OPTIONS(self) -> None:
        self._json(405, {"error": {"code": "bad_request", "message": "Cross-origin requests are not supported.", "retryable": False}})

    def do_PUT(self) -> None:
        self._json(405, {"error": {"code": "bad_request", "message": "Method not allowed.", "retryable": False}})

    do_DELETE = do_PUT
    do_PATCH = do_PUT
    do_TRACE = do_PUT
    do_CONNECT = do_PUT

    def _raw_upload(self, device: dict[str, Any], upload_id: str) -> None:
        if self.headers.get_all("Transfer-Encoding", []) or self.headers.get_all("Content-Range", []) or self.headers.get_all("Trailer", []):
            raise ApiError("bad_request", 400, message="Chunked, ranged, and trailer uploads are not accepted.")
        raw_length = self._one_header("Content-Length")
        if not raw_length or not raw_length.isdigit():
            raise ApiError("bad_request", 411, message="An exact Content-Length is required.")
        length = int(raw_length)
        if length < 1 or length > MAX_INBOX_FILE_BYTES:
            raise ApiError("upload_rejected", 413)
        raw_type = self._one_header("Content-Type")
        if ";" in raw_type or raw_type.strip().lower() != raw_type:
            raise ApiError("upload_rejected", 415, message="Use the exact declared file content type.")
        replay = self.sidecar.core.completed_upload_replay(device, upload_id, length, raw_type)
        if replay is not None:
            self.close_connection = True
            self._json(200, {"receipt": replay, "replay": True}, {"Connection": "close"})
            return
        self.connection.settimeout(BODY_TIMEOUT_SECONDS)
        result = self.sidecar.core.upload_inbox_file(device, upload_id, length, raw_type, self.rfile)
        self._json(201, {"receipt": result.receipt, "replay": result.replay})

    def do_HEAD(self) -> None:
        self.send_response(405)
        self._base_headers()
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _events(self, device: dict[str, Any]) -> None:
        last_event = self._one_header("Last-Event-ID")
        if last_event and (not last_event.isdigit() or len(last_event) > 20):
            raise ApiError("bad_request")
        subscriber = self.sidecar.core.subscribe(device, MAX_STREAMS)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self._base_headers()
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while not subscriber.closed:
                event = subscriber.get(EVENT_HEARTBEAT_SECONDS)
                if event is None:
                    self.wfile.write(f": heartbeat {utc_timestamp()}\n\n".encode("ascii"))
                    self.wfile.flush()
                    continue
                data = canonical_json(event["data"]).decode("utf-8")
                frame = f"id: {event['seq']}\nevent: {event['event']}\ndata: {data}\n\n".encode("utf-8")
                self.wfile.write(frame)
                self.wfile.flush()
                if event["event"] in {"session.revoked", "server.restarting"}:
                    break
        finally:
            self.sidecar.core.events.unsubscribe(subscriber)
            self.close_connection = True

    def _static(self, path: str) -> None:
        mapping = {
            "/app": "index.html",
            "/app/": "index.html",
            "/app/pair": "index.html",
            "/app/share-target": "index.html",
            "/app/app.v1012.css": "app.v1012.css",
            "/app/app.v1012.js": "app.v1012.js",
            "/app/model.v1012.js": "model.v1012.js",
            "/app/sw.v1012.js": "sw.v1012.js",
            "/app/manifest.webmanifest": "manifest.webmanifest",
            "/app/icon.svg": "icon.svg",
            "/app/icon-192.png": "icon-192.png",
            "/app/icon-512.png": "icon-512.png",
        }
        relative = mapping.get(path)
        if relative is None:
            raise ApiError("bad_request", 404, message="Not found.")
        target = (self.sidecar.web_root / relative).resolve()
        if target.parent != self.sidecar.web_root or not target.is_file():
            raise ApiError("service_unavailable", 503)
        body = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            mime = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith(("text/", "application/javascript", "application/manifest")) else mime)
        cache = "public, max-age=31536000, immutable" if ".v1012." in target.name or target.suffix == ".png" else "no-cache"
        self._base_headers(cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Service-Worker-Allowed", "/app/" if target.name.startswith("sw.") else "none")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


def utc_timestamp() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
