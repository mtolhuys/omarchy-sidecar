"""Sidecar pairing, policy, state, events, and typed action boundary."""

from __future__ import annotations

import base64
import collections
import math
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .adapters import AdapterError, FakeAdapter, OmarchyAdapter
from .constants import (
    ACTION_SCOPES,
    ALL_SCOPES,
    DEFAULT_SCOPES,
    HELPER_BUILD_ID,
    MAX_EVENT_QUEUE_BYTES,
    MAX_EVENT_QUEUE_ITEMS,
    MAX_CAPABILITY_REQUESTS,
    MAX_PENDING_REQUESTS,
    MAX_REPLAY_PER_DEVICE,
    MAX_SNAPSHOT_BYTES,
    PAIRING_LIFETIME_SECONDS,
    PENDING_LIFETIME_SECONDS,
    PROTOCOL,
    REQUESTABLE_SCOPES,
    REPLAY_LIFETIME_SECONDS,
    SAFE_ERROR_MESSAGES,
    SCOPE_LABELS,
    SERVICE_BUILD_ID,
    VERSION,
    WEB_BUILD_ID,
    LOOPBACK_HOST,
    LOOPBACK_PORT,
    TAILSCALE_HTTPS_PORT,
    INBOX_SCOPE,
    MAX_INBOX_BATCH_BYTES,
    MAX_INBOX_FILE_BYTES,
    MAX_INBOX_FILES,
)
from .inbox import InboxError, InboxManager, UploadResult
from .security import client_instance_hash, random_id, random_token, verification_phrase
from .routes import RouteError
from .store import DeviceStore
from .util import canonical_json, normalize_id, normalize_name, normalize_platform, require_exact_object, utc_now


class ApiError(RuntimeError):
    def __init__(self, code: str, status: int = 400, details: dict[str, Any] | None = None, message: str | None = None):
        self.code = code
        self.status = status
        self.details = details or {}
        self.message = message or SAFE_ERROR_MESSAGES.get(code, "Sidecar could not complete that request.")
        super().__init__(self.message)

    def envelope(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.code in {"rate_limited", "service_unavailable", "action_failed"}}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = capacity
        self.updated = time.monotonic()

    def take(self, amount: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_second)
        self.updated = now
        if self.tokens < amount:
            return False
        self.tokens -= amount
        return True


class RateLimits:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: collections.OrderedDict[tuple[str, str], TokenBucket] = collections.OrderedDict()

    def allow(self, category: str, key: str, capacity: float, refill: float) -> bool:
        bucket_key = (category, key[:128])
        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                bucket = TokenBucket(capacity, refill)
                self._buckets[bucket_key] = bucket
            self._buckets.move_to_end(bucket_key)
            while len(self._buckets) > 256:
                self._buckets.popitem(last=False)
            return bucket.take()


@dataclass
class Subscriber:
    device_id: str
    events: queue.Queue[dict[str, Any]] = field(default_factory=lambda: queue.Queue(MAX_EVENT_QUEUE_ITEMS))
    bytes_queued: int = 0
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def put(self, event: dict[str, Any]) -> bool:
        with self.lock:
            size = len(canonical_json(event))
            if self.closed or self.events.full() or self.bytes_queued + size > MAX_EVENT_QUEUE_BYTES:
                self.closed = True
                return False
            self.events.put_nowait(event)
            self.bytes_queued += size
            return True

    def replace(self, event: dict[str, Any]) -> bool:
        """Atomically discard stale state and queue one authoritative event."""
        with self.lock:
            if self.closed:
                return False
            while True:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    break
            self.bytes_queued = 0
            size = len(canonical_json(event))
            if size > MAX_EVENT_QUEUE_BYTES:
                self.closed = True
                return False
            self.events.put_nowait(event)
            self.bytes_queued = size
            return True

    def get(self, timeout: float) -> dict[str, Any] | None:
        try:
            event = self.events.get(timeout=timeout)
        except queue.Empty:
            return None
        with self.lock:
            self.bytes_queued = max(0, self.bytes_queued - len(canonical_json(event)))
        return event


class EventHub:
    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: list[Subscriber] = []

    def subscribe(self, device_id: str, maximum: int) -> Subscriber:
        with self._lock:
            active = [subscriber for subscriber in self._subscribers if not subscriber.closed]
            self._subscribers = active
            if len(active) >= maximum:
                raise ApiError("rate_limited", 429, {"retryAfterMs": 3000})
            subscriber = Subscriber(device_id)
            self._subscribers.append(subscriber)
            return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            subscriber.closed = True
            self._subscribers = [item for item in self._subscribers if item is not subscriber]

    def snapshot(self) -> list[Subscriber]:
        with self._lock:
            return [subscriber for subscriber in self._subscribers if not subscriber.closed]

    def revoke(self, device_id: str, sequence: int) -> None:
        with self._lock:
            for subscriber in self._subscribers:
                if subscriber.device_id == device_id and not subscriber.closed:
                    subscriber.replace({"event": "session.revoked", "seq": sequence, "data": {"seq": sequence}})

    def close_all(self, sequence: int) -> None:
        with self._lock:
            for subscriber in self._subscribers:
                if not subscriber.closed:
                    subscriber.replace({"event": "server.restarting", "seq": sequence, "data": {"seq": sequence, "retryAfterMs": 1500}})


class SidecarCore:
    _AUTO_RETRY_CODES = {
        "tailscale-unavailable",
        "tailscale-disconnected",
        "tailscale-https-unavailable",
        # A conflict remains fail-closed: retries perform the same read-only
        # preflight and never overwrite it. Retrying only lets a transient
        # predecessor route disappear cleanly during path-busted updates.
        "tailscale-route-conflict",
        "tailscale-route-failed",
        "tailscale-route-stopped",
    }

    def __init__(
        self,
        store: DeviceStore,
        adapter: FakeAdapter | OmarchyAdapter,
        route: Any,
        service_build_id: str = SERVICE_BUILD_ID,
        *,
        listener_host: str = LOOPBACK_HOST,
        listener_port: int = LOOPBACK_PORT,
        https_port: int = TAILSCALE_HTTPS_PORT,
        inbox: InboxManager | None = None,
    ):
        self.store = store
        self.adapter = adapter
        self.route = route
        self.service_build_id = service_build_id
        self.listener_host = listener_host
        self.listener_port = listener_port
        self.https_port = https_port
        self.inbox = inbox or InboxManager(home=store.root.parent)
        self.boot_id = random_id("boot")
        self.origin = ""
        self.route_error: dict[str, str] | None = None
        self.sequence = 1
        self.started_at = utc_now()
        self._lock = threading.RLock()
        # Actions and policy mutations share one serialization boundary. This
        # makes replay, pause, rescope, and revocation authoritative at the
        # exact point where a desktop mutation can be dispatched.
        self._action_lock = threading.RLock()
        self._stop = threading.Event()
        self._pairing: dict[str, Any] | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._capability_requests: dict[str, dict[str, Any]] = {}
        self._last_private_state: dict[str, Any] = {}
        self._locked = True
        self._lock_known = False
        self._last_lock_poll = 0.0
        self._last_state_poll = 0.0
        self._replay: dict[str, collections.OrderedDict[str, tuple[float, bytes, dict[str, Any]]]] = {}
        self.rates = RateLimits()
        self.events = EventHub()
        self._state_thread = threading.Thread(target=self._state_loop, name="sidecar-state", daemon=True)
        self._route_retry_lock = threading.Lock()
        self._route_retry_delay = 2.0
        self._next_route_retry = 0.0

    def start(self) -> None:
        self._refresh_lock(force=True)
        self._refresh_state(force=True)
        self._state_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.inbox.invalidate_all()
        self.inbox.cleanup_staging()
        with self._lock:
            self.sequence += 1
            self.events.close_all(self.sequence)
        if self._state_thread.is_alive():
            self._state_thread.join(timeout=2)

    def set_origin(self, origin: str) -> None:
        with self._action_lock:
            with self._lock:
                recovered = self.route_error is not None
                self.origin = origin.rstrip("/")
                self.route_error = None
                self._route_retry_delay = 2.0
                self._next_route_retry = 0.0
        if recovered:
            self._advance_and_broadcast()

    def set_route_error(self, code: str, message: str) -> None:
        with self._action_lock:
            self.inbox.invalidate_all()
            with self._lock:
                previous_code = (self.route_error or {}).get("code")
                self.route_error = {"code": code, "message": message}
                if code in self._AUTO_RETRY_CODES and (previous_code != code or self._next_route_retry <= 0):
                    self._route_retry_delay = 2.0
                    self._next_route_retry = time.monotonic() + self._route_retry_delay
        if previous_code != code:
            self._advance_and_broadcast()

    def retry_route(self) -> dict[str, Any]:
        with self._route_retry_lock:
            if self.route_error is None:
                return self.local_status()
            try:
                origin = self.route.start()
                self.set_origin(origin)
            except RouteError as error:
                self.set_route_error(error.code, error.message)
                with self._lock:
                    self._route_retry_delay = min(max(self._route_retry_delay * 2, 5.0), 30.0)
                    self._next_route_retry = time.monotonic() + self._route_retry_delay
            return self.local_status()

    def _retry_route_if_due(self) -> None:
        with self._lock:
            error_code = (self.route_error or {}).get("code", "")
            due = self._next_route_retry > 0 and time.monotonic() >= self._next_route_retry
        if error_code in self._AUTO_RETRY_CODES and due:
            self.retry_route()

    def _state_loop(self) -> None:
        while not self._stop.wait(0.2):
            self._retry_route_if_due()
            lock_changed = self._refresh_lock()
            if lock_changed:
                with self._action_lock:
                    self._refresh_lock(force=True)
                    with self._lock:
                        fail_closed = not self._lock_known or self._locked
                    if fail_closed:
                        self.inbox.invalidate_all()
            state_changed = self._refresh_state()
            if lock_changed or state_changed:
                self._advance_and_broadcast()
            self._expire_pairing()

    def _refresh_lock(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._last_lock_poll < 0.2:
            return False
        known, locked = self.adapter.lock_state()
        with self._lock:
            changed = known != self._lock_known or locked != self._locked
            self._lock_known = known
            self._locked = locked if known else True
            self._last_lock_poll = now
            return changed

    def _refresh_state(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._last_state_poll < 1.0:
            return False
        try:
            state = self.adapter.snapshot()
        except Exception:
            state = {"theme": {}, "themes": {"items": [], "currentId": "", "currentName": ""}, "workspaces": [], "windows": [], "media": {"available": False}, "controls": {}, "capabilities": {}}
        with self._lock:
            changed = canonical_json(state) != canonical_json(self._last_private_state)
            self._last_private_state = state
            self._last_state_poll = now
            return changed

    def _advance_and_broadcast(self, *, advance: bool = True) -> None:
        with self._lock:
            if advance:
                self.sequence += 1
            sequence = self.sequence
        for subscriber in self.events.snapshot():
            device = next((item for item in self.store.list_devices() if item["id"] == subscriber.device_id), None)
            if device is None:
                continue
            snapshot = self.snapshot(device)
            # State events are complete per-device snapshots. Coalescing them
            # prevents a slow client from receiving pre-lock, pre-pause, or
            # pre-rescope detail after the security boundary has changed.
            subscriber.replace({"event": "snapshot", "seq": sequence, "data": snapshot})

    def _expire_pairing(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._pairing is not None and now >= self._pairing["deadline"]:
                self._pairing = None
                self.sequence += 1
            expired = [request_id for request_id, request in self._pending.items() if now >= request["deadline"]]
            for request_id in expired:
                self._pending[request_id]["status"] = "expired"
            removable = [
                request_id for request_id, request in self._pending.items()
                if request["status"] in {"expired", "denied", "delivered"}
                and now >= request["deadline"] + 60
            ]
            for request_id in removable:
                del self._pending[request_id]
            for request in self._capability_requests.values():
                if request["status"] == "pending" and now >= request["deadline"]:
                    request["status"] = "expired"
            capability_removable = [
                request_id for request_id, request in self._capability_requests.items()
                if request["status"] in {"approved", "denied", "expired"}
                and now >= request["deadline"] + 60
            ]
            for request_id in capability_removable:
                del self._capability_requests[request_id]

    def _qr_data(self, url: str) -> str:
        executable = shutil.which("qrencode")
        if executable is None:
            raise ApiError("service_unavailable", 503, message="QR generation is unavailable. Install qrencode and reopen Sidecar.")
        try:
            result = subprocess.run(
                [executable, "-t", "SVG", "-o", "-", "-s", "7", "-m", "2"],
                input=url.encode("utf-8"), capture_output=True, timeout=3, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ApiError("service_unavailable", 503, message="Sidecar could not create the pairing QR code.") from error
        if result.returncode != 0 or not result.stdout.startswith(b"<?xml") or len(result.stdout) > 256 * 1024:
            raise ApiError("service_unavailable", 503, message="Sidecar could not create the pairing QR code.")
        return "data:image/svg+xml;base64," + base64.b64encode(result.stdout).decode("ascii")

    def open_pairing(self) -> dict[str, Any]:
        with self._lock:
            if not self.origin or self.route_error is not None:
                raise ApiError("service_unavailable", 503, message=(self.route_error or {}).get("message", "Tailscale is not ready for pairing."))
            secret = random_token(32)
            expires_at = (datetime.now(UTC) + timedelta(seconds=PAIRING_LIFETIME_SECONDS)).isoformat(timespec="seconds").replace("+00:00", "Z")
            pair_url = f"{self.origin}/app/pair#{secret}"
            qr_data = self._qr_data(pair_url)
            self._pairing = {"secret": secret, "deadline": time.monotonic() + PAIRING_LIFETIME_SECONDS, "expiresAt": expires_at, "qrDataUrl": qr_data}
            self.sequence += 1
            return {"pairUrl": pair_url, "qrDataUrl": qr_data, "expiresAt": expires_at, "endpoint": self.origin}

    def cancel_pairing(self) -> None:
        with self._lock:
            self._pairing = None
            for request in self._pending.values():
                if request["status"] == "pending":
                    request["status"] = "denied"
            self.sequence += 1

    def pairing_status_local(self) -> dict[str, Any] | None:
        with self._lock:
            if self._pairing is None:
                return None
            remaining = max(0, int(self._pairing["deadline"] - time.monotonic()))
            return {"active": True, "expiresAt": self._pairing["expiresAt"], "remainingSeconds": remaining, "qrDataUrl": self._pairing["qrDataUrl"], "endpoint": self.origin}

    def pair_request(self, body: Any, source: str) -> dict[str, Any]:
        self._expire_pairing()
        if not self.rates.allow("pair", source, 6, 0.1):
            raise ApiError("rate_limited", 429, {"retryAfterMs": 5000})
        request = require_exact_object(body, {"secret", "device"})
        device_raw = require_exact_object(
            request["device"],
            {"name", "platform", "clientVersion", "protocol"},
            {"clientInstanceId"},
        )
        if device_raw["protocol"] != PROTOCOL:
            raise ApiError("protocol_mismatch", 409)
        name = normalize_name(device_raw["name"])
        platform = normalize_platform(device_raw["platform"])
        client_version = device_raw["clientVersion"]
        if not isinstance(client_version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,31}", client_version):
            raise ApiError("bad_request")
        try:
            instance_hash = client_instance_hash(device_raw["clientInstanceId"]) if "clientInstanceId" in device_raw else None
        except ValueError as error:
            raise ApiError("bad_request") from error
        with self._lock:
            pairing = self._pairing
            if pairing is None or time.monotonic() >= pairing["deadline"]:
                self._pairing = None
                raise ApiError("pairing_expired", 410)
            supplied = request["secret"]
            if not isinstance(supplied, str) or not secrets.compare_digest(supplied, pairing["secret"]):
                raise ApiError("pairing_expired", 410)
            if len([item for item in self._pending.values() if item["status"] == "pending"]) >= MAX_PENDING_REQUESTS:
                raise ApiError("rate_limited", 429, {"retryAfterMs": 5000})
            request_id = random_id("pairreq")
            capability = random_token(32)
            transcript = {
                "requestId": request_id,
                "name": name,
                "platform": platform,
                "clientVersion": client_version,
                "protocol": PROTOCOL,
                "clientInstanceHash": instance_hash,
            }
            phrase = verification_phrase(pairing["secret"], transcript)
            expires_at = (datetime.now(UTC) + timedelta(seconds=PENDING_LIFETIME_SECONDS)).isoformat(timespec="seconds").replace("+00:00", "Z")
            self._pending[request_id] = {
                "id": request_id, "capability": capability, "name": name, "platform": platform,
                "clientVersion": client_version, "clientInstanceHash": instance_hash,
                "phrase": phrase, "status": "pending",
                "requestedAt": utc_now(),
                "deadline": time.monotonic() + PENDING_LIFETIME_SECONDS, "expiresAt": expires_at,
                "credential": None, "device": None,
            }
            self._pairing = None
            self.sequence += 1
            return {"requestId": request_id, "pendingCapability": capability, "expiresAt": expires_at, "verificationPhrase": phrase, "pollAfterMs": 1000}

    def pair_status(self, body: Any, source: str) -> dict[str, Any]:
        if not self.rates.allow("pair-status", source, 12, 1.0):
            raise ApiError("rate_limited", 429, {"retryAfterMs": 1200})
        request = require_exact_object(body, {"requestId", "pendingCapability"})
        request_id = normalize_id(request["requestId"], "request id")
        capability = request["pendingCapability"]
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or not isinstance(capability, str) or not secrets.compare_digest(capability, pending["capability"]):
                raise ApiError("pairing_expired", 410)
            if time.monotonic() >= pending["deadline"]:
                pending["status"] = "expired"
            if pending["status"] == "pending":
                return {"status": "pending", "expiresAt": pending["expiresAt"], "pollAfterMs": 1200}
            if pending["status"] == "approved" and pending["credential"]:
                response = {
                    "status": "approved", "device": pending["device"], "credential": pending["credential"],
                    "server": {"protocol": PROTOCOL, "buildId": HELPER_BUILD_ID},
                }
                pending["credential"] = None
                pending["capability"] = ""
                pending["status"] = "delivered"
                return response
            if pending["status"] == "denied":
                pending["capability"] = ""
                raise ApiError("pairing_denied", 403)
            raise ApiError("pairing_expired", 410)

    def pending_local(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"id": item["id"], "name": item["name"], "platform": item["platform"], "verificationPhrase": item["phrase"], "requestedAt": item["requestedAt"], "expiresAt": item["expiresAt"], "proposedScopes": list(DEFAULT_SCOPES)}
                for item in self._pending.values() if item["status"] == "pending"
            ]

    def request_capabilities(self, device: dict[str, Any], body: Any) -> dict[str, Any]:
        request = require_exact_object(body, {"requestId", "scopes"})
        request_id = normalize_id(request["requestId"], "request id")
        scopes = request["scopes"]
        if not isinstance(scopes, list) or not 1 <= len(scopes) <= len(REQUESTABLE_SCOPES):
            raise ApiError("bad_request")
        normalized = list(dict.fromkeys(scopes))
        if len(normalized) != len(scopes) or any(scope not in REQUESTABLE_SCOPES for scope in normalized):
            raise ApiError("bad_request")
        with self._action_lock:
            current = self.store.get_device(device["id"])
            if current is None:
                raise ApiError("session_revoked", 401)
            missing = [scope for scope in normalized if scope not in current["scopes"]]
            if not missing:
                return {"status": "approved", "requestId": request_id, "scopes": normalized}
            if not self.rates.allow("capability-request", current["id"], 2, 1 / 30):
                raise ApiError("rate_limited", 429, {"retryAfterMs": 30000})
            with self._lock:
                existing = self._capability_requests.get(request_id)
                if existing is not None:
                    if existing["deviceId"] != current["id"] or existing["scopes"] != normalized:
                        raise ApiError("bad_request")
                    return {"status": existing["status"], "requestId": request_id, "scopes": normalized,
                            "expiresAt": existing["expiresAt"], "pollAfterMs": 1200}
                pending_for_device = next((item for item in self._capability_requests.values()
                                           if item["deviceId"] == current["id"] and item["status"] == "pending"), None)
                if pending_for_device is not None:
                    return {"status": "pending", "requestId": pending_for_device["id"],
                            "scopes": pending_for_device["scopes"], "expiresAt": pending_for_device["expiresAt"],
                            "pollAfterMs": 1200}
                if len([item for item in self._capability_requests.values() if item["status"] == "pending"]) >= MAX_CAPABILITY_REQUESTS:
                    raise ApiError("rate_limited", 429, {"retryAfterMs": 5000})
                expires_at = (datetime.now(UTC) + timedelta(seconds=PENDING_LIFETIME_SECONDS)).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._capability_requests[request_id] = {
                    "id": request_id, "deviceId": current["id"], "deviceName": current["name"],
                    "scopes": normalized, "status": "pending", "requestedAt": utc_now(),
                    "deadline": time.monotonic() + PENDING_LIFETIME_SECONDS, "expiresAt": expires_at,
                }
                self.sequence += 1
                return {"status": "pending", "requestId": request_id, "scopes": normalized,
                        "expiresAt": expires_at, "pollAfterMs": 1200}

    def capability_status(self, device: dict[str, Any], body: Any) -> dict[str, Any]:
        request = require_exact_object(body, {"requestId"})
        request_id = normalize_id(request["requestId"], "request id")
        self._expire_pairing()
        with self._action_lock:
            current = self.store.get_device(device["id"])
            if current is None:
                raise ApiError("session_revoked", 401)
            with self._lock:
                pending = self._capability_requests.get(request_id)
                if pending is None or pending["deviceId"] != current["id"]:
                    raise ApiError("bad_request", 404)
                status = pending["status"]
                if status == "denied":
                    raise ApiError("capability_denied", 403)
                if status == "expired":
                    raise ApiError("capability_denied", 410, message="That capability request expired. Ask again from this phone.")
                return {"status": status, "requestId": request_id, "scopes": pending["scopes"],
                        "expiresAt": pending["expiresAt"], "pollAfterMs": 1200}

    def capability_requests_local(self) -> list[dict[str, Any]]:
        self._expire_pairing()
        with self._lock:
            return [
                {"id": item["id"], "deviceId": item["deviceId"], "deviceName": item["deviceName"],
                 "scopes": list(item["scopes"]), "labels": [SCOPE_LABELS[scope] for scope in item["scopes"]],
                 "requestedAt": item["requestedAt"], "expiresAt": item["expiresAt"]}
                for item in self._capability_requests.values() if item["status"] == "pending"
            ]

    def approve_capabilities(self, request_id: str) -> dict[str, Any]:
        normalize_id(request_id, "request id")
        self._refresh_lock(force=True)
        if not self._lock_known or self._locked:
            raise ValueError("unlock the desktop before approving phone capabilities")
        with self._action_lock:
            with self._lock:
                pending = self._capability_requests.get(request_id)
                if pending is None or pending["status"] != "pending" or time.monotonic() >= pending["deadline"]:
                    raise KeyError("capability request is no longer pending")
                device = self.store.get_device(pending["deviceId"])
                if device is None:
                    raise KeyError("device is no longer paired")
                scopes = list(dict.fromkeys([*device["scopes"], *pending["scopes"]]))
                updated = self.store.rescope(device["id"], scopes)
                pending["status"] = "approved"
                self.sequence += 1
        self._advance_and_broadcast(advance=False)
        return updated

    def deny_capabilities(self, request_id: str) -> None:
        normalize_id(request_id, "request id")
        with self._lock:
            pending = self._capability_requests.get(request_id)
            if pending is None or pending["status"] != "pending":
                raise KeyError("capability request is no longer pending")
            pending["status"] = "denied"
            self.sequence += 1

    def approve(self, request_id: str, scopes: list[str]) -> dict[str, Any]:
        normalize_id(request_id, "request id")
        normalized_scopes = list(dict.fromkeys(scopes))
        # Retired permission strings remain loadable so an existing durable
        # record is never corrupted. No active action maps to them and the
        # phone cannot request them as a capability upgrade.
        if any(scope not in ALL_SCOPES for scope in normalized_scopes):
            raise ValueError("unknown scope")
        self._refresh_lock(force=True)
        if not self._lock_known or self._locked:
            raise ValueError("unlock the desktop before approving a phone")
        replaced_ids: list[str] = []
        with self._action_lock:
            with self._lock:
                pending = self._pending.get(request_id)
                if pending is None or pending["status"] != "pending" or time.monotonic() >= pending["deadline"]:
                    raise KeyError("pairing request is no longer pending")
                device, credential, replaced_ids = self.store.create_or_replace_device(
                    pending["name"], pending["platform"], normalized_scopes,
                    pending["clientInstanceHash"],
                )
                for replaced_id in replaced_ids:
                    self.inbox.invalidate_device(replaced_id)
                    self._replay.pop(replaced_id, None)
                    for request in self._capability_requests.values():
                        if request["deviceId"] == replaced_id and request["status"] == "pending":
                            request["status"] = "denied"
                pending["device"] = {"id": device["id"], "name": device["name"], "scopes": device["scopes"]}
                pending["credential"] = credential
                pending["status"] = "approved"
                self.sequence += 1
                sequence = self.sequence
        for replaced_id in replaced_ids:
            self.events.revoke(replaced_id, sequence)
        return device

    def bind_client_instance(self, device: dict[str, Any], body: Any) -> dict[str, Any]:
        request = require_exact_object(body, {"clientInstanceId"})
        try:
            instance_hash = client_instance_hash(request["clientInstanceId"])
        except ValueError as error:
            raise ApiError("bad_request") from error
        with self._action_lock:
            current = self.store.get_device(device["id"])
            if current is None:
                raise ApiError("session_revoked", 401)
            try:
                winner, removed_ids, current_won, changed = self.store.bind_client_instance(device["id"], instance_hash)
            except KeyError as error:
                raise ApiError("session_revoked", 401) from error
            except ValueError as error:
                raise ApiError("bad_request") from error
            for removed_id in removed_ids:
                self.inbox.invalidate_device(removed_id)
                self._replay.pop(removed_id, None)
                for pending in self._capability_requests.values():
                    if pending["deviceId"] == removed_id and pending["status"] == "pending":
                        pending["status"] = "denied"
            with self._lock:
                if changed:
                    self.sequence += 1
                sequence = self.sequence
        for removed_id in removed_ids:
            self.events.revoke(removed_id, sequence)
        if not current_won:
            raise ApiError("session_revoked", 401)
        if changed:
            self._advance_and_broadcast(advance=False)
        return {"status": "bound", "deviceId": winner["id"], "replacedPairings": len(removed_ids)}

    def deny(self, request_id: str) -> None:
        normalize_id(request_id, "request id")
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending["status"] != "pending":
                raise KeyError("pairing request is no longer pending")
            pending["status"] = "denied"
            self.sequence += 1

    def authenticate(self, credential: str, source: str = "local") -> dict[str, Any]:
        if not self.rates.allow("authenticate", source, 20, 5):
            raise ApiError("rate_limited", 429, {"retryAfterMs": 1000})
        device = self.store.authenticate(credential)
        if device is None:
            raise ApiError("not_authenticated", 401)
        return device

    def _minimal_snapshot(self, device: dict[str, Any], paused: bool, locked: bool, available: bool = True) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "seq": self.sequence,
            "server": {"bootId": self.boot_id, "buildId": HELPER_BUILD_ID, "webBuildId": WEB_BUILD_ID, "time": utc_now()},
            "session": {"deviceId": device["id"], "permissions": [], "paused": paused},
            "desktop": {"locked": locked, "available": available},
        }

    def snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        with self._action_lock:
            current = self.store.get_device(device["id"])
            if current is None:
                raise ApiError("session_revoked", 401)
            self._refresh_lock(force=True)
            with self._lock:
                paused = self.store.paused
                locked = self._locked or not self._lock_known
                available = self.route_error is None
                if paused or locked or not available:
                    return self._minimal_snapshot(current, paused, locked, available)
                scopes = list(current["scopes"])
            state = self._last_private_state if "read:desktop" in scopes else {}
            snapshot: dict[str, Any] = {
                "protocol": PROTOCOL,
                "seq": self.sequence,
                "server": {"bootId": self.boot_id, "buildId": HELPER_BUILD_ID, "webBuildId": WEB_BUILD_ID, "time": utc_now()},
                "session": {"deviceId": current["id"], "permissions": scopes, "paused": False},
                "desktop": {"locked": False, "available": True},
            }
            if state:
                snapshot.update({
                    "theme": state.get("theme", {}),
                    "themes": state.get("themes", {"items": [], "currentId": "", "currentName": ""}),
                    "workspaces": state.get("workspaces", []),
                    "windows": state.get("windows", []),
                    "media": state.get("media", {"available": False}),
                    "controls": state.get("controls", {}),
                    "capabilities": state.get("capabilities", {}),
                })
                snapshot["beam"] = self._beam(snapshot)
            snapshot["inbox"] = {
                "destination": "Sidecar Inbox",
                "scope": INBOX_SCOPE,
                "limits": {"files": MAX_INBOX_FILES, "fileBytes": MAX_INBOX_FILE_BYTES, "batchBytes": MAX_INBOX_BATCH_BYTES},
            }
            if len(canonical_json(snapshot)) > MAX_SNAPSHOT_BYTES:
                raise ApiError("service_unavailable", 503)
            return snapshot

    def theme_preview(self, device: dict[str, Any], theme_id: str) -> tuple[bytes, str]:
        """Return one bounded installed-theme image under current phone policy."""
        normalize_id(theme_id, "theme id")
        with self._action_lock:
            current_device = self.store.get_device(device["id"])
            if current_device is None:
                raise ApiError("session_revoked", 401)
            self._refresh_lock(force=True)
            with self._lock:
                route_unavailable = self.route_error is not None
                lock_known = self._lock_known
                locked = self._locked
            if route_unavailable:
                raise ApiError("service_unavailable", 503)
            if self.store.paused:
                raise ApiError("sidecar_paused", 423)
            if not lock_known or locked:
                raise ApiError("desktop_locked", 423)
            for required_scope in ("read:desktop", "control:theme"):
                if required_scope not in current_device["scopes"]:
                    raise ApiError("permission_denied", 403, {"requiredScope": required_scope})
            if not self.rates.allow("theme-preview", device["id"], 12, 6.0):
                raise ApiError("rate_limited", 429, {"retryAfterMs": 1000})
            try:
                body, mime = self.adapter.theme_preview(theme_id)
            except AdapterError as error:
                raise ApiError("theme_unavailable", 404) from error
            # Pause, rescope, and revoke share this serialization boundary.
            # Recheck external lock state before any image bytes leave it.
            self._refresh_lock(force=True)
            current_device = self.store.get_device(device["id"])
            if current_device is None:
                raise ApiError("session_revoked", 401)
            if self.store.paused:
                raise ApiError("sidecar_paused", 423)
            with self._lock:
                if self.route_error is not None:
                    raise ApiError("service_unavailable", 503)
                if not self._lock_known or self._locked:
                    raise ApiError("desktop_locked", 423)
            for required_scope in ("read:desktop", "control:theme"):
                if required_scope not in current_device["scopes"]:
                    raise ApiError("permission_denied", 403, {"requiredScope": required_scope})
            return body, mime

    def _beam(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """Build one fixed, bounded provider surface from typed state only."""
        media = snapshot.get("media")
        if not isinstance(media, dict) or not media.get("available"):
            return None
        actions = [
            {"id": "previous", "label": "Previous", "action": "media.previous", "enabled": bool(media.get("canPrevious"))},
            {"id": "playPause", "label": "Pause" if media.get("playing") else "Play", "action": "media.playPause", "enabled": True},
            {"id": "next", "label": "Next", "action": "media.next", "enabled": bool(media.get("canNext"))},
        ]
        return {"provider": "media.transport", "kind": "typed-actions", "label": "Media nearby",
                "status": "Playing" if media.get("playing") else "Ready", "actions": actions}

    def _validate_action(self, action: str, parameters: Any) -> dict[str, Any]:
        if not isinstance(parameters, dict):
            raise ApiError("bad_request")
        no_parameters = {"media.playPause", "media.previous", "media.next", "theme.backgroundNext", "desktop.lock"}
        if action in no_parameters:
            require_exact_object(parameters, set())
        elif action == "workspace.focus":
            require_exact_object(parameters, {"workspaceId"})
            normalize_id(parameters["workspaceId"], "workspace id")
        elif action == "window.focus":
            require_exact_object(parameters, {"windowId"})
            normalize_id(parameters["windowId"], "window id")
        elif action == "window.moveToWorkspace":
            require_exact_object(parameters, {"windowId", "workspaceId"})
            normalize_id(parameters["windowId"], "window id")
            normalize_id(parameters["workspaceId"], "workspace id")
        elif action == "theme.set":
            require_exact_object(parameters, {"themeId"})
            normalize_id(parameters["themeId"], "theme id")
        elif action == "media.setVolume":
            require_exact_object(parameters, {"volume"})
            volume = parameters["volume"]
            if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0 or volume > 1:
                raise ApiError("bad_request")
            parameters = {"volume": round(float(volume), 3)}
        else:
            raise ApiError("bad_request")
        return parameters

    def perform_action(self, device: dict[str, Any], body: Any) -> dict[str, Any]:
        request = require_exact_object(body, {"requestId", "action", "parameters"}, {"expectedSeq"})
        request_id = normalize_id(request["requestId"], "request id")
        action = request["action"]
        if not isinstance(action, str) or action not in ACTION_SCOPES:
            raise ApiError("bad_request")
        fingerprint = canonical_json(request)
        with self._action_lock:
            current_device = self.store.get_device(device["id"])
            if current_device is None:
                raise ApiError("session_revoked", 401)
            self._refresh_lock(force=True)
            with self._lock:
                route_unavailable = self.route_error is not None
                lock_known = self._lock_known
                locked = self._locked
            if route_unavailable:
                raise ApiError("service_unavailable", 503)
            if self.store.paused:
                raise ApiError("sidecar_paused", 423)
            if not lock_known or locked:
                raise ApiError("desktop_locked", 423)
            required_scope = ACTION_SCOPES[action]
            if required_scope not in current_device["scopes"]:
                raise ApiError("permission_denied", 403, {"requiredScope": required_scope})
            replay = self._replay.setdefault(device["id"], collections.OrderedDict())
            now = time.monotonic()
            while replay and now - next(iter(replay.values()))[0] > REPLAY_LIFETIME_SECONDS:
                replay.popitem(last=False)
            if request_id in replay:
                _created, prior_fingerprint, prior_result = replay[request_id]
                if prior_fingerprint != fingerprint:
                    raise ApiError("bad_request")
                return prior_result
            while len(replay) >= MAX_REPLAY_PER_DEVICE:
                replay.popitem(last=False)
            if not self.rates.allow("action", device["id"], 30, 10):
                raise ApiError("rate_limited", 429, {"retryAfterMs": 1000})
            if action.startswith("theme.") and not self.rates.allow("theme-action", device["id"], 1, 1.0):
                raise ApiError("rate_limited", 429, {"retryAfterMs": 1000})
            parameters = self._validate_action(action, request["parameters"])
            if "expectedSeq" in request and (isinstance(request["expectedSeq"], bool) or not isinstance(request["expectedSeq"], int) or request["expectedSeq"] < 0):
                raise ApiError("bad_request")
            self._refresh_state(force=True)
            self._refresh_lock(force=True)
            if self.store.paused:
                raise ApiError("sidecar_paused", 423)
            with self._lock:
                if self.route_error is not None:
                    raise ApiError("service_unavailable", 503)
                if not self._lock_known or self._locked:
                    raise ApiError("desktop_locked", 423)
            # Re-read policy after the relatively expensive adapter refreshes.
            # A stale authenticated device copy never authorizes dispatch.
            current_device = self.store.get_device(device["id"])
            if current_device is None:
                raise ApiError("session_revoked", 401)
            if required_scope not in current_device["scopes"]:
                raise ApiError("permission_denied", 403, {"requiredScope": required_scope})
            try:
                result_data = self.adapter.perform(action, parameters)
            except AdapterError as error:
                code = str(error) if str(error) in {"target_unavailable", "action_failed"} else "target_unavailable"
                raise ApiError(code, 409 if code == "target_unavailable" else 502) from error
            self._refresh_state(force=True)
            with self._lock:
                self.sequence += 1
                result = {"requestId": request_id, "status": "completed", "seq": self.sequence, "result": result_data}
                replay[request_id] = (now, fingerprint, result)
        self._advance_and_broadcast(advance=False)
        return result

    def revoke(self, device_id: str) -> dict[str, Any]:
        with self._action_lock:
            self.inbox.invalidate_device(device_id)
            device = self.store.revoke(device_id)
            with self._lock:
                self._replay.pop(device_id, None)
                for request in self._capability_requests.values():
                    if request["deviceId"] == device_id and request["status"] == "pending":
                        request["status"] = "denied"
                self.sequence += 1
                sequence = self.sequence
        self.events.revoke(device_id, sequence)
        return device

    def rescope_device(self, device_id: str, scopes: list[str]) -> dict[str, Any]:
        with self._action_lock:
            if INBOX_SCOPE not in scopes:
                self.inbox.invalidate_device(device_id)
            device = self.store.rescope(device_id, scopes)
        self._advance_and_broadcast()
        return device

    def set_paused(self, paused: bool) -> dict[str, Any]:
        with self._action_lock:
            if paused:
                self.inbox.invalidate_all()
            self.store.set_paused(paused)
        self._advance_and_broadcast()
        return self.local_status()

    def self_unpair(self, device: dict[str, Any]) -> dict[str, Any]:
        with self._action_lock:
            if self.store.get_device(device["id"]) is None:
                raise ApiError("session_revoked", 401)
            self.revoke(device["id"])
        return {"status": "revoked"}

    def subscribe(self, device: dict[str, Any], maximum: int) -> Subscriber:
        with self._action_lock:
            current = self.store.get_device(device["id"])
            if current is None:
                raise ApiError("session_revoked", 401)
            subscriber = self.events.subscribe(current["id"], maximum)
            try:
                subscriber.put({"event": "snapshot", "seq": self.sequence, "data": self.snapshot(current)})
                return subscriber
            except Exception:
                self.events.unsubscribe(subscriber)
                raise

    def local_status(self) -> dict[str, Any]:
        route_state = getattr(self.route, "state", "unavailable")
        with self._lock:
            active_device_ids = {subscriber.device_id for subscriber in self.events.snapshot()}
            devices = [
                dict(device, online=device["id"] in active_device_ids)
                for device in self.store.list_devices()
            ]
            return {
                "version": VERSION,
                "protocol": PROTOCOL,
                "builds": {"service": self.service_build_id, "helper": HELPER_BUILD_ID, "web": WEB_BUILD_ID},
                "bootId": self.boot_id,
                "state": "paused" if self.store.paused else ("unavailable" if self.route_error else "ready"),
                "paused": self.store.paused,
                "locked": self._locked or not self._lock_known,
                "endpoint": self.origin,
                "route": {"state": route_state, "childPid": self.route.child_pid(), "error": self.route_error},
                "pairing": self.pairing_status_local(),
                "pending": self.pending_local(),
                "capabilityRequests": self.capability_requests_local(),
                "inbox": self.inbox.status(),
                "devices": devices,
                "deviceCount": len(devices),
                "onlineCount": len(active_device_ids),
                "adapter": type(self.adapter).__name__,
                "lastError": self.route_error,
                "securityReceipt": self.security_receipt(),
            }

    def security_receipt(self) -> dict[str, Any]:
        uid = os.geteuid()
        listener = f"{self.listener_host}:{self.listener_port}"
        loopback_only = self.listener_host == LOOPBACK_HOST
        non_root = uid != 0
        route_target = f"http://{LOOPBACK_HOST}:{self.listener_port}"
        route_endpoint = self.origin or f"https://<this-device>.ts.net:{self.https_port}"
        failures: list[str] = []
        if not non_root:
            failures.append("helper-is-root")
        if not loopback_only:
            failures.append("listener-is-not-loopback")
        if self.route_error is not None:
            failures.append(str(self.route_error.get("code", "route-unavailable")))
        if failures:
            if "helper-is-root" in failures:
                recovery = "Stop Sidecar and start it from the non-root Omarchy session."
            elif "listener-is-not-loopback" in failures:
                recovery = f"Disable Sidecar. Restore the listener to {LOOPBACK_HOST}:{self.listener_port}, verify the artifact, then re-enable it."
            else:
                recovery = (self.route_error or {}).get("message", "Disable Sidecar, resolve the Tailscale route conflict yourself, then re-enable it.")
        else:
            recovery = "All locally checkable security invariants hold."
        return {
            "healthy": not failures,
            "uid": uid,
            "nonRoot": non_root,
            "listener": listener,
            "loopbackOnly": loopback_only,
            "existingTailnetOnly": True,
            "tailscaleSsh": "not used or changed",
            "systemMutation": "no package, service, SSH, firewall, sudoers, or SUID mutation",
            "serveRoute": f"{route_endpoint} -> {route_target}",
            "serveTarget": route_target,
            "transport": "Tailscale supplies private transport.",
            "authorization": "Sidecar device credentials and scopes authorize phones.",
            "invariantFailures": failures,
            "recovery": recovery,
        }

    def diagnostics(self) -> dict[str, Any]:
        status = self.local_status()
        return {
            "version": status["version"], "protocol": status["protocol"], "builds": status["builds"],
            "state": status["state"], "routeState": status["route"]["state"],
            "deviceCount": status["deviceCount"], "onlineCount": status["onlineCount"],
            "adapter": status["adapter"], "store": self.store.diagnostics(),
            "errorCode": (status["lastError"] or {}).get("code", ""),
            "securityReceipt": status["securityReceipt"],
        }

    def _inbox_policy(self, device_id: str) -> dict[str, Any]:
        with self._action_lock:
            device = self.store.get_device(device_id)
            if device is None:
                raise ApiError("session_revoked", 401)
            self._refresh_lock()
            if self.store.paused:
                raise ApiError("sidecar_paused", 423)
            with self._lock:
                if self.route_error is not None:
                    raise ApiError("service_unavailable", 503)
                if not self._lock_known or self._locked:
                    raise ApiError("desktop_locked", 423)
            if INBOX_SCOPE not in device["scopes"]:
                raise ApiError("permission_denied", 403, {"requiredScope": INBOX_SCOPE})
            return device

    @staticmethod
    def _translate_inbox_error(error: InboxError) -> ApiError:
        return ApiError(error.code, error.status, message=error.message or None)

    def create_inbox_intent(self, device: dict[str, Any], body: Any) -> dict[str, Any]:
        with self._action_lock:
            current = self._inbox_policy(device["id"])
            try:
                return self.inbox.create_intent(current["id"], body)
            except InboxError as error:
                raise self._translate_inbox_error(error) from error

    def cancel_inbox_intent(self, device: dict[str, Any], body: Any) -> dict[str, Any]:
        try:
            return self.inbox.cancel(device["id"], body)
        except InboxError as error:
            raise self._translate_inbox_error(error) from error

    def completed_upload_replay(self, device: dict[str, Any], upload_id: str, length: int, media_type: str) -> dict[str, Any] | None:
        self._inbox_policy(device["id"])
        try:
            return self.inbox.completed_replay(device["id"], upload_id, length, media_type)
        except InboxError as error:
            raise self._translate_inbox_error(error) from error

    def upload_inbox_file(self, device: dict[str, Any], upload_id: str, length: int, media_type: str, body: Any) -> UploadResult:
        self._inbox_policy(device["id"])
        def commit_guard(commit: Any) -> dict[str, Any]:
            with self._action_lock:
                self._inbox_policy(device["id"])
                return commit()
        try:
            result = self.inbox.upload(
                device["id"], upload_id, length, media_type, body,
                lambda: self._inbox_policy(device["id"]),
                commit_guard,
            )
        except InboxError as error:
            raise self._translate_inbox_error(error) from error
        self._advance_and_broadcast()
        return result

    def open_inbox_local(self) -> dict[str, Any]:
        return self.inbox.open_inbox()
