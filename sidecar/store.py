"""Owner-only durable paired-device state."""

from __future__ import annotations

import base64
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .constants import ALL_SCOPES, MAX_DEVICES, PROTOCOL
from .security import (
    SCRYPT_DKLEN,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    create_verifier,
    credential_lookup,
    new_credential,
    verify_credential,
)
from .util import normalize_id, normalize_name, normalize_platform, parse_rfc3339, state_directory, utc_now


class DeviceStore:
    SCHEMA_VERSION = 2
    _PRIVATE_FIELDS = {"verifier", "credentialLookup", "clientInstanceHash"}

    STATE_FILE = "devices.json"

    def __init__(self, state_root: Path):
        # The directory is reached by the Store block's descriptor walk on
        # every transaction (util.state_transaction); state_root is the base
        # the walk resolves the same way, kept for the paths in messages.
        self.state_root = state_root
        self.root = state_root / "omarchy-sidecar"
        self.path = self.root / self.STATE_FILE
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"schemaVersion": self.SCHEMA_VERSION, "paused": False, "devices": []}
        self._last_touch_write: dict[str, float] = {}
        self.quarantined_path: str | None = None
        self.load()

    def load(self) -> None:
        with self._lock:
            state = state_directory(self.state_root)
            result = state.read(self.STATE_FILE)
            self.path = Path(state.path) / self.STATE_FILE
            if result["state"] == "missing":
                return
            try:
                if result["state"] != "ok":
                    raise ValueError(result.get("reason", result["state"]))
                loaded = result["value"]
                migrated = self._migrate_state(loaded)
                self._validate_state(loaded)
                self._state = loaded
                if migrated:
                    self._persist()
            except Exception:
                # A file that is not ours to trust (a link, another owner,
                # over the cap, not the schema) is moved aside by name in the
                # directory descriptor, never followed, and the daemon starts
                # paused with no devices.
                quarantine = f"devices.corrupt.{secrets.token_hex(6)}.json"
                state.move_aside(self.STATE_FILE, quarantine)
                self.quarantined_path = str(Path(state.path) / quarantine)
                self._state = {"schemaVersion": self.SCHEMA_VERSION, "paused": True, "devices": []}
                self._persist()

    def _migrate_state(self, value: Any) -> bool:
        """Add private browser-instance bindings without inventing identity."""
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            return False
        if set(value) != {"schemaVersion", "paused", "devices"} or not isinstance(value.get("devices"), list):
            return False
        legacy_fields = {
            "id", "credentialLookup", "name", "platform", "scopes", "createdAt",
            "lastSeenAt", "verifier", "revoked",
        }
        if any(not isinstance(device, dict) or set(device) != legacy_fields for device in value["devices"]):
            return False
        for device in value["devices"]:
            device["clientInstanceHash"] = None
        value["schemaVersion"] = self.SCHEMA_VERSION
        return True

    def _validate_state(self, value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {"schemaVersion", "paused", "devices"}:
            raise ValueError("invalid state shape")
        if value["schemaVersion"] != self.SCHEMA_VERSION or not isinstance(value["paused"], bool):
            raise ValueError("unsupported state schema")
        devices = value["devices"]
        if not isinstance(devices, list) or len(devices) > MAX_DEVICES:
            raise ValueError("invalid device collection")
        seen_ids: set[str] = set()
        seen_lookups: set[str] = set()
        for device in devices:
            required = {
                "id", "credentialLookup", "clientInstanceHash", "name", "platform",
                "scopes", "createdAt", "lastSeenAt", "verifier", "revoked",
            }
            if not isinstance(device, dict) or set(device) != required:
                raise ValueError("invalid device record")
            normalize_id(device["id"], "device id")
            normalize_name(device["name"])
            normalize_platform(device["platform"])
            if device["id"] in seen_ids or device["credentialLookup"] in seen_lookups:
                raise ValueError("duplicate device record")
            seen_ids.add(device["id"])
            seen_lookups.add(device["credentialLookup"])
            if (
                not isinstance(device["scopes"], list)
                or len(device["scopes"]) != len(set(device["scopes"]))
                or any(scope not in ALL_SCOPES for scope in device["scopes"])
            ):
                raise ValueError("invalid scopes")
            if not isinstance(device["revoked"], bool) or not isinstance(device["verifier"], dict):
                raise ValueError("invalid credential record")
            verifier = device["verifier"]
            if set(verifier) != {"algorithm", "salt", "digest", "n", "r", "p", "dklen"}:
                raise ValueError("invalid verifier shape")
            if (verifier["algorithm"], verifier["n"], verifier["r"], verifier["p"], verifier["dklen"]) != (
                "scrypt", SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN
            ):
                raise ValueError("unsupported verifier parameters")
            if not isinstance(device["credentialLookup"], str) or not re.fullmatch(r"[0-9a-f]{32}", device["credentialLookup"]):
                raise ValueError("invalid credential lookup")
            if device["clientInstanceHash"] is not None and (
                not isinstance(device["clientInstanceHash"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", device["clientInstanceHash"])
            ):
                raise ValueError("invalid client instance binding")
            try:
                salt = base64.b64decode(verifier["salt"], validate=True)
                digest = base64.b64decode(verifier["digest"], validate=True)
            except (TypeError, ValueError) as error:
                raise ValueError("invalid verifier encoding") from error
            if len(salt) != 16 or len(digest) != SCRYPT_DKLEN:
                raise ValueError("invalid verifier length")
            parse_rfc3339(device["createdAt"])
            parse_rfc3339(device["lastSeenAt"])

    def _persist(self) -> None:
        result = state_directory(self.state_root).write(self.STATE_FILE, self._state)
        if result["state"] != "ok":
            raise RuntimeError(f"state write {result['state']}: {result.get('reason', '')}")

    @property
    def paused(self) -> bool:
        with self._lock:
            return bool(self._state["paused"])

    def set_paused(self, value: bool) -> None:
        with self._lock:
            self._state["paused"] = bool(value)
            self._persist()

    def list_devices(self, include_revoked: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for device in self._state["devices"]:
                if device["revoked"] and not include_revoked:
                    continue
                result.append(self._public(device))
            return result

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        normalize_id(device_id, "device id")
        with self._lock:
            device = next(
                (item for item in self._state["devices"] if item["id"] == device_id and not item["revoked"]),
                None,
            )
            if device is None:
                return None
            return self._public(device)

    def create_device(
        self,
        name: str,
        platform: str,
        scopes: list[str],
    ) -> tuple[dict[str, Any], str]:
        device, credential, _replaced = self.create_or_replace_device(name, platform, scopes, None)
        return device, credential

    def create_or_replace_device(
        self,
        name: str,
        platform: str,
        scopes: list[str],
        client_instance_hash: str | None,
    ) -> tuple[dict[str, Any], str, list[str]]:
        if not isinstance(scopes, list):
            raise ValueError("scopes must be a list")
        normalized_scopes = list(dict.fromkeys(scopes))
        if len(normalized_scopes) != len(scopes) or any(scope not in ALL_SCOPES for scope in normalized_scopes):
            raise ValueError("invalid scopes")
        if client_instance_hash is not None and (
            not isinstance(client_instance_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", client_instance_hash)
        ):
            raise ValueError("invalid client instance binding")
        with self._lock:
            replaced = [
                device for device in self._state["devices"]
                if client_instance_hash is not None and device["clientInstanceHash"] == client_instance_hash
            ]
            active = [device for device in self._state["devices"] if not device["revoked"] and device not in replaced]
            if len(active) >= MAX_DEVICES:
                raise ValueError("device limit reached")
            lookup, credential = new_credential()
            now = utc_now()
            device = {
                "id": f"dev_{secrets.token_urlsafe(16)}",
                "credentialLookup": lookup,
                "clientInstanceHash": client_instance_hash,
                "name": normalize_name(name),
                "platform": normalize_platform(platform),
                "scopes": normalized_scopes,
                "createdAt": now,
                "lastSeenAt": now,
                "verifier": create_verifier(credential),
                "revoked": False,
            }
            replaced_ids = [item["id"] for item in replaced]
            self._state["devices"] = [item for item in self._state["devices"] if item not in replaced]
            for device_id in replaced_ids:
                self._last_touch_write.pop(device_id, None)
            self._state["devices"].append(device)
            self._persist()
            return self._public(device), credential, replaced_ids

    def bind_client_instance(self, device_id: str, client_instance_hash: str) -> tuple[dict[str, Any], list[str], bool, bool]:
        """Bind a legacy credential and retain the newest credential deterministically."""
        normalize_id(device_id, "device id")
        if not isinstance(client_instance_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", client_instance_hash):
            raise ValueError("invalid client instance binding")
        with self._lock:
            current = next((item for item in self._state["devices"] if item["id"] == device_id), None)
            if current is None:
                raise KeyError("device not found")
            if current["clientInstanceHash"] not in {None, client_instance_hash}:
                raise ValueError("device is already bound to another client instance")
            if current["clientInstanceHash"] == client_instance_hash and not any(
                item is not current and item["clientInstanceHash"] == client_instance_hash
                for item in self._state["devices"]
            ):
                return self._public(current), [], True, False
            candidates = [
                item for item in self._state["devices"]
                if item is current or item["clientInstanceHash"] == client_instance_hash
            ]
            winner = max(
                candidates,
                key=lambda item: (parse_rfc3339(item["createdAt"]), self._state["devices"].index(item)),
            )
            winner["clientInstanceHash"] = client_instance_hash
            removed = [item["id"] for item in candidates if item is not winner]
            self._state["devices"] = [item for item in self._state["devices"] if item["id"] not in removed]
            for removed_id in removed:
                self._last_touch_write.pop(removed_id, None)
            self._persist()
            return self._public(winner), removed, winner["id"] == device_id, True

    def authenticate(self, credential: str, touch: bool = True) -> dict[str, Any] | None:
        lookup = credential_lookup(credential)
        if lookup is None:
            return None
        with self._lock:
            device = next((item for item in self._state["devices"] if item["credentialLookup"] == lookup), None)
            if device is None or device["revoked"] or not verify_credential(credential, device["verifier"]):
                return None
            if touch:
                device["lastSeenAt"] = utc_now()
                now = time.monotonic()
                if now - self._last_touch_write.get(device["id"], 0.0) >= 60:
                    self._persist()
                    self._last_touch_write[device["id"]] = now
            return self._public(device)

    def rename(self, device_id: str, name: str) -> dict[str, Any]:
        return self._update(device_id, lambda device: device.__setitem__("name", normalize_name(name)))

    def rescope(self, device_id: str, scopes: list[str]) -> dict[str, Any]:
        normalized = list(dict.fromkeys(scopes))
        if any(scope not in ALL_SCOPES for scope in normalized):
            raise ValueError("unknown scope")
        return self._update(device_id, lambda device: device.__setitem__("scopes", normalized))

    def revoke(self, device_id: str) -> dict[str, Any]:
        normalize_id(device_id, "device id")
        with self._lock:
            device = next((item for item in self._state["devices"] if item["id"] == device_id and not item["revoked"]), None)
            if device is None:
                raise KeyError("device not found")
            public = self._public(device)
            public["revoked"] = True
            self._state["devices"].remove(device)
            self._last_touch_write.pop(device_id, None)
            self._persist()
            return public

    def _update(self, device_id: str, mutator: Any) -> dict[str, Any]:
        normalize_id(device_id, "device id")
        with self._lock:
            device = next((item for item in self._state["devices"] if item["id"] == device_id and not item["revoked"]), None)
            if device is None:
                raise KeyError("device not found")
            mutator(device)
            self._persist()
            return self._public(device)

    def _public(self, device: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in device.items() if key not in self._PRIVATE_FIELDS}

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schemaVersion": self.SCHEMA_VERSION,
                "paused": self._state["paused"],
                "deviceCount": len([device for device in self._state["devices"] if not device["revoked"]]),
                "quarantinedState": self.quarantined_path is not None,
                "protocol": PROTOCOL,
            }
