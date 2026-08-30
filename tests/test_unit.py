from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from sidecar.adapters import AdapterError, FakeAdapter, OmarchyAdapter
from sidecar import app as sidecar_app
from sidecar.constants import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    MAX_EVENT_QUEUE_BYTES,
    MAX_THEME_PREVIEW_BYTES,
    REQUESTABLE_SCOPES,
    RETIRED_SCOPES,
)
from sidecar.core import ApiError, RateLimits, SidecarCore, Subscriber
from sidecar.routes import FakeRouteController, RouteError, TailscaleRouteController, _contains_exact_value, _contains_port
from sidecar.security import create_verifier, new_credential, verification_phrase, verify_credential
from sidecar.store import DeviceStore
from sidecar.util import (
    DuplicateKeyError,
    json_safe_log,
    normalize_name,
    sanitized_color,
    strict_json_loads,
)


PROJECT = Path(__file__).resolve().parents[1]


class CoreFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = DeviceStore(Path(self.temporary.name) / "state")
        self.adapter = FakeAdapter()
        self.route = FakeRouteController(port=47991)
        self.core = SidecarCore(self.store, self.adapter, self.route)
        self.core.set_origin(self.route.start())
        self.core._qr_data = lambda url: "data:image/svg+xml;base64,PHN2Zy8+"  # type: ignore[method-assign]
        self.core._refresh_lock(force=True)
        self.core._refresh_state(force=True)

    def close(self) -> None:
        self.temporary.cleanup()

    def request_pair(self, name: str = "Test Phone") -> tuple[dict, dict]:
        opened = self.core.open_pairing()
        secret = opened["pairUrl"].split("#", 1)[1]
        pending = self.core.pair_request(
            {
                "secret": secret,
                "device": {
                    "name": name,
                    "platform": "android-web",
                    "clientVersion": "0.2.1",
                    "protocol": 1,
                },
            },
            "client",
        )
        return opened, pending

    def pair(self, scopes: list[str] | None = None) -> tuple[dict, str]:
        _, pending = self.request_pair()
        self.core.approve(pending["requestId"], scopes or list(DEFAULT_SCOPES))
        delivered = self.core.pair_status(
            {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
            "client",
        )
        return self.core.authenticate(delivered["credential"]), delivered["credential"]


class StrictJsonTests(unittest.TestCase):
    def test_rejects_duplicate_nonfinite_surrogate_and_depth(self) -> None:
        with self.assertRaises(DuplicateKeyError):
            strict_json_loads(b'{"a":1,"a":2}')
        for value in (b'{"n":NaN}', b'{"n":1e999}', b'{"s":"\\ud800"}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json_loads(value)
        with self.assertRaises(ValueError):
            strict_json_loads(("[" * 33 + "0" + "]" * 33).encode())

    def test_names_are_normalized_but_not_interpreted(self) -> None:
        self.assertEqual(normalize_name("  Caf\u0065\u0301 <b>  "), "Café <b>")
        with self.assertRaises(ValueError):
            normalize_name("bad\nname")

    def test_logging_is_allowlisted(self) -> None:
        line = json_safe_log("probe", build="safe", secret="never", device="dev_123")
        self.assertIn('"build":"safe"', line)
        self.assertNotIn("never", line)


class CredentialTests(unittest.TestCase):
    def test_credentials_are_unique_expensive_verifiers(self) -> None:
        lookup_one, credential_one = new_credential()
        lookup_two, credential_two = new_credential()
        self.assertNotEqual((lookup_one, credential_one), (lookup_two, credential_two))
        self.assertGreaterEqual(len(credential_one.split("_", 2)[2]), 43)
        verifier = create_verifier(credential_one)
        self.assertTrue(verify_credential(credential_one, verifier))
        self.assertFalse(verify_credential(credential_two, verifier))
        self.assertNotIn(credential_one, json.dumps(verifier))

    def test_verification_phrase_is_transcript_bound(self) -> None:
        transcript = {"requestId": "pairreq_one", "name": "Phone", "protocol": 1}
        first = verification_phrase("secret", transcript)
        self.assertEqual(first, verification_phrase("secret", transcript))
        self.assertNotEqual(first, verification_phrase("secret", dict(transcript, name="Other")))
        self.assertEqual(len(first), 3)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "state"
        self.store = DeviceStore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_private_atomic_persistence_and_reload(self) -> None:
        device, credential = self.store.create_device("Phone", "ios-web", ["read:desktop"])
        path = self.root / "omarchy-sidecar" / "devices.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertNotIn(credential, path.read_text())
        reloaded = DeviceStore(self.root)
        self.assertEqual(reloaded.authenticate(credential)["id"], device["id"])

    def test_existing_credential_and_retired_scopes_survive_load_without_new_authority(self) -> None:
        legacy = ["read:desktop", "control:workspace", "control:window-focus", "control:media", *RETIRED_SCOPES]
        device, credential = self.store.create_device("Existing v0.1 phone", "android-web", legacy)
        reloaded = DeviceStore(self.root)
        authenticated = reloaded.authenticate(credential)
        self.assertEqual(authenticated["id"], device["id"])
        self.assertEqual(authenticated["scopes"], legacy)
        self.assertFalse(any(scope in authenticated["scopes"] for scope in REQUESTABLE_SCOPES))

    def test_rename_rescope_revoke_and_repair_remain_bounded(self) -> None:
        device, credential = self.store.create_device("Phone", "android-web", ["read:desktop"])
        self.assertEqual(self.store.rename(device["id"], "Pocket Deck")["name"], "Pocket Deck")
        self.assertEqual(self.store.rescope(device["id"], ["control:media"])["scopes"], ["control:media"])
        self.assertTrue(self.store.revoke(device["id"])["revoked"])
        self.assertIsNone(self.store.authenticate(credential))
        self.assertEqual(self.store.list_devices(include_revoked=True), [])
        for index in range(20):
            new_device, _ = self.store.create_device(f"Phone {index}", "android-web", ["read:desktop"])
            self.store.revoke(new_device["id"])
        DeviceStore(self.root)

    def test_device_creation_validates_its_own_trust_boundary(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_device("Phone", "platform\nspoof", ["read:desktop"])
        with self.assertRaises(ValueError):
            self.store.create_device("Phone", "android-web", ["read:desktop", "read:desktop"])
        with self.assertRaises(ValueError):
            self.store.create_device("Phone", "android-web", ["remote:shell"])

    def test_corruption_is_quarantined_private_and_paused(self) -> None:
        device, _ = self.store.create_device("Phone", "ios-web", ["read:desktop"])
        path = self.root / "omarchy-sidecar" / "devices.json"
        state = json.loads(path.read_text())
        state["devices"][0]["verifier"]["n"] = 2**30
        path.write_text(json.dumps(state))
        path.chmod(0o600)
        recovered = DeviceStore(self.root)
        self.assertTrue(recovered.paused)
        self.assertTrue(recovered.quarantined_path)
        quarantine = Path(recovered.quarantined_path)
        self.assertEqual(stat.S_IMODE(quarantine.stat().st_mode), 0o600)
        self.assertEqual(recovered.list_devices(), [])


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CoreFixture()
        self.core = self.fixture.core

    def tearDown(self) -> None:
        self.fixture.close()

    def test_pairing_consumes_secret_and_delivers_credential_once(self) -> None:
        opened, pending = self.fixture.request_pair("<Phone & safe>")
        self.assertNotIn(opened["pairUrl"].split("#", 1)[1], json.dumps(self.core.local_status()))
        with self.assertRaises(ApiError) as context:
            self.core.pair_request(
                {"secret": "wrong", "device": {"name": "Other", "platform": "ios-web", "clientVersion": "0.2.1", "protocol": 1}},
                "other",
            )
        self.assertEqual(context.exception.code, "pairing_expired")
        local = self.core.pending_local()[0]
        self.assertEqual(local["verificationPhrase"], pending["verificationPhrase"])
        self.assertIn("requestedAt", local)
        self.core.approve(pending["requestId"], ["read:desktop"])
        delivered = self.core.pair_status(
            {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]}, "client"
        )
        self.assertTrue(delivered["credential"].startswith("sc1_"))
        with self.assertRaises(ApiError):
            self.core.pair_status(
                {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]}, "client"
            )

    def test_denial_is_explicit_then_terminal(self) -> None:
        _, pending = self.fixture.request_pair()
        self.core.deny(pending["requestId"])
        with self.assertRaises(ApiError) as context:
            self.core.pair_status(
                {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]}, "client"
            )
        self.assertEqual(context.exception.code, "pairing_denied")

    def test_unknown_or_locked_state_blocks_approval_and_actions(self) -> None:
        _, pending = self.fixture.request_pair()
        self.fixture.adapter.locked = True
        with self.assertRaisesRegex(ValueError, "unlock"):
            self.core.approve(pending["requestId"], ["read:desktop"])
        self.fixture.adapter.locked = False
        device = self.core.approve(pending["requestId"], ["read:desktop", "control:media"])
        credential = self.core.pair_status(
            {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]}, "client"
        )["credential"]
        authenticated = self.core.authenticate(credential)
        self.fixture.adapter.lock_known = False
        with self.assertRaises(ApiError) as context:
            self.core.perform_action(authenticated, {"requestId": "req_locked", "action": "media.playPause", "parameters": {}})
        self.assertEqual(context.exception.code, "desktop_locked")
        snapshot = self.core.snapshot(authenticated)
        self.assertEqual(set(snapshot), {"protocol", "seq", "server", "session", "desktop"})
        self.assertTrue(snapshot["desktop"]["locked"])
        self.assertEqual(device["id"], snapshot["session"]["deviceId"])

    def test_scope_pause_schema_and_replay(self) -> None:
        device, _ = self.fixture.pair(["read:desktop", "control:workspace"])
        denied = {"requestId": "req_no_media", "action": "media.playPause", "parameters": {}}
        with self.assertRaises(ApiError) as context:
            self.core.perform_action(device, denied)
        self.assertEqual(context.exception.code, "permission_denied")
        with self.assertRaises((ApiError, ValueError)):
            self.core.perform_action(device, {"requestId": "req_extra", "action": "workspace.focus", "parameters": {"workspaceId": "ws_2", "extra": 1}})
        request = {"requestId": "req_workspace", "action": "workspace.focus", "parameters": {"workspaceId": "ws_2"}, "expectedSeq": self.core.sequence}
        first = self.core.perform_action(device, request)
        replay = self.core.perform_action(device, request)
        self.assertEqual(first, replay)
        self.assertEqual(first["seq"], self.core.snapshot(device)["seq"])
        self.fixture.store.set_paused(True)
        with self.assertRaises(ApiError) as replay_while_paused:
            self.core.perform_action(device, request)
        self.assertEqual(replay_while_paused.exception.code, "sidecar_paused")
        with self.assertRaises(ApiError) as paused:
            self.core.perform_action(device, {"requestId": "req_paused", "action": "workspace.focus", "parameters": {"workspaceId": "ws_1"}})
        self.assertEqual(paused.exception.code, "sidecar_paused")
        self.assertEqual(set(self.core.snapshot(device)), {"protocol", "seq", "server", "session", "desktop"})

    def test_concurrent_replay_dispatches_once_and_binds_payload(self) -> None:
        device, _ = self.fixture.pair(["read:desktop", "control:media"])
        original_perform = self.fixture.adapter.perform
        calls = 0
        calls_lock = threading.Lock()

        def slow_perform(action: str, parameters: dict) -> dict:
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.05)
            return original_perform(action, parameters)

        request = {"requestId": "req_concurrent", "action": "media.playPause", "parameters": {}}
        results: list[dict] = []
        errors: list[Exception] = []

        def invoke() -> None:
            try:
                results.append(self.core.perform_action(device, request))
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        with mock.patch.object(self.fixture.adapter, "perform", side_effect=slow_perform):
            threads = [threading.Thread(target=invoke) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
        self.assertFalse(errors)
        self.assertEqual(calls, 1)
        self.assertEqual(results[0], results[1])
        with self.assertRaises(ApiError) as mismatch:
            self.core.perform_action(device, {"requestId": "req_concurrent", "action": "media.next", "parameters": {}})
        self.assertEqual(mismatch.exception.code, "bad_request")

    def test_stale_authenticated_policy_and_route_uncertainty_fail_closed(self) -> None:
        device, _ = self.fixture.pair(["read:desktop", "control:media"])
        self.core.rescope_device(device["id"], ["read:desktop"])
        with self.assertRaises(ApiError) as rescoped:
            self.core.perform_action(device, {"requestId": "req_stale_scope", "action": "media.playPause", "parameters": {}})
        self.assertEqual(rescoped.exception.code, "permission_denied")
        current = self.fixture.store.get_device(device["id"])
        assert current is not None
        self.core.set_route_error("tailscale-route-stopped", "route disappeared")
        with self.assertRaises(ApiError) as route:
            self.core.perform_action(current, {"requestId": "req_route_down", "action": "workspace.focus", "parameters": {"workspaceId": "ws_1"}})
        self.assertEqual(route.exception.code, "service_unavailable")

    def test_stale_authenticated_snapshot_stream_and_capability_fail_after_revoke(self) -> None:
        device, _ = self.fixture.pair()
        self.core.revoke(device["id"])
        for operation in (
            lambda: self.core.snapshot(device),
            lambda: self.core.subscribe(device, 4),
            lambda: self.core.request_capabilities(
                device, {"requestId": "cap_stale", "scopes": ["control:theme"]}
            ),
        ):
            with self.assertRaises(ApiError) as context:
                operation()
            self.assertEqual((context.exception.code, context.exception.status), ("session_revoked", 401))

    def test_route_loss_redacts_snapshots_and_invalidates_drop_intents(self) -> None:
        device, _ = self.fixture.pair([*DEFAULT_SCOPES, "write:inbox"])
        body = b"route race"
        intent = self.core.create_inbox_intent(device, {
            "requestId": "drop_route_loss",
            "files": [{
                "name": "route.txt", "mediaType": "text/plain", "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }],
        })
        self.core.set_route_error("tailscale-route-stopped", "route stopped")
        snapshot = self.core.snapshot(device)
        self.assertFalse(snapshot["desktop"]["available"])
        self.assertNotIn("windows", snapshot)
        with self.assertRaises(ApiError) as replay_error:
            self.core.completed_upload_replay(
                device, intent["files"][0]["uploadId"], len(body), "text/plain"
            )
        self.assertEqual(replay_error.exception.code, "service_unavailable")

    def test_pairing_rejects_non_string_or_unsafe_client_version(self) -> None:
        for client_version in (2, "", "bad version", "x" * 33):
            with self.subTest(client_version=client_version):
                opened = self.core.open_pairing()
                secret = opened["pairUrl"].split("#", 1)[1]
                with self.assertRaises(ApiError) as context:
                    self.core.pair_request({
                        "secret": secret,
                        "device": {"name": "Phone", "platform": "android-web", "clientVersion": client_version, "protocol": 1},
                    }, "client")
                self.assertEqual(context.exception.code, "bad_request")

    def test_read_scope_filters_all_desktop_details(self) -> None:
        device, _ = self.fixture.pair(["control:media"])
        snapshot = self.core.snapshot(device)
        self.assertNotIn("theme", snapshot)
        self.assertNotIn("workspaces", snapshot)
        self.assertEqual(snapshot["session"]["permissions"], ["control:media"])

    def test_revocation_invalidates_before_return_and_closes_stream(self) -> None:
        device, credential = self.fixture.pair()
        subscriber = self.core.subscribe(device, 4)
        subscriber.get(0.1)
        listed = self.core.local_status()["devices"]
        self.assertTrue(listed[0]["online"])
        revoked = self.core.revoke(device["id"])
        self.assertTrue(revoked["revoked"])
        self.assertEqual(subscriber.get(0.1)["event"], "session.revoked")
        with self.assertRaises(ApiError):
            self.core.authenticate(credential)

    def test_security_receipt_is_redacted_and_literal(self) -> None:
        receipt = self.core.local_status()["securityReceipt"]
        self.assertTrue(receipt["nonRoot"])
        self.assertTrue(receipt["loopbackOnly"])
        self.assertEqual(receipt["listener"], "127.0.0.1:47991")
        self.assertIn("Sidecar device credentials and scopes", receipt["authorization"])
        self.assertNotIn("credential", json.dumps(receipt).lower().replace("credentials", ""))
        self.assertIn("securityReceipt", self.core.diagnostics())

    def test_local_retry_recovers_route_without_restarting_helper(self) -> None:
        self.core.set_route_error("tailscale-unavailable", "Tailscale is missing.")
        status = self.core.retry_route()
        self.assertEqual(status["state"], "ready")
        self.assertIsNone(status["lastError"])
        self.assertEqual(status["endpoint"], "http://127.0.0.1:47991")
        self.assertNotIn("carry", status)

    def test_local_retry_keeps_route_error_literal_on_failure(self) -> None:
        self.core.set_route_error("tailscale-unavailable", "Tailscale is missing.")
        with mock.patch.object(self.core.route, "start", side_effect=RouteError("tailscale-disconnected", "Connect Tailscale, then try again.")):
            status = self.core.retry_route()
        self.assertEqual(status["lastError"], {
            "code": "tailscale-disconnected",
            "message": "Connect Tailscale, then try again.",
        })

    def test_portal_morph_beam_and_hold_lock_are_typed_and_authoritative(self) -> None:
        device, _ = self.fixture.pair()
        snapshot = self.core.snapshot(device)
        self.assertEqual(snapshot["themes"]["currentId"], "theme_aurora")
        self.assertEqual(set(snapshot["beam"]), {"provider", "kind", "label", "status", "actions"})
        self.assertEqual(snapshot["beam"]["provider"], "media.transport")
        self.assertEqual([item["action"] for item in snapshot["beam"]["actions"]], [
            "media.previous", "media.playPause", "media.next",
        ])
        moved = self.core.perform_action(device, {
            "requestId": "req_move", "action": "window.moveToWorkspace",
            "parameters": {"windowId": "win_editor", "workspaceId": "ws_2"},
        })
        self.assertEqual(moved["result"], {"movedWindowId": "win_editor", "workspaceId": "ws_2"})
        self.assertTrue(any(item["id"] == "win_editor" and item["workspaceId"] == "ws_2"
                            for item in self.core.snapshot(device)["windows"]))
        themed = self.core.perform_action(device, {
            "requestId": "req_theme", "action": "theme.set", "parameters": {"themeId": "theme_ember"},
        })
        self.assertEqual(themed["result"]["themeName"], "Ember")
        self.assertEqual(self.core.snapshot(device)["themes"]["currentId"], "theme_ember")
        with self.assertRaises(ApiError) as limited:
            self.core.perform_action(device, {
                "requestId": "req_theme_fast", "action": "theme.backgroundNext", "parameters": {},
            })
        self.assertEqual(limited.exception.code, "rate_limited")
        locked = self.core.perform_action(device, {
            "requestId": "req_lock", "action": "desktop.lock", "parameters": {},
        })
        self.assertTrue(locked["result"]["locked"])
        redacted = self.core.snapshot(device)
        self.assertEqual(set(redacted), {"protocol", "seq", "server", "session", "desktop"})

    def test_existing_phone_explicitly_requests_and_keeps_credential(self) -> None:
        old_scopes = ["read:desktop", "control:workspace", "control:window-focus", "control:media", "control:focus-mode"]
        device, credential = self.fixture.pair(old_scopes)
        self.assertFalse(any(scope in device["scopes"] for scope in REQUESTABLE_SCOPES))
        requested = self.core.request_capabilities(device, {
            "requestId": "cap_upgrade", "scopes": list(REQUESTABLE_SCOPES),
        })
        self.assertEqual(requested["status"], "pending")
        local = self.core.capability_requests_local()
        self.assertEqual(local[0]["deviceName"], "Test Phone")
        self.assertEqual(len(local[0]["labels"]), len(REQUESTABLE_SCOPES))
        approved = self.core.approve_capabilities("cap_upgrade")
        self.assertEqual(approved["id"], device["id"])
        self.assertTrue(set(REQUESTABLE_SCOPES).issubset(approved["scopes"]))
        authenticated = self.core.authenticate(credential)
        self.assertEqual(authenticated["id"], device["id"])
        self.assertTrue(set(REQUESTABLE_SCOPES).issubset(authenticated["scopes"]))
        status = self.core.capability_status(authenticated, {"requestId": "cap_upgrade"})
        self.assertEqual(status["status"], "approved")

    def test_capability_requests_are_allowlisted_and_desktop_unlock_gated(self) -> None:
        device, _ = self.fixture.pair(["read:desktop"])
        with self.assertRaises(ApiError):
            self.core.request_capabilities(device, {"requestId": "cap_bad", "scopes": ["control:media"]})
        pending = self.core.request_capabilities(device, {"requestId": "cap_lock", "scopes": ["control:lock"]})
        self.assertEqual(pending["status"], "pending")
        self.fixture.adapter.locked = True
        with self.assertRaisesRegex(ValueError, "unlock"):
            self.core.approve_capabilities("cap_lock")
        self.core.deny_capabilities("cap_lock")
        with self.assertRaises(ApiError) as denied:
            self.core.capability_status(device, {"requestId": "cap_lock"})
        self.assertEqual(denied.exception.code, "capability_denied")


class BoundTests(unittest.TestCase):
    def test_subscriber_and_rate_limit_memory_are_bounded(self) -> None:
        subscriber = Subscriber("dev_one")
        self.assertFalse(subscriber.put({"event": "huge", "seq": 1, "data": {"value": "x" * MAX_EVENT_QUEUE_BYTES}}))
        self.assertTrue(subscriber.closed)
        limits = RateLimits()
        for index in range(400):
            limits.allow("auth", str(index), 1, 0)
        self.assertLessEqual(len(limits._buckets), 256)

    def test_authoritative_event_replaces_stale_queued_state(self) -> None:
        subscriber = Subscriber("dev_one")
        self.assertTrue(subscriber.put({"event": "snapshot", "seq": 1, "data": {"windows": [{"app": "Private"}]}}))
        self.assertTrue(subscriber.put({"event": "snapshot", "seq": 2, "data": {"media": {"playing": True}}}))
        locked = {"event": "snapshot", "seq": 3, "data": {"desktop": {"locked": True}}}
        self.assertTrue(subscriber.replace(locked))
        self.assertEqual(subscriber.get(0.1), locked)
        self.assertIsNone(subscriber.get(0.01))
        self.assertEqual(subscriber.bytes_queued, 0)


class AdapterAndRouteTests(unittest.TestCase):
    def test_route_conflict_detection_is_recursive_and_exact(self) -> None:
        self.assertTrue(_contains_port({"TCP": {"48719": {"Web": "http://127.0.0.1:47991"}}}, 48719))
        self.assertFalse(_contains_port({"Web": "http://127.0.0.1:148719"}, 48719))
        self.assertTrue(_contains_exact_value({"Web": {"Proxy": "http://127.0.0.1:47991"}}, "http://127.0.0.1:47991"))
        self.assertFalse(_contains_exact_value({"Web": {"Proxy": "http://127.0.0.1:47992"}}, "http://127.0.0.1:47991"))

    def test_owned_route_requires_exact_port_and_loopback_target(self) -> None:
        controller = TailscaleRouteController()
        owned = {
            "TCP": {"48719": {"HTTPS": True}},
            "Web": {"desktop.tailnet.ts.net:48719": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:47991"}}}},
        }
        self.assertTrue(controller._owned_route_visible(owned))
        self.assertFalse(controller._owned_route_visible({"TCP": {"48719": {"HTTPS": True}}}))
        self.assertFalse(controller._owned_route_visible({
            "TCP": {"48719": {"HTTPS": True}},
            "Web": {"desktop.tailnet.ts.net:48719": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:49999"}}}},
        }))
        self.assertFalse(controller._owned_route_visible({
            "TCP": {"48719": {"HTTPS": True}},
            "Web": {"desktop.tailnet.ts.net:4443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:47991"}}}},
        }))
        self.assertFalse(controller._owned_route_visible({
            "Web": {
                "desktop.tailnet.ts.net:48719": {"Handlers": {}},
                "desktop.tailnet.ts.net:4443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:47991"}}},
            },
        }))

    def test_live_serve_child_without_owned_status_fails_closed(self) -> None:
        child = mock.Mock()
        child.poll.return_value = None
        child.stdout = None
        child.stderr = None
        child.pid = 12345
        controller = TailscaleRouteController(startup_timeout=0.001)
        controller._tailscale_path = "/usr/bin/tailscale"
        with (
            mock.patch.object(controller, "_preflight", return_value="desktop.tailnet.ts.net"),
            mock.patch("sidecar.routes.subprocess.Popen", return_value=child),
            mock.patch.object(controller, "_start_output_drains"),
            mock.patch.object(controller, "_terminate_child") as terminate,
            mock.patch.object(controller, "_run_json", return_value={}),
            mock.patch("sidecar.routes.time.monotonic", side_effect=[0.0, 0.0, 1.0]),
            mock.patch("sidecar.routes.time.sleep"),
        ):
            with self.assertRaises(RouteError) as context:
                controller.start()
        self.assertEqual(context.exception.code, "tailscale-https-unavailable")
        self.assertEqual(terminate.call_args_list[-1], mock.call(child))
        self.assertIsNone(controller.child_pid())
        self.assertEqual(controller.state, "unavailable")

    def test_route_error_recovers_automatically_without_helper_restart(self) -> None:
        fixture = CoreFixture()
        try:
            route = mock.Mock()
            route.start.return_value = "https://desktop.tailnet.ts.net:48719"
            fixture.core.route = route
            fixture.core.set_route_error("tailscale-unavailable", "Tailscale is missing.")
            fixture.core._next_route_retry = 1.0
            with mock.patch("sidecar.core.time.monotonic", return_value=2.0):
                fixture.core._retry_route_if_due()
            self.assertEqual(fixture.core.origin, "https://desktop.tailnet.ts.net:48719")
            self.assertIsNone(fixture.core.route_error)
            route.start.assert_called_once_with()
        finally:
            fixture.close()

    def test_route_conflict_retry_stays_read_only_and_can_recover_after_update(self) -> None:
        fixture = CoreFixture()
        try:
            route = mock.Mock()
            route.start.return_value = "https://desktop.tailnet.ts.net:48719"
            fixture.core.route = route
            fixture.core.set_route_error("tailscale-route-conflict", "An exact route is already present.")
            fixture.core._next_route_retry = 1.0
            with mock.patch("sidecar.core.time.monotonic", return_value=2.0):
                fixture.core._retry_route_if_due()
            route.start.assert_called_once_with()
            self.assertEqual(fixture.core.origin, "https://desktop.tailnet.ts.net:48719")
            self.assertIsNone(fixture.core.route_error)
        finally:
            fixture.close()

    def test_tailscale_preflight_preserves_conflicts(self) -> None:
        controller = TailscaleRouteController()
        replies = iter([
            {"BackendState": "Running", "Self": {"DNSName": "desktop.tailnet.ts.net."}},
            {"TCP": {"48719": {"HTTPS": True}}},
        ])
        with mock.patch("sidecar.routes.shutil.which", return_value="/usr/bin/tailscale"), mock.patch.object(controller, "_run_json", side_effect=lambda *_args, **_kwargs: next(replies)) as run_json:
            with self.assertRaises(RouteError) as context:
                controller._preflight()
        self.assertEqual(context.exception.code, "tailscale-route-conflict")
        self.assertIsNone(controller.child_pid())
        self.assertEqual(run_json.call_args_list, [
            mock.call(["tailscale", "status", "--json"]),
            mock.call(["tailscale", "serve", "status", "--json"], allow_empty=True),
        ])

    def test_tailscale_funnel_indicator_fails_without_funnel_command(self) -> None:
        controller = TailscaleRouteController()
        replies = iter([
            {"BackendState": "Running", "Self": {"DNSName": "desktop.tailnet.ts.net."}},
            {"TCP": {"443": {"Funnel": True}}},
        ])
        with mock.patch("sidecar.routes.shutil.which", return_value="/usr/bin/tailscale"), mock.patch.object(controller, "_run_json", side_effect=lambda *_args, **_kwargs: next(replies)) as run_json:
            with self.assertRaises(RouteError) as context:
                controller._preflight()
        self.assertEqual(context.exception.code, "tailscale-route-conflict")
        self.assertEqual(len(run_json.call_args_list), 2)

    def test_helper_refuses_root_before_runtime_setup(self) -> None:
        with mock.patch("sidecar.app.os.geteuid", return_value=0):
            with self.assertRaisesRegex(SystemExit, "refuses to run as root"):
                sidecar_app.main([])

    def test_helper_launcher_disables_plugin_tree_bytecode(self) -> None:
        launcher = (PROJECT / "helper" / "sidecard").read_text()
        self.assertLess(launcher.index("sys.dont_write_bytecode = True"), launcher.index("from sidecar.app import main"))

    def test_adapter_dispatch_is_fixed_and_target_mapped(self) -> None:
        adapter = OmarchyAdapter()
        adapter._workspace_targets = {"ws_safe": "2"}
        adapter._window_targets = {"win_safe": "0xabc"}
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        with (
            mock.patch.object(adapter, "_run", return_value=completed) as run,
            mock.patch.object(adapter, "_run_json", return_value={"id": 2}) as run_json,
        ):
            adapter.perform("workspace.focus", {"workspaceId": "ws_safe"})
            run.assert_called_once_with(["hyprctl", "dispatch", 'hl.dsp.focus({ workspace = "2" })'])
            run_json.assert_called_once_with(["hyprctl", "-j", "activeworkspace"])
        with mock.patch.object(adapter, "_run", side_effect=[
            subprocess.CompletedProcess([], 1, "", ""), completed,
        ]) as run, mock.patch.object(adapter, "_run_json", return_value={"address": "0xabc"}) as run_json:
            adapter.perform("window.focus", {"windowId": "win_safe"})
            self.assertEqual(run.call_args_list[0].args[0], [
                "hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xabc" })',
            ])
            self.assertEqual(run.call_args_list[1].args[0], [
                "hyprctl", "dispatch", "focuswindow", "address:0xabc",
            ])
            run_json.assert_called_once_with(["hyprctl", "-j", "activewindow"])
        with self.assertRaises(AdapterError):
            adapter.perform("workspace.focus", {"workspaceId": "workspace;rm"})
        with mock.patch.object(adapter, "_run", return_value=subprocess.CompletedProcess([], 0, "--upload-command=bad", "")):
            with self.assertRaises(AdapterError):
                adapter._sink_name()

    def test_adapter_requires_authoritative_focus_confirmation(self) -> None:
        adapter = OmarchyAdapter()
        adapter._workspace_targets = {"ws_safe": "2"}
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        with (
            mock.patch.object(adapter, "_run", return_value=completed),
            mock.patch.object(adapter, "_run_json", return_value={"id": 1}),
            mock.patch("sidecar.adapters.time.monotonic", side_effect=[0.0, 2.0]),
        ):
            with self.assertRaisesRegex(AdapterError, "action_failed"):
                adapter.perform("workspace.focus", {"workspaceId": "ws_safe"})

    def test_adapter_executes_the_resolved_fixed_binary(self) -> None:
        adapter = OmarchyAdapter()
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            mock.patch("sidecar.adapters.shutil.which", return_value="/usr/bin/hyprctl"),
            mock.patch("sidecar.adapters.subprocess.run", return_value=completed) as run,
        ):
            adapter._run(["hyprctl", "-j", "clients"])
        self.assertEqual(run.call_args.args[0], ["/usr/bin/hyprctl", "-j", "clients"])

    def test_window_move_is_addressed_no_follow_and_confirmed(self) -> None:
        adapter = OmarchyAdapter()

        def refresh_maps() -> tuple[list[dict], list[dict]]:
            adapter._workspace_targets = {"ws_safe": "2"}
            adapter._window_targets = {"win_safe": "0xabc"}
            return [], []

        completed = subprocess.CompletedProcess([], 0, "ok", "")
        clients = [{"address": "0xabc", "workspace": {"id": 2}}]
        with (
            mock.patch.object(adapter, "_desktop_state", side_effect=refresh_maps),
            mock.patch.object(adapter, "_run", return_value=completed) as run,
            mock.patch.object(adapter, "_run_json", return_value=clients) as run_json,
        ):
            result = adapter.perform("window.moveToWorkspace", {"windowId": "win_safe", "workspaceId": "ws_safe"})
        self.assertEqual(result, {"movedWindowId": "win_safe", "workspaceId": "ws_safe"})
        run.assert_called_once_with([
            "hyprctl", "dispatch",
            'hl.dsp.window.move({ workspace = "2", follow = false, window = "address:0xabc" })',
        ])
        run_json.assert_called_once_with(["hyprctl", "-j", "clients"])

    def test_theme_inventory_and_mutations_are_fixed_and_trusted_only(self) -> None:
        adapter = OmarchyAdapter()
        listed = subprocess.CompletedProcess([], 0, "Aurora\n../escape\nEmber;bad\nLagoon\nAurora\n", "")
        current = subprocess.CompletedProcess([], 0, "Aurora\n", "")
        with mock.patch.object(adapter, "_run", side_effect=[listed, current]) as run:
            themes = adapter._themes()
        self.assertEqual([item["name"] for item in themes["items"]], ["Aurora", "Lagoon"])
        self.assertEqual(themes["currentName"], "Aurora")
        self.assertEqual(run.call_args_list, [mock.call(["omarchy-theme-list"]), mock.call(["omarchy-theme-current"])])
        self.assertTrue(all(isinstance(item["previewAvailable"], bool) for item in themes["items"]))

        theme_id = themes["currentId"]

        def refresh_themes() -> dict:
            adapter._theme_targets = {theme_id: "Aurora"}
            return {"items": [{"id": theme_id, "name": "Aurora", "previewAccent": "#8b5cf6"}],
                    "currentId": theme_id, "currentName": "Aurora"}

        with (
            mock.patch.object(adapter, "_themes", side_effect=refresh_themes),
            mock.patch.object(adapter, "_run", side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "Aurora\n", ""),
            ]) as run,
        ):
            selected = adapter.perform("theme.set", {"themeId": theme_id})
        self.assertEqual(selected["themeName"], "Aurora")
        self.assertEqual(run.call_args_list, [
            mock.call(["omarchy-theme-set", "Aurora"], timeout=45.0),
            mock.call(["omarchy-theme-current"], timeout=2.0),
        ])
        with mock.patch.object(adapter, "_themes", return_value={"items": [], "currentId": "", "currentName": ""}):
            with self.assertRaises(AdapterError):
                adapter.perform("theme.set", {"themeId": theme_id})

    def test_theme_preview_uses_exact_installed_files_without_following_symlinks(self) -> None:
        adapter = OmarchyAdapter()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            omarchy = root / "omarchy"
            user_theme = home / ".config" / "omarchy" / "themes" / "aurora"
            stock_theme = omarchy / "themes" / "aurora"
            (user_theme / "backgrounds").mkdir(parents=True)
            (stock_theme / "backgrounds").mkdir(parents=True)
            (user_theme / "backgrounds" / "z-last.png").write_bytes(b"user-background")
            (stock_theme / "preview.png").write_bytes(b"stock-preview")
            with (
                mock.patch("sidecar.adapters.Path.home", return_value=home),
                mock.patch.dict("sidecar.adapters.os.environ", {"OMARCHY_PATH": str(omarchy)}),
            ):
                self.assertEqual(adapter._theme_preview("Aurora", include_body=True), (b"user-background", "image/png"))
                (user_theme / "preview.webp").write_bytes(b"user-preview")
                self.assertEqual(adapter._theme_preview("Aurora", include_body=True), (b"user-preview", "image/webp"))
                (user_theme / "preview.webp").unlink()
                (user_theme / "backgrounds" / "z-last.png").unlink()
                secret = root / "secret.png"
                secret.write_bytes(b"must-not-leak")
                (user_theme / "preview.png").symlink_to(secret)
                self.assertEqual(adapter._theme_preview("Aurora", include_body=True), (b"stock-preview", "image/png"))
                (user_theme / "preview.png").unlink()
                (stock_theme / "preview.png").write_bytes(b"x" * (MAX_THEME_PREVIEW_BYTES + 1))
                self.assertIsNone(adapter._theme_preview("Aurora", include_body=True))

    def test_theme_preview_requires_a_current_opaque_inventory_entry(self) -> None:
        adapter = OmarchyAdapter()
        theme_id = "theme_safe"

        def inventory() -> dict:
            adapter._theme_targets = {theme_id: "Aurora"}
            return {"items": [{"id": theme_id, "name": "Aurora", "previewAccent": "#8b5cf6", "previewAvailable": True}],
                    "currentId": theme_id, "currentName": "Aurora"}

        with (
            mock.patch.object(adapter, "_themes", side_effect=inventory),
            mock.patch.object(adapter, "_theme_preview", return_value=(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x01\x00\x00\x00\x01",
                "image/png",
            )),
        ):
            body, mime = adapter.theme_preview(theme_id)
            self.assertEqual((len(body), mime), (24, "image/png"))
            with self.assertRaises(AdapterError):
                adapter.theme_preview("theme_stale")
        with (
            mock.patch.object(adapter, "_themes", side_effect=inventory),
            mock.patch.object(adapter, "_theme_preview", return_value=(b"not-an-image", "image/png")),
        ):
            with self.assertRaises(AdapterError):
                adapter.theme_preview(theme_id)

    def test_background_and_lock_use_only_public_fixed_commands(self) -> None:
        adapter = OmarchyAdapter()
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(adapter, "_run", return_value=completed) as run:
            self.assertTrue(adapter.perform("theme.backgroundNext", {})["backgroundChanged"])
        run.assert_called_once_with(["omarchy-theme-bg-next"], timeout=10.0)
        with (
            mock.patch.object(adapter, "_run", return_value=completed) as run,
            mock.patch.object(adapter, "lock_state", return_value=(True, True)),
        ):
            self.assertTrue(adapter.perform("desktop.lock", {})["locked"])
        run.assert_called_once_with(["omarchy-system-lock"], timeout=3.0)

    def test_theme_tokens_are_sanitized(self) -> None:
        self.assertEqual(sanitized_color("#AABBcc", "#000000"), "#aabbcc")
        self.assertEqual(sanitized_color("url(evil)", "#000000"), "#000000")

    def test_qml_text_never_auto_interprets_untrusted_markup(self) -> None:
        widget = (PROJECT / "bar-widget" / "v1011" / "BarWidget.qml").read_text()
        service = (PROJECT / "service" / "v1011" / "Service.qml").read_text()
        self.assertEqual(widget.count("Text {"), widget.count("textFormat: Text.PlainText"))
        self.assertIn('"Confirm remove phone"', widget)
        self.assertIn("revokeConfirming", widget)
        self.assertIn('"Private by design"', widget)
        self.assertIn('"Security Receipt · UID " + String(root.securityReceipt.uid)', widget)
        self.assertIn('"Security Receipt · checking local helper · "', widget)
        self.assertIn("Tailscale transports privately", widget)
        self.assertIn('? "Tailscale is needed"', widget)
        self.assertIn('id: tailscaleSetupActions', widget)
        self.assertIn('text: "Try again now"', widget)
        self.assertIn('leftAlign: true', widget)
        self.assertIn('"Open Service menu"', widget)
        self.assertIn('"Open HTTPS settings"', widget)
        self.assertIn('"https://login.tailscale.com/admin/dns"', service)
        self.assertIn('"omarchy-shell", "omarchy.tailscale", "open"', service)
        self.assertIn('text: "BEFORE YOU SCAN"', widget)
        self.assertIn("Open Tailscale on this phone", widget)
        self.assertIn('text: root.hasDevices ? "+ Pair" : "Pair a phone"', widget)
        self.assertIn("capabilityRequests", widget)
        self.assertIn("capabilityApprove", widget)
        self.assertIn("capabilityDeny", widget)
        self.assertIn('text: "Unlock new Sidecar abilities"', widget)
        self.assertNotIn('text: "Agent"', widget)
        self.assertNotIn("agentSetup", service)
        self.assertIn('readonly property var displayDevices: status.devices || []', widget)
        self.assertNotIn("groupedDevices", widget)
        self.assertNotIn("old pairing", widget)
        self.assertIn(
            '"read:desktop,control:workspace,control:window-focus,control:window-move,control:media,control:theme,control:lock"',
            service,
        )
        self.assertNotIn('wl-copy', service)
        self.assertIn("approvalGlow", widget)
        receipt = widget.split('"Security Receipt · UID " + String(root.securityReceipt.uid)', 1)[1]
        self.assertIn('visible: root.securityExpanded || (!root.securityReceipt.healthy && !root.securityReceiptPending)', widget)
        self.assertIn('visible: root.tailscaleSetupFailure', receipt)
        self.assertIn('onClicked: root.openTailscaleRecovery()', receipt)
        self.assertIn('foreground: Color.urgent', receipt)
        self.assertIn('component PhoneMark: Item', widget)
        self.assertIn('fixedWidth: root.barSize', widget)
        setup_actions = widget.split("id: tailscaleSetupActions", 1)[1].split("Column {", 1)[0]
        self.assertGreaterEqual(setup_actions.count("width: parent.width"), 3)
        self.assertNotIn("Row {", setup_actions)
        self.assertIn('["omarchy-menu", "summon", "install.service"]', service)
        self.assertNotIn("omarchy-install-service-tailscale", service)

    def test_versioned_runtime_graph_moves_as_one_cache_busting_unit(self) -> None:
        graph = "v1011"
        manifest = json.loads((PROJECT / "manifest.json").read_text())
        self.assertEqual(manifest["entryPoints"]["service"], f"service/{graph}/Service.qml")
        self.assertEqual(manifest["entryPoints"]["barWidget"], f"bar-widget/{graph}/BarWidget.qml")
        self.assertEqual({path.name for path in (PROJECT / "service").iterdir()}, {graph})
        self.assertEqual({path.name for path in (PROJECT / "bar-widget").iterdir()}, {graph})
        self.assertIn(f"sidecar-service-{graph}", (PROJECT / "service" / graph / "Service.qml").read_text())
        self.assertIn(f"sidecar-widget-{graph}", (PROJECT / "bar-widget" / graph / "BarWidget.qml").read_text())
        for name in ("app", "model", "sw"):
            self.assertTrue((PROJECT / "web" / "dist" / f"{name}.{graph}.js").is_file())
        self.assertTrue((PROJECT / "web" / "dist" / f"app.{graph}.css").is_file())
        self.assertNotIn("v0101", (PROJECT / "sidecar" / "constants.py").read_text())
        self.assertNotIn("v0101", (PROJECT / "sidecar" / "server.py").read_text())
        self.assertEqual(set(ALL_SCOPES), set(DEFAULT_SCOPES) | set(RETIRED_SCOPES) | {"write:inbox"})
        self.assertTrue(set(REQUESTABLE_SCOPES).isdisjoint(RETIRED_SCOPES))


if __name__ == "__main__":
    unittest.main()
