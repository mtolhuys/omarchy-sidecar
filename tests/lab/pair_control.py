#!/usr/bin/env python3
"""Pair and exercise production adapters while keeping credentials in memory."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


ORIGIN = os.environ.get("SIDECAR_LAB_ORIGIN", "https://sidecar-lab.example.ts.net:48719")
BASE = os.environ.get("SIDECAR_LAB_BASE", "http://127.0.0.1:47991")
CLIENT_INSTANCE_ID = "sci1_" + "L" * 43


class SafeHttpError(RuntimeError):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


def request(path: str, payload: dict | None = None, credential: str = "") -> dict:
    headers = {"Accept": "application/json"}
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        headers.update({"Content-Type": "application/json", "Origin": ORIGIN})
        method = "POST"
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(BASE + path, data=data, headers=headers, method=method),
            timeout=5,
        ) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            body = json.load(error)
            code = str(body.get("error", {}).get("code", "unknown"))[:64]
        except Exception:
            code = "unknown"
        finally:
            error.close()
        raise SafeHttpError(error.code, code) from None


def create_drop_intent(credential: str, request_id: str, name: str, body: bytes) -> dict:
    return request(
        "/api/v1/inbox/intents",
        {
            "requestId": request_id,
            "files": [{
                "name": name, "mediaType": "text/plain", "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }],
        },
        credential,
    )


def upload_connection(upload_id: str, credential: str, size: int) -> http.client.HTTPConnection:
    parsed = urlsplit(BASE)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    connection.putrequest("POST", "/api/v1/inbox/uploads/" + upload_id)
    connection.putheader("Authorization", f"Bearer {credential}")
    connection.putheader("Origin", ORIGIN)
    connection.putheader("Content-Type", "text/plain")
    connection.putheader("Content-Length", str(size))
    connection.endheaders()
    return connection


def upload_text(credential: str, upload_id: str, body: bytes) -> tuple[int, dict]:
    connection = upload_connection(upload_id, credential, len(body))
    try:
        connection.send(body)
        response = connection.getresponse()
        payload = json.load(response)
        status = response.status
        response.close()
        return status, payload
    finally:
        connection.close()


def preview_is_safe(theme_id: str, credential: str) -> bool:
    item = urllib.request.Request(
        BASE + "/api/v1/theme-previews/" + theme_id,
        headers={"Authorization": f"Bearer {credential}"},
        method="GET",
    )
    with urllib.request.urlopen(item, timeout=5) as response:
        body = response.read(2 * 1024 * 1024 + 1)
        mime = response.headers.get_content_type()
        signatures = {
            "image/png": (b"\x89PNG\r\n\x1a\n",),
            "image/jpeg": (b"\xff\xd8\xff",),
            "image/webp": (b"RIFF",),
            "image/gif": (b"GIF87a", b"GIF89a"),
            "image/bmp": (b"BM",),
        }
        return (
            0 < len(body) <= 2 * 1024 * 1024
            and mime in signatures
            and any(body.startswith(value) for value in signatures[mime])
            and response.headers.get("Cache-Control") == "private, no-store"
            and response.headers.get("X-Content-Type-Options") == "nosniff"
        )


def write_safe(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def action(credential: str, request_id: str, name: str, parameters: dict, seq: int) -> dict:
    return request(
        "/api/v1/actions",
        {"requestId": request_id, "action": name, "parameters": parameters, "expectedSeq": seq},
        credential,
    )


def replace_pairing(sidecarctl: str, pending_path: Path, result_path: Path, old_device_id: str) -> int:
    stage = "replacement_open"
    try:
        opened = json.loads(
            subprocess.run([sidecarctl, "pair-open"], check=True, capture_output=True, text=True).stdout
        )
        stage = "replacement_request"
        pending = request(
            "/api/v1/pair/request",
            {
                "secret": urlsplit(opened["pairUrl"]).fragment,
                "device": {
                    "name": "<b>Lab Phone</b>",
                    "platform": "android-web",
                    "clientVersion": "0.2.2",
                    "protocol": 1,
                    "clientInstanceId": CLIENT_INSTANCE_ID,
                },
            },
        )
        write_safe(pending_path, {"requestId": pending["requestId"]})
        stage = "replacement_approval"
        deadline = time.monotonic() + 120
        approved = None
        while time.monotonic() < deadline:
            time.sleep(max(0.8, float(pending.get("pollAfterMs", 1000)) / 1000))
            approved = request(
                "/api/v1/pair/status",
                {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
            )
            if approved.get("status") == "approved":
                break
            pending["pollAfterMs"] = approved.get("pollAfterMs", 1200)
        if not approved or approved.get("status") != "approved":
            raise RuntimeError("replacement_approval_timeout")
        credential = approved["credential"]
        snapshot = request("/api/v1/snapshot", credential=credential)
        new_device_id = snapshot["session"]["deviceId"]
        devices = json.loads(
            subprocess.run([sidecarctl, "status"], check=True, capture_output=True, text=True).stdout
        )["devices"]
        write_safe(
            result_path,
            {
                "ok": len(devices) == 1 and devices[0]["id"] == new_device_id and new_device_id != old_device_id,
                "newDeviceId": new_device_id,
                "oldDeviceRemoved": all(item["id"] != old_device_id for item in devices),
                "deviceCount": len(devices),
            },
        )
        credential = ""
        return 0
    except Exception as error:
        write_safe(result_path, {"ok": False, "error": "replacement_failed", "stage": stage, "errorClass": type(error).__name__})
        return 1


def marketing_drop(sidecarctl: str) -> int:
    opened = json.loads(subprocess.run([sidecarctl, "pair-open"], check=True, capture_output=True, text=True).stdout)
    pending = request(
        "/api/v1/pair/request",
        {
            "secret": urlsplit(opened["pairUrl"]).fragment,
            "device": {"name": "Demo phone", "platform": "android-web", "clientVersion": "0.2.2", "protocol": 1},
        },
    )
    subprocess.run(
        [sidecarctl, "pair-approve", pending["requestId"], "--scopes", "read:desktop,write:inbox"],
        check=True, capture_output=True,
    )
    approved = request(
        "/api/v1/pair/status",
        {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
    )
    credential = approved["credential"]
    body = b"Sidecar demo fixture."
    intent = create_drop_intent(credential, "drop_marketing", "Demo note.txt", body)
    status, uploaded = upload_text(credential, intent["files"][0]["uploadId"], body)
    credential = ""
    return 0 if status == 201 and uploaded.get("receipt", {}).get("status") == "saved" else 1


def browser_fixture(sidecarctl: str, output: Path) -> int:
    opened = json.loads(subprocess.run([sidecarctl, "pair-open"], check=True, capture_output=True, text=True).stdout)
    pending = request(
        "/api/v1/pair/request",
        {
            "secret": urlsplit(opened["pairUrl"]).fragment,
            "device": {"name": "Responsive fixture", "platform": "desktop-web", "clientVersion": "0.2.2", "protocol": 1},
        },
    )
    subprocess.run(
        [sidecarctl, "pair-approve", pending["requestId"], "--scopes", "read:desktop,write:inbox"],
        check=True, capture_output=True,
    )
    approved = request(
        "/api/v1/pair/status",
        {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
    )
    write_safe(output, {"credential": approved["credential"]})
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--browser-fixture":
        return browser_fixture(sys.argv[2], Path(sys.argv[3]))
    if len(sys.argv) == 3 and sys.argv[1] == "--marketing":
        return marketing_drop(sys.argv[2])
    if len(sys.argv) == 6 and sys.argv[1] == "--replace":
        return replace_pairing(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])
    if len(sys.argv) != 8:
        raise SystemExit(
            "usage: pair_control.py SIDECARCTL PENDING_JSON RESULT_JSON "
            "CAPABILITY_JSON LOCK_TRIGGER LOCK_RESULT REVOKE_RESULT"
        )
    sidecarctl = sys.argv[1]
    pending_path, result_path = Path(sys.argv[2]), Path(sys.argv[3])
    capability_path, lock_trigger = Path(sys.argv[4]), Path(sys.argv[5])
    lock_result_path = Path(sys.argv[6])
    revoke_result_path = Path(sys.argv[7])
    stage = "open_pairing"
    try:
        opened = json.loads(
            subprocess.run([sidecarctl, "pair-open"], check=True, capture_output=True, text=True).stdout
        )
        secret = urlsplit(opened["pairUrl"]).fragment
        stage = "request_pairing"
        pending = request(
            "/api/v1/pair/request",
            {
                "secret": secret,
                "device": {
                    "name": "<b>Lab Phone</b>",
                    "platform": "android-web",
                    "clientVersion": "0.2.2",
                    "protocol": 1,
                    "clientInstanceId": CLIENT_INSTANCE_ID,
                },
            },
        )
        secret = ""
        write_safe(
            pending_path,
            {"requestId": pending["requestId"], "verificationPhrase": pending["verificationPhrase"]},
        )

        stage = "wait_approval"
        deadline = time.monotonic() + 120
        approved = None
        while time.monotonic() < deadline:
            time.sleep(max(0.8, float(pending.get("pollAfterMs", 1000)) / 1000))
            approved = request(
                "/api/v1/pair/status",
                {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
            )
            if approved.get("status") == "approved":
                break
            pending["pollAfterMs"] = approved.get("pollAfterMs", 1200)
        if not approved or approved.get("status") != "approved":
            raise RuntimeError("approval_timeout")

        credential = approved["credential"]
        snapshot = request("/api/v1/snapshot", credential=credential)
        original_device_id = snapshot["session"]["deviceId"]

        stage = "wait_rescope"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snapshot = request("/api/v1/snapshot", credential=credential)
            if "control:window-move" not in snapshot["session"]["permissions"]:
                break
            time.sleep(0.2)
        if "control:window-move" in snapshot["session"]["permissions"]:
            raise RuntimeError("rescope_timeout")

        stage = "capability_request"
        capability = request(
            "/api/v1/capabilities/request",
            {
                "requestId": "cap_lab_v1013",
                "scopes": ["control:window-move", "control:theme", "control:lock", "write:inbox"],
            },
            credential,
        )
        write_safe(capability_path, {"requestId": capability["requestId"], "status": capability["status"]})
        stage = "wait_capability_approval"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(max(0.8, float(capability.get("pollAfterMs", 1200)) / 1000))
            capability = request(
                "/api/v1/capabilities/status", {"requestId": capability["requestId"]}, credential,
            )
            if capability.get("status") == "approved":
                break
        if capability.get("status") != "approved":
            raise RuntimeError("capability_approval_timeout")

        snapshot = request("/api/v1/snapshot", credential=credential)
        capability_upgraded = (
            snapshot["session"]["deviceId"] == original_device_id
            and {"control:window-move", "control:theme", "control:lock", "write:inbox"}.issubset(
                snapshot["session"]["permissions"]
            )
        )
        initial_workspace = next(item for item in snapshot["workspaces"] if item["active"])
        target_workspace = next(item for item in snapshot["workspaces"] if not item["active"])

        stage = "workspace_focus"
        workspace_result = action(
            credential,
            "req_lab_workspace",
            "workspace.focus",
            {"workspaceId": target_workspace["id"]},
            snapshot["seq"],
        )
        after_workspace = request("/api/v1/snapshot", credential=credential)
        workspace_changed = any(
            item["id"] == target_workspace["id"] and item["active"]
            for item in after_workspace["workspaces"]
        )

        target_window = next(
            item for item in after_workspace["windows"]
            if item["workspaceId"] == initial_workspace["id"]
        )
        order_before = [item["id"] for item in after_workspace["windows"]]
        stage = "window_focus"
        window_result = action(
            credential,
            "req_lab_window",
            "window.focus",
            {"windowId": target_window["id"]},
            after_workspace["seq"],
        )
        after_window = request("/api/v1/snapshot", credential=credential)
        order_after = [item["id"] for item in after_window["windows"]]
        window_changed = any(
            item["id"] == target_window["id"] and item["focused"]
            for item in after_window["windows"]
        )
        focus_order_stable = order_before == order_after

        stage = "window_move"
        move_result = action(
            credential,
            "req_lab_window_move",
            "window.moveToWorkspace",
            {"windowId": target_window["id"], "workspaceId": target_workspace["id"]},
            after_window["seq"],
        )
        after_move = request("/api/v1/snapshot", credential=credential)
        window_moved = any(
            item["id"] == target_window["id"] and item["workspaceId"] == target_workspace["id"]
            for item in after_move["windows"]
        )
        move_did_not_follow = any(
            item["id"] == initial_workspace["id"] and item["active"]
            for item in after_move["workspaces"]
        )

        themes = after_move["themes"]
        preview_theme = next((item for item in themes["items"] if item.get("previewAvailable")), None)
        if preview_theme is None or not preview_is_safe(preview_theme["id"], credential):
            raise RuntimeError("theme_preview_invalid")
        alternate = next((item for item in themes["items"] if item["id"] != themes["currentId"]), None)
        if alternate is None:
            raise RuntimeError("alternate_theme_unavailable")
        original_id, original_name = themes["currentId"], themes["currentName"]
        stage = "theme_set"
        theme_result = action(
            credential, "req_lab_theme_set", "theme.set", {"themeId": alternate["id"]}, after_move["seq"]
        )
        after_theme = request("/api/v1/snapshot", credential=credential)
        theme_changed = after_theme["themes"]["currentId"] == alternate["id"]
        time.sleep(1.1)
        stage = "theme_restore"
        restore_result = action(
            credential, "req_lab_theme_restore", "theme.set", {"themeId": original_id}, after_theme["seq"]
        )
        restored = request("/api/v1/snapshot", credential=credential)
        theme_restored = (
            restored["themes"]["currentId"] == original_id
            and restored["themes"]["currentName"] == original_name
        )

        stage = "drop_commit"
        drop_body = b"Sidecar disposable-lab Drop fixture.\n"
        drop_digest = hashlib.sha256(drop_body).hexdigest()
        drop_intent = create_drop_intent(credential, "drop_lab_commit", "Lab note.txt", drop_body)
        drop_status, drop_response = upload_text(
            credential, drop_intent["files"][0]["uploadId"], drop_body,
        )
        drop_receipt = drop_response.get("receipt", {})
        drop_committed = (
            drop_status == 201 and drop_receipt.get("status") == "saved"
            and drop_receipt.get("sha256") == drop_digest
            and drop_receipt.get("size") == len(drop_body)
        )
        collision_intent = create_drop_intent(credential, "drop_lab_collision", "Lab note.txt", drop_body)
        collision_status, collision_response = upload_text(
            credential, collision_intent["files"][0]["uploadId"], drop_body,
        )
        collision_receipt = collision_response.get("receipt", {})
        no_overwrite = (
            collision_status == 201 and collision_receipt.get("sha256") == drop_digest
            and collision_receipt.get("name") != drop_receipt.get("name")
        )

        stage = "drop_pause_midstream"
        policy_body = b"bounded lab stream\n" * 32768
        pause_intent = create_drop_intent(credential, "drop_lab_pause", "Paused note.txt", policy_body)
        pause_connection = upload_connection(pause_intent["files"][0]["uploadId"], credential, len(policy_body))
        pause_denied = False
        try:
            pause_connection.send(policy_body[:65536])
            subprocess.run([sidecarctl, "pause", "on"], check=True, capture_output=True)
            try:
                pause_connection.send(policy_body[65536:])
                pause_response = pause_connection.getresponse()
                pause_denied = pause_response.status == 423
                pause_response.read()
                pause_response.close()
            except (BrokenPipeError, ConnectionResetError):
                pause_denied = True
        finally:
            pause_connection.close()
            subprocess.run([sidecarctl, "pause", "off"], check=True, capture_output=True)

        stage = "drop_rescope_midstream"
        rescope_intent = create_drop_intent(credential, "drop_lab_rescope", "Rescoped note.txt", policy_body)
        rescope_connection = upload_connection(rescope_intent["files"][0]["uploadId"], credential, len(policy_body))
        rescope_denied = False
        full_scopes = "read:desktop,control:workspace,control:window-focus,control:window-move,control:media,control:theme,control:lock,write:inbox"
        without_drop = "read:desktop,control:workspace,control:window-focus,control:window-move,control:media,control:theme,control:lock"
        try:
            rescope_connection.send(policy_body[:65536])
            subprocess.run(
                [sidecarctl, "device-rescope", original_device_id, "--scopes", without_drop],
                check=True, capture_output=True,
            )
            try:
                rescope_connection.send(policy_body[65536:])
                rescope_response = rescope_connection.getresponse()
                rescope_denied = rescope_response.status == 403
                rescope_response.read()
                rescope_response.close()
            except (BrokenPipeError, ConnectionResetError):
                rescope_denied = True
        finally:
            rescope_connection.close()
            subprocess.run(
                [sidecarctl, "device-rescope", original_device_id, "--scopes", full_scopes],
                check=True, capture_output=True,
            )

        checks = (
            capability_upgraded,
            workspace_changed,
            window_changed,
            focus_order_stable,
            window_moved,
            move_did_not_follow,
            theme_changed,
            theme_restored,
            drop_committed,
            no_overwrite,
            pause_denied,
            rescope_denied,
        )
        write_safe(
            result_path,
            {
                "ok": all(checks),
                "capabilityUpgraded": capability_upgraded,
                "workspaceChanged": workspace_changed,
                "windowChanged": window_changed,
                "focusOrderStable": focus_order_stable,
                "windowMoved": window_moved,
                "moveDidNotFollow": move_did_not_follow,
                "themePreviewDelivered": True,
                "themeChanged": theme_changed,
                "themeRestored": theme_restored,
                "dropCommitted": drop_committed,
                "dropName": drop_receipt.get("name", ""),
                "dropSha256": drop_digest,
                "dropBytes": len(drop_body),
                "collisionName": collision_receipt.get("name", ""),
                "noOverwrite": no_overwrite,
                "pauseMidUploadDenied": pause_denied,
                "rescopeMidUploadDenied": rescope_denied,
                "finalWorkspaceLabel": initial_workspace["label"],
                "resultsCompleted": all(
                    item.get("status") == "completed"
                    for item in (workspace_result, window_result, move_result, theme_result, restore_result)
                ),
            },
        )

        stage = "wait_lock_trigger"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not lock_trigger.exists():
            time.sleep(0.1)
        if not lock_trigger.exists():
            raise RuntimeError("lock_trigger_timeout")

        stage = "lock_mid_upload"
        before_lock = request("/api/v1/snapshot", credential=credential)
        lock_intent = create_drop_intent(credential, "drop_lab_lock", "Locked note.txt", policy_body)
        lock_connection = upload_connection(lock_intent["files"][0]["uploadId"], credential, len(policy_body))
        lock_connection.send(policy_body[:65536])
        locked_result = action(credential, "req_lab_lock", "desktop.lock", {}, before_lock["seq"])
        if locked_result.get("result", {}).get("locked") is not True:
            raise RuntimeError("lock_action_failed")
        lock_denied = False
        try:
            try:
                lock_connection.send(policy_body[65536:])
                lock_response = lock_connection.getresponse()
                lock_denied = lock_response.status == 423
                lock_response.read()
                lock_response.close()
            except (BrokenPipeError, ConnectionResetError):
                lock_denied = True
        finally:
            lock_connection.close()
        deadline = time.monotonic() + 12
        locked = None
        while time.monotonic() < deadline:
            locked = request("/api/v1/snapshot", credential=credential)
            if locked.get("desktop", {}).get("locked"):
                break
            time.sleep(0.2)
        if not locked or not locked.get("desktop", {}).get("locked"):
            raise RuntimeError("lock_state_timeout")
        locked_redacted = set(locked) == {"desktop", "protocol", "seq", "server", "session"}
        denied = False
        try:
            action(
                credential,
                "req_lab_locked",
                "workspace.focus",
                {"workspaceId": initial_workspace["id"]},
                locked["seq"],
            )
        except SafeHttpError as error:
            denied = error.status == 423 and error.code == "desktop_locked"
        write_safe(
            lock_result_path,
            {
                "ok": locked_redacted and denied and lock_denied,
                "lockedRedacted": locked_redacted, "actionDenied": denied,
                "lockMidUploadDenied": lock_denied,
            },
        )

        stage = "wait_unlock_for_revoke"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                unlocked = request("/api/v1/snapshot", credential=credential)
                if not unlocked.get("desktop", {}).get("locked", True):
                    break
            except SafeHttpError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("unlock_timeout")

        stage = "revoke_mid_upload"
        revoke_intent = create_drop_intent(credential, "drop_lab_revoke", "Revoked note.txt", policy_body)
        revoke_connection = upload_connection(revoke_intent["files"][0]["uploadId"], credential, len(policy_body))
        revoke_denied = False
        try:
            revoke_connection.send(policy_body[:65536])
            subprocess.run([sidecarctl, "device-revoke", original_device_id], check=True, capture_output=True)
            try:
                revoke_connection.send(policy_body[65536:])
                revoke_response = revoke_connection.getresponse()
                revoke_denied = revoke_response.status == 401
                revoke_response.read()
                revoke_response.close()
            except (BrokenPipeError, ConnectionResetError):
                revoke_denied = True
        finally:
            revoke_connection.close()
        local_status = json.loads(
            subprocess.run([sidecarctl, "status"], check=True, capture_output=True, text=True).stdout
        )
        staging_clean = local_status.get("inbox", {}).get("stagingCount") == 0
        write_safe(
            revoke_result_path,
            {"ok": revoke_denied and staging_clean, "revokeMidUploadDenied": revoke_denied, "stagingClean": staging_clean},
        )
        credential = ""
        return 0
    except Exception as error:
        details = (
            {"status": error.status, "sidecarCode": error.code}
            if isinstance(error, SafeHttpError)
            else {}
        )
        write_safe(
            result_path,
            {
                "ok": False,
                "error": "lab_client_failed",
                "stage": stage,
                "errorClass": type(error).__name__,
                **details,
            },
        )
        if stage.startswith("lock") or stage == "wait_lock_trigger":
            write_safe(lock_result_path, {"ok": False, "error": "lab_client_failed", "stage": stage})
        if stage.startswith("revoke") or stage == "wait_unlock_for_revoke":
            write_safe(revoke_result_path, {"ok": False, "error": "lab_client_failed", "stage": stage})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
