from __future__ import annotations

import http.client
import hashlib
import base64
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from sidecar.constants import DEFAULT_SCOPES


PROJECT = Path(__file__).resolve().parents[1]


class HelperIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.runtime.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        self.home.mkdir(mode=0o700)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.environment = dict(
            os.environ,
            HOME=str(self.home),
            XDG_RUNTIME_DIR=str(self.runtime),
            XDG_STATE_HOME=str(self.state),
            XDG_DOWNLOAD_DIR=str(self.home / "Downloads"),
        )
        self.process: subprocess.Popen[str] | None = None
        self._start()

    def tearDown(self) -> None:
        self._stop()
        self.temporary.cleanup()

    def _start(self) -> None:
        self.process = subprocess.Popen(
            [
                str(PROJECT / "helper" / "sidecard"),
                "--plugin-root", str(PROJECT),
                "--fake-route", "--fake-adapters",
                "--port", str(self.port),
            ],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate()
                self.fail(f"helper exited during startup: {stdout} {stderr}")
            try:
                status, body, _ = self.request("GET", "/health", origin=False)
                if status == 200 and body["status"] == "ok":
                    return
            except (OSError, urllib.error.URLError):
                time.sleep(0.03)
        self.fail("helper did not become ready")

    def _stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                self.control("shutdown")
                self.process.wait(timeout=5)
            except Exception:
                self.process.terminate()
                self.process.wait(timeout=5)
        self.process.communicate(timeout=1)
        self.process = None

    def control(self, *arguments: str) -> dict:
        output = subprocess.check_output(
            [str(PROJECT / "helper" / "sidecarctl"), *arguments],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            timeout=5,
        )
        return json.loads(output)

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        token: str = "",
        origin: bool = True,
        content_type: str = "application/json",
        raw: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict, dict[str, str]]:
        headers = dict(extra_headers or {})
        if origin:
            headers["Origin"] = self.origin
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = raw
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
        if data is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = response.read()
                return response.status, json.loads(payload or b"{}"), dict(response.headers)
        except urllib.error.HTTPError as error:
            try:
                payload = error.read()
                return error.code, json.loads(payload or b"{}"), dict(error.headers)
            finally:
                error.close()

    def request_bytes(self, path: str, token: str = "") -> tuple[int, bytes, dict[str, str]]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request = urllib.request.Request(self.origin + path, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.read(), dict(error.headers)
            finally:
                error.close()

    def pair(
        self,
        scopes: str = ",".join(DEFAULT_SCOPES),
        client_instance_id: str = "",
    ) -> tuple[dict, str]:
        opened = self.control("pair-open")
        secret = opened["pairUrl"].split("#", 1)[1]
        device = {"name": "HTTP Phone", "platform": "android-web", "clientVersion": "0.2.2", "protocol": 1}
        if client_instance_id:
            device["clientInstanceId"] = client_instance_id
        status, pending, _ = self.request(
            "POST",
            "/api/v1/pair/request",
            {
                "secret": secret,
                "device": device,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.control("status")["pending"][0]["verificationPhrase"], pending["verificationPhrase"])
        self.control("pair-approve", pending["requestId"], "--scopes", scopes)
        status, delivered, _ = self.request(
            "POST",
            "/api/v1/pair/status",
            {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
        )
        self.assertEqual(status, 200)
        return delivered["device"], delivered["credential"]

    def test_same_browser_pairing_replaces_old_http_session_without_merging_names(self) -> None:
        instance_one = "sci1_" + "A" * 43
        instance_two = "sci1_" + "B" * 43
        first, first_credential = self.pair(client_instance_id=instance_one)
        second, second_credential = self.pair(client_instance_id=instance_one)
        self.assertNotEqual(first["id"], second["id"])
        status, body, _ = self.request("GET", "/api/v1/snapshot", token=first_credential, origin=False)
        self.assertEqual((status, body["error"]["code"]), (401, "not_authenticated"))
        status, snapshot, _ = self.request("GET", "/api/v1/snapshot", token=second_credential, origin=False)
        self.assertEqual((status, snapshot["session"]["deviceId"]), (200, second["id"]))
        self.assertEqual(self.control("status")["deviceCount"], 1)

        third, third_credential = self.pair(client_instance_id=instance_two)
        self.assertNotEqual(second["id"], third["id"])
        self.assertEqual(self.control("status")["deviceCount"], 2)
        status, identified, _ = self.request(
            "POST", "/api/v1/session/identify", {"clientInstanceId": instance_two}, third_credential,
        )
        self.assertEqual((status, identified["status"], identified["replacedPairings"]), (200, "bound", 0))

    def test_static_app_headers_manifest_and_no_external_runtime_assets(self) -> None:
        status, health, headers = self.request("GET", "/health", origin=False)
        self.assertEqual((status, health), (200, {"protocol": {"max": 1, "min": 1}, "status": "ok"}))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        with urllib.request.urlopen(self.origin + "/app/", timeout=5) as response:
            html = response.read().decode()
            static_headers = dict(response.headers)
        self.assertIn("default-src 'self'", static_headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-", static_headers["Content-Security-Policy"])
        self.assertEqual(static_headers["X-Frame-Options"], "DENY")
        self.assertEqual(static_headers["Referrer-Policy"], "no-referrer")
        self.assertIn("camera=()", static_headers["Permissions-Policy"])
        self.assertIn("microphone=()", static_headers["Permissions-Policy"])
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        for asset in ("app.v1013.js", "model.v1013.js", "app.v1013.css", "sw.v1013.js"):
            with urllib.request.urlopen(self.origin + "/app/" + asset, timeout=5) as response:
                content = response.read().decode()
                self.assertNotIn("https://", content)
                self.assertNotIn("http://", content)
        status, manifest, _ = self.request("GET", "/app/manifest.webmanifest", origin=False)
        self.assertEqual(status, 200)
        self.assertEqual(manifest["display"], "standalone")

    def test_theme_previews_are_authenticated_bounded_and_policy_gated(self) -> None:
        device, credential = self.pair()
        status, snapshot, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
        self.assertEqual(status, 200)
        theme = snapshot["themes"]["items"][0]
        self.assertTrue(theme["previewAvailable"])
        path = f'/api/v1/theme-previews/{theme["id"]}'

        status, body, headers = self.request_bytes(path, credential)
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLessEqual(len(body), 2 * 1024 * 1024)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Disposition"], "inline")

        status, unauthenticated, _ = self.request_bytes(path)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(unauthenticated)["error"]["code"], "not_authenticated")
        status, unavailable, _ = self.request_bytes("/api/v1/theme-previews/theme_missing", credential)
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(unavailable)["error"]["code"], "theme_unavailable")

        self.control("device-rescope", device["id"], "--scopes", "read:desktop")
        status, denied, _ = self.request_bytes(path, credential)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(denied)["error"]["details"]["requiredScope"], "control:theme")
        self.control("device-rescope", device["id"], "--scopes", "read:desktop,control:theme")
        self.control("pause", "on")
        status, paused, _ = self.request_bytes(path, credential)
        self.assertEqual(status, 423)
        self.assertEqual(json.loads(paused)["error"]["code"], "sidecar_paused")
        self.control("pause", "off")
        self.control("device-rescope", device["id"], "--scopes", "read:desktop,control:theme,control:lock")
        status, latest, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
        self.assertEqual(status, 200)
        status, _, _ = self.request("POST", "/api/v1/actions", {
            "requestId": "req_preview_lock", "action": "desktop.lock", "parameters": {}, "expectedSeq": latest["seq"],
        }, credential)
        self.assertEqual(status, 200)
        status, locked, _ = self.request_bytes(path, credential)
        self.assertEqual(status, 423)
        self.assertEqual(json.loads(locked)["error"]["code"], "desktop_locked")
        self.control("device-revoke", device["id"])
        status, revoked, _ = self.request_bytes(path, credential)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(revoked)["error"]["code"], "not_authenticated")

    def test_pair_action_replay_pause_rescope_and_revoke(self) -> None:
        device, credential = self.pair()
        status, snapshot, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
        self.assertEqual(status, 200)
        request = {"requestId": "req_http_workspace", "action": "workspace.focus", "parameters": {"workspaceId": "ws_2"}, "expectedSeq": snapshot["seq"]}
        status, result, _ = self.request("POST", "/api/v1/actions", request, credential)
        self.assertEqual(status, 200)
        _, replay, _ = self.request("POST", "/api/v1/actions", request, credential)
        self.assertEqual(result, replay)
        _, after, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
        self.assertEqual(result["seq"], after["seq"])
        self.assertTrue(after["workspaces"][1]["active"])
        self.control("device-rescope", device["id"], "--scopes", "read:desktop")
        status, denied, _ = self.request("POST", "/api/v1/actions", {"requestId": "req_denied", "action": "media.playPause", "parameters": {}}, credential)
        self.assertEqual((status, denied["error"]["code"]), (403, "permission_denied"))
        self.control("pause", "on")
        _, paused, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
        self.assertEqual(set(paused), {"desktop", "protocol", "seq", "server", "session"})
        self.assertTrue(paused["session"]["paused"])
        self.control("pause", "off")
        self.control("device-revoke", device["id"])
        status, revoked, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
        self.assertEqual((status, revoked["error"]["code"]), (401, "not_authenticated"))

    def test_v01_phone_requests_new_capabilities_with_same_credential(self) -> None:
        legacy = "read:desktop,control:workspace,control:window-focus,control:media,control:focus-mode"
        device, credential = self.pair(legacy)
        status, pending, _ = self.request("POST", "/api/v1/capabilities/request", {
            "requestId": "cap_http_upgrade",
            "scopes": ["control:window-move", "control:theme", "control:lock"],
        }, credential)
        self.assertEqual((status, pending["status"]), (200, "pending"))
        local = self.control("status")
        self.assertEqual(local["capabilityRequests"][0]["deviceId"], device["id"])
        self.assertEqual(len(local["capabilityRequests"][0]["labels"]), 3)
        approved = self.control("capability-approve", pending["requestId"])
        self.assertEqual(approved["id"], device["id"])
        status, capability, _ = self.request("POST", "/api/v1/capabilities/status", {
            "requestId": pending["requestId"],
        }, credential)
        self.assertEqual((status, capability["status"]), (200, "approved"))
        status, snapshot, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
        self.assertEqual(status, 200)
        self.assertTrue({"control:window-move", "control:theme", "control:lock"}.issubset(snapshot["session"]["permissions"]))
        status, moved, _ = self.request("POST", "/api/v1/actions", {
            "requestId": "req_http_move", "action": "window.moveToWorkspace",
            "parameters": {"windowId": "win_editor", "workspaceId": "ws_2"},
        }, credential)
        self.assertEqual(status, 200)
        self.assertEqual(moved["result"]["workspaceId"], "ws_2")

    def test_drop_scope_stream_commit_collision_and_live_policy_cleanup(self) -> None:
        device, credential = self.pair()
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        declared = {
            "name": "../\u202e generic screenshot.png",
            "mediaType": "image/png",
            "size": len(png),
            "sha256": hashlib.sha256(png).hexdigest(),
        }
        status, denied, _ = self.request("POST", "/api/v1/inbox/intents", {
            "requestId": "drop_without_scope", "files": [declared],
        }, credential)
        self.assertEqual((status, denied["error"]["details"]["requiredScope"]), (403, "write:inbox"))

        status, capability, _ = self.request("POST", "/api/v1/capabilities/request", {
            "requestId": "drop_capability", "scopes": ["write:inbox"],
        }, credential)
        self.assertEqual((status, capability["status"]), (200, "pending"))
        self.assertEqual(self.control("status")["capabilityRequests"][0]["labels"], ["Send allowed files to Sidecar Inbox"])
        self.control("capability-approve", capability["requestId"])

        def create_intent(request_id: str, file: dict = declared) -> dict:
            status, intent, _ = self.request("POST", "/api/v1/inbox/intents", {
                "requestId": request_id, "files": [file],
            }, credential)
            self.assertEqual(status, 201, intent)
            return intent

        first = create_intent("drop_first")
        upload_path = "/api/v1/inbox/uploads/" + first["files"][0]["uploadId"]
        status, uploaded, _ = self.request("POST", upload_path, token=credential, raw=png, content_type="image/png")
        self.assertEqual((status, uploaded["receipt"]["status"]), (201, "saved"))
        first_target = self.home / "Downloads" / "Sidecar" / uploaded["receipt"]["name"]
        self.assertEqual(first_target.read_bytes(), png)
        self.assertEqual(first_target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(first_target.stat().st_nlink, 1)
        self.assertNotIn("/", first_target.name)
        self.assertNotIn("\u202e", first_target.name)
        self.assertEqual(first_target.resolve().parent, (self.home / "Downloads" / "Sidecar").resolve())

        status, replay, _ = self.request("POST", upload_path, token=credential, raw=png, content_type="image/png")
        self.assertEqual((status, replay["replay"], replay["receipt"]), (200, True, uploaded["receipt"]))
        second = create_intent("drop_collision")
        status, collision, _ = self.request(
            "POST", "/api/v1/inbox/uploads/" + second["files"][0]["uploadId"],
            token=credential, raw=png, content_type="image/png",
        )
        self.assertEqual(status, 201)
        self.assertNotEqual(collision["receipt"]["name"], uploaded["receipt"]["name"])
        self.assertEqual((self.home / "Downloads" / "Sidecar" / collision["receipt"]["name"]).read_bytes(), png)

        mismatched = dict(declared, sha256=hashlib.sha256(b"%PDF-1.7\n%%EOF").hexdigest(), size=14)
        bad = create_intent("drop_bad_magic", mismatched)
        status, rejected, _ = self.request(
            "POST", "/api/v1/inbox/uploads/" + bad["files"][0]["uploadId"],
            token=credential, raw=b"%PDF-1.7\n%%EOF", content_type="image/png",
        )
        self.assertEqual((status, rejected["error"]["code"]), (415, "upload_rejected"))
        staging = self.home / "Downloads" / "Sidecar" / ".sidecar-staging"
        self.assertEqual(list(staging.iterdir()), [])

        text = (b"safe text line\n" * 8192)
        text_file = {"name": "notes.txt", "mediaType": "text/plain", "size": len(text), "sha256": hashlib.sha256(text).hexdigest()}
        active = create_intent("drop_pause_midstream", text_file)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("POST", "/api/v1/inbox/uploads/" + active["files"][0]["uploadId"])
        connection.putheader("Authorization", f"Bearer {credential}")
        connection.putheader("Origin", self.origin)
        connection.putheader("Content-Type", "text/plain")
        connection.putheader("Content-Length", str(len(text)))
        connection.endheaders()
        connection.send(text[:65536])
        time.sleep(0.05)
        self.control("pause", "on")
        try:
            connection.send(text[65536:])
            response = connection.getresponse()
            response_body = response.read()
            self.assertEqual(response.status, 423, response_body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            connection.close()
        self.assertEqual(list(staging.iterdir()), [])
        self.assertTrue(first_target.is_file(), "completed user files survive pause and cleanup")
        self.control("pause", "off")

        self._stop()
        self._start()
        self.assertTrue(first_target.is_file(), "completed user files survive helper restart")
        self.assertEqual(list(staging.iterdir()), [])

    def test_drop_http_rejects_chunking_wrong_headers_and_partial_disconnect(self) -> None:
        _, credential = self.pair(",".join((*DEFAULT_SCOPES, "write:inbox")))
        body = b"bounded text"
        declared = {"name": "generic.txt", "mediaType": "text/plain", "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}

        def intent(request_id: str) -> str:
            status, result, _ = self.request("POST", "/api/v1/inbox/intents", {"requestId": request_id, "files": [declared]}, credential)
            self.assertEqual(status, 201)
            return result["files"][0]["uploadId"]

        upload_id = intent("drop_chunked")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(
            "POST", "/api/v1/inbox/uploads/" + upload_id,
            body=[body],
            headers={"Authorization": f"Bearer {credential}", "Origin": self.origin, "Content-Type": "text/plain"},
            encode_chunked=True,
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        connection.close()

        upload_id = intent("drop_content_range")
        status, rejected, _ = self.request(
            "POST", "/api/v1/inbox/uploads/" + upload_id,
            token=credential, raw=body, content_type="text/plain", extra_headers={"Content-Range": "bytes 0-11/12"},
        )
        self.assertEqual((status, rejected["error"]["code"]), (400, "bad_request"))

        upload_id = intent("drop_partial")
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        request = (
            f"POST /api/v1/inbox/uploads/{upload_id} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\nAuthorization: Bearer {credential}\r\n"
            f"Origin: {self.origin}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode()
        raw.sendall(request + body[:3])
        raw.close()
        staging = self.home / "Downloads" / "Sidecar" / ".sidecar-staging"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and list(staging.iterdir()):
            time.sleep(0.02)
        self.assertEqual(list(staging.iterdir()), [])

    def test_origin_auth_content_type_and_parser_fail_closed(self) -> None:
        _, credential = self.pair("read:desktop,control:media")
        status, body, _ = self.request("POST", "/api/v1/actions", {"requestId": "req_origin", "action": "media.playPause", "parameters": {}}, credential, origin=False)
        self.assertEqual((status, body["error"]["code"]), (403, "bad_request"))
        status, body, _ = self.request("POST", "/api/v1/actions", {"requestId": "req_form", "action": "media.playPause", "parameters": {}}, credential, content_type="application/x-www-form-urlencoded")
        self.assertEqual(status, 415)
        status, body, _ = self.request("POST", "/api/v1/actions", token=credential, raw=b'{"requestId":"x","requestId":"y"}')
        self.assertEqual(status, 400)
        status, body, _ = self.request("POST", "/api/v1/actions", token=credential, raw=("[" * 33 + "0" + "]" * 33).encode())
        self.assertEqual(status, 400)
        status, body, _ = self.request("GET", f"/api/v1/snapshot?token={credential}", origin=False)
        self.assertEqual(status, 400)
        status, body, _ = self.request("GET", "/api/v1/snapshot", origin=False, extra_headers={"Cookie": f"token={credential}"})
        self.assertEqual((status, body["error"]["code"]), (401, "not_authenticated"))
        status, body, _ = self.request("OPTIONS", "/api/v1/actions", origin=False)
        self.assertEqual(status, 405)
        duplicate = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        duplicate.putrequest("POST", "/api/v1/actions")
        duplicate.putheader("Authorization", f"Bearer {credential}")
        duplicate.putheader("Origin", self.origin)
        duplicate.putheader("Origin", self.origin)
        duplicate.putheader("Content-Type", "application/json")
        duplicate.putheader("Content-Length", "2")
        duplicate.endheaders(b"{}")
        self.assertEqual(duplicate.getresponse().status, 400)
        duplicate.close()

        ranged = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        ranged.request("GET", "/app/", headers={"Range": "bytes=0-5"})
        response = ranged.getresponse()
        self.assertEqual((response.status, response.getheader("Connection")), (400, "close"))
        response.read()
        ranged.close()

        pipelined = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        pipelined.sendall((
            f"POST /api/v1/actions HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            f"Origin: {self.origin}\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{{}}"
            f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n"
        ).encode())
        received = b""
        while True:
            block = pipelined.recv(4096)
            if not block:
                break
            received += block
        pipelined.close()
        self.assertEqual(received.count(b"HTTP/1.1"), 1)
        self.assertIn(b"Connection: close", received)

    def test_runtime_status_logs_and_diagnostics_never_contain_secret(self) -> None:
        opened = self.control("pair-open")
        secret = opened["pairUrl"].split("#", 1)[1]
        time.sleep(1.1)
        runtime_status = (self.runtime / "omarchy-sidecar" / "status.json").read_text()
        self.assertNotIn(secret, runtime_status)
        self.assertNotIn("qrDataUrl", runtime_status)
        self.assertNotIn("pairUrl", runtime_status)
        diagnostics = json.dumps(self.control("diagnostics"))
        self.assertNotIn(secret, diagnostics)
        self.assertNotIn("pending", diagnostics)
        assert self.process is not None
        self.assertNotIn(secret, " ".join(self.process.args))

    def test_stream_limit_and_immediate_self_unpair(self) -> None:
        device, credential = self.pair()
        streams: list[tuple[http.client.HTTPConnection, http.client.HTTPResponse]] = []
        try:
            for _ in range(4):
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
                connection.request("GET", "/api/v1/events", headers={"Authorization": f"Bearer {credential}"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                streams.append((connection, response))
            fifth = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
            fifth.request("GET", "/api/v1/events", headers={"Authorization": f"Bearer {credential}"})
            response = fifth.getresponse()
            self.assertEqual(response.status, 429)
            fifth.close()
            status, body, _ = self.request("POST", "/api/v1/session/unpair", {"requestId": "req_self_unpair"}, credential)
            self.assertEqual((status, body["status"]), (200, "revoked"))
            status, _, _ = self.request("GET", "/api/v1/snapshot", token=credential, origin=False)
            self.assertEqual(status, 401)
            self.assertEqual(self.control("status")["deviceCount"], 0)
        finally:
            for connection, response in streams:
                response.close()
                connection.close()

    def test_listener_is_loopback_only_and_hot_restart_reclaims_port(self) -> None:
        listeners = subprocess.check_output(["ss", "-ltn"], text=True)
        matching = [line for line in listeners.splitlines() if f":{self.port}" in line]
        self.assertEqual(len(matching), 1)
        self.assertIn(f"127.0.0.1:{self.port}", matching[0])
        self.assertNotIn(f"0.0.0.0:{self.port}", matching[0])
        self._stop()
        self._start()
        status, health, _ = self.request("GET", "/health", origin=False)
        self.assertEqual((status, health["status"]), (200, "ok"))


if __name__ == "__main__":
    unittest.main()
