from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sidecar.constants import DEFAULT_SCOPES
from sidecar.core import ApiError
from sidecar.security import client_instance_hash
from sidecar.store import DeviceStore
from tests.test_unit import CoreFixture


INSTANCE_ONE = "sci1_" + "A" * 43
INSTANCE_TWO = "sci1_" + "B" * 43


class DeviceInstanceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = DeviceStore(Path(self.temporary.name) / "state")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_schema_one_migrates_without_inventing_browser_identity(self) -> None:
        device, credential = self.store.create_device("Legacy phone", "android-web", ["read:desktop"])
        path = self.store.path
        state = json.loads(path.read_text())
        state["schemaVersion"] = 1
        state["devices"][0].pop("clientInstanceHash")
        path.write_text(json.dumps(state))

        migrated = DeviceStore(Path(self.temporary.name) / "state")

        self.assertEqual(migrated.authenticate(credential)["id"], device["id"])
        persisted = json.loads(path.read_text())
        self.assertEqual(persisted["schemaVersion"], 2)
        self.assertIsNone(persisted["devices"][0]["clientInstanceHash"])
        self.assertNotIn("clientInstanceHash", migrated.list_devices()[0])

    def test_same_browser_replaces_exact_old_credential_but_same_name_does_not(self) -> None:
        digest_one = client_instance_hash(INSTANCE_ONE)
        first, first_credential, replaced = self.store.create_or_replace_device(
            "My Android phone", "android-web", list(DEFAULT_SCOPES), digest_one,
        )
        self.assertEqual(replaced, [])
        second, second_credential, replaced = self.store.create_or_replace_device(
            "My Android phone", "android-web", list(DEFAULT_SCOPES), digest_one,
        )
        self.assertEqual(replaced, [first["id"]])
        self.assertIsNone(self.store.authenticate(first_credential))
        self.assertEqual(self.store.authenticate(second_credential)["id"], second["id"])

        other, other_credential, replaced = self.store.create_or_replace_device(
            "My Android phone", "android-web", list(DEFAULT_SCOPES), client_instance_hash(INSTANCE_TWO),
        )
        self.assertEqual(replaced, [])
        self.assertEqual(self.store.authenticate(other_credential)["id"], other["id"])
        self.assertEqual(len(self.store.list_devices()), 2)

    def test_legacy_binding_keeps_newest_and_refuses_identity_rebinding(self) -> None:
        older, older_credential = self.store.create_device("Phone", "android-web", ["read:desktop"])
        newer, newer_credential = self.store.create_device("Phone", "android-web", ["read:desktop"])
        digest = client_instance_hash(INSTANCE_ONE)
        winner, removed, current_won, changed = self.store.bind_client_instance(older["id"], digest)
        self.assertTrue(current_won)
        self.assertTrue(changed)
        self.assertEqual((winner["id"], removed), (older["id"], []))
        winner, removed, current_won, changed = self.store.bind_client_instance(newer["id"], digest)
        self.assertTrue(current_won)
        self.assertTrue(changed)
        self.assertEqual((winner["id"], removed), (newer["id"], [older["id"]]))
        self.assertIsNone(self.store.authenticate(older_credential))
        self.assertIsNotNone(self.store.authenticate(newer_credential))
        with self.assertRaises(ValueError):
            self.store.bind_client_instance(newer["id"], client_instance_hash(INSTANCE_TWO))


class DeviceInstanceCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CoreFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def _pair(self, instance_id: str, name: str = "My Android phone") -> tuple[dict, str]:
        opened = self.fixture.core.open_pairing()
        pending = self.fixture.core.pair_request(
            {
                "secret": opened["pairUrl"].split("#", 1)[1],
                "device": {
                    "name": name,
                    "platform": "android-web",
                    "clientVersion": "0.2.2",
                    "protocol": 1,
                    "clientInstanceId": instance_id,
                },
            },
            "client",
        )
        self.fixture.core.approve(pending["requestId"], list(DEFAULT_SCOPES))
        delivered = self.fixture.core.pair_status(
            {"requestId": pending["requestId"], "pendingCapability": pending["pendingCapability"]},
            "client",
        )
        return delivered["device"], delivered["credential"]

    def test_pairing_same_browser_is_atomic_replacement_and_closes_old_stream(self) -> None:
        first, first_credential = self._pair(INSTANCE_ONE)
        subscriber = self.fixture.core.subscribe(self.fixture.core.authenticate(first_credential), 4)
        second, second_credential = self._pair(INSTANCE_ONE)

        with self.assertRaises(ApiError) as rejected:
            self.fixture.core.authenticate(first_credential)
        self.assertEqual(rejected.exception.code, "not_authenticated")
        self.assertEqual(self.fixture.core.authenticate(second_credential)["id"], second["id"])
        self.assertEqual(self.fixture.core.local_status()["deviceCount"], 1)
        self.assertEqual(subscriber.get(0.1)["event"], "session.revoked")

    def test_existing_session_can_bind_and_diagnostics_never_expose_identity(self) -> None:
        device, credential = self.fixture.pair()
        result = self.fixture.core.bind_client_instance(
            self.fixture.core.authenticate(credential), {"clientInstanceId": INSTANCE_ONE},
        )
        self.assertEqual((result["status"], result["replacedPairings"]), ("bound", 0))
        sequence = self.fixture.core.sequence
        repeated = self.fixture.core.bind_client_instance(
            self.fixture.core.authenticate(credential), {"clientInstanceId": INSTANCE_ONE},
        )
        self.assertEqual((repeated["replacedPairings"], self.fixture.core.sequence), (0, sequence))
        serialized = json.dumps({
            "status": self.fixture.core.local_status(),
            "diagnostics": self.fixture.store.diagnostics(),
            "devices": self.fixture.store.list_devices(),
        })
        self.assertNotIn(INSTANCE_ONE, serialized)
        self.assertNotIn(client_instance_hash(INSTANCE_ONE), serialized)

    def test_bad_instance_id_fails_closed(self) -> None:
        opened = self.fixture.core.open_pairing()
        with self.assertRaises(ApiError) as rejected:
            self.fixture.core.pair_request(
                {
                    "secret": opened["pairUrl"].split("#", 1)[1],
                    "device": {
                        "name": "Phone", "platform": "android-web", "clientVersion": "0.2.2",
                        "protocol": 1, "clientInstanceId": "same-phone",
                    },
                },
                "client",
            )
        self.assertEqual(rejected.exception.code, "bad_request")


if __name__ == "__main__":
    unittest.main()
