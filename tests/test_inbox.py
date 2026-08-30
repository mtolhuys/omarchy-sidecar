from __future__ import annotations

import hashlib
import io
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sidecar.inbox import InboxError, InboxManager, resolve_downloads_directory, sanitize_filename


class InboxPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.manager = InboxManager(home=self.home, environ={}, announce=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def declared(name: str, media_type: str, body: bytes) -> dict:
        return {"name": name, "mediaType": media_type, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    def intent(self, request_id: str, file: dict) -> dict:
        return self.manager.create_intent("dev_test", {"requestId": request_id, "files": [file]})

    def upload(self, intent: dict, body: bytes, media_type: str) -> dict:
        result = self.manager.upload(
            "dev_test", intent["files"][0]["uploadId"], len(body), media_type, io.BytesIO(body),
            lambda: None, lambda commit: commit(),
        )
        return result.receipt

    def test_downloads_resolution_is_fixed_bounded_and_shell_free(self) -> None:
        config = self.home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Incoming"\n', encoding="utf-8")
        self.assertEqual(resolve_downloads_directory(self.home, {}), self.home / "Incoming")
        self.assertEqual(resolve_downloads_directory(self.home, {"XDG_DOWNLOAD_DIR": "$(touch /tmp/nope)"}), self.home / "Downloads")
        outside = self.home.parent / "outside"
        self.assertEqual(resolve_downloads_directory(self.home, {"XDG_DOWNLOAD_DIR": str(outside)}), self.home / "Downloads")

    def test_unicode_filename_policy_removes_injection_and_preserves_readability(self) -> None:
        name, stem, extension = sanitize_filename(" ../-report\u202e/line\n.png ")
        self.assertEqual(extension, ".png")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn("\u202e", name)
        self.assertNotIn("\n", name)
        self.assertTrue(stem)
        with self.assertRaises(InboxError):
            sanitize_filename("\u202e\x00")

    def test_magic_utf8_digest_mode_no_overwrite_and_exact_replay(self) -> None:
        fixtures = [
            ("capture.png", "image/png", base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")),
            ("capture.jpg", "image/jpeg", b"\xff\xd8\xff\xc0\x00\x02\xff\xda\x00\x02\xff\xd9"),
            ("capture.webp", "image/webp", b"RIFF\x0c\x00\x00\x00WEBPVP8 \x00\x00\x00\x00"),
            ("capture.gif", "image/gif", b"GIF89a\x01\x00\x01\x00\x00;"),
            ("note.pdf", "application/pdf", b"%PDF-1.7\nfixture\n%%EOF\n"),
            ("note.txt", "text/plain", "hello\nworld".encode()),
        ]
        names = []
        for index, (name, media_type, body) in enumerate(fixtures):
            intent = self.intent(f"req_{index}", self.declared(name, media_type, body))
            exact = self.manager.create_intent("dev_test", {"requestId": f"req_{index}", "files": [self.declared(name, media_type, body)]})
            self.assertEqual(exact, intent)
            with self.assertRaises(InboxError):
                self.manager.create_intent("dev_test", {"requestId": f"req_{index}", "files": [self.declared("other" + Path(name).suffix, media_type, body)]})
            receipt = self.upload(intent, body, media_type)
            target = self.manager.inbox / receipt["name"]
            self.assertEqual(target.read_bytes(), body)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.stat().st_nlink, 1)
            names.append(target.name)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(list(self.manager.staging.iterdir()), [])

    def test_active_content_spoof_low_space_cancel_and_restart_cleanup_fail_closed(self) -> None:
        png_named_pdf = self.declared("false.png", "image/png", b"%PDF-1.7\n%%EOF")
        intent = self.intent("req_spoof", png_named_pdf)
        with self.assertRaises(InboxError) as rejected:
            self.upload(intent, b"%PDF-1.7\n%%EOF", "image/png")
        self.assertEqual(rejected.exception.code, "upload_rejected")
        bad_text = b"hello\x00world"
        intent = self.intent("req_binary_text", self.declared("note.txt", "text/plain", bad_text))
        with self.assertRaises(InboxError):
            self.upload(intent, bad_text, "text/plain")

        for index, active_pdf in enumerate((
            b"%PDF-1.7\n1 0 obj <</Open#41ction 2 0 R>> endobj\n%%EOF\n",
            b"%PDF-1.7\n1 0 obj <</Type /ObjStm>> stream\nopaque\nendstream\n%%EOF\n",
        )):
            intent = self.intent(f"req_active_pdf_{index}", self.declared("active.pdf", "application/pdf", active_pdf))
            with self.assertRaises(InboxError) as active:
                self.upload(intent, active_pdf, "application/pdf")
            self.assertEqual(active.exception.code, "upload_rejected")

        usage = os.statvfs(self.manager.inbox)
        fake_usage = mock.Mock(free=0, total=usage.f_blocks * usage.f_frsize, used=0)
        with mock.patch("sidecar.inbox.shutil.disk_usage", return_value=fake_usage):
            with self.assertRaises(InboxError) as full:
                self.intent("req_full", self.declared("small.txt", "text/plain", b"x"))
        self.assertEqual(full.exception.code, "inbox_full")

        pending = self.intent("req_cancel", self.declared("cancel.txt", "text/plain", b"cancel"))
        self.assertEqual(self.manager.cancel("dev_test", {"intentId": pending["intentId"]}), {"status": "cancelled"})
        with self.assertRaises(InboxError):
            self.manager.completed_replay("dev_test", pending["files"][0]["uploadId"], 6, "text/plain")

        abandoned = self.manager.staging / "part_abandoned"
        abandoned.write_bytes(b"partial")
        restarted = InboxManager(home=self.home, environ={}, announce=False)
        self.assertFalse(abandoned.exists())
        self.assertTrue(restarted.inbox.is_dir())

        moved_staging = self.manager.inbox / ".sidecar-staging-moved"
        self.manager.staging.rename(moved_staging)
        self.manager.staging.symlink_to(moved_staging, target_is_directory=True)
        intent = self.intent("req_staging_swap", self.declared("swap.txt", "text/plain", b"safe"))
        with self.assertRaises(OSError):
            self.upload(intent, b"safe", "text/plain")
        self.manager.staging.unlink()
        moved_staging.rename(self.manager.staging)

    def test_global_upload_concurrency_is_bounded(self) -> None:
        body = b"bounded"
        intent = self.intent("req_busy", self.declared("busy.txt", "text/plain", body))
        self.assertTrue(self.manager._uploads.acquire())
        self.assertTrue(self.manager._uploads.acquire())
        try:
            with self.assertRaises(InboxError) as busy:
                self.upload(intent, body, "text/plain")
        finally:
            self.manager._uploads.release()
            self.manager._uploads.release()
        self.assertEqual(busy.exception.code, "upload_busy")


if __name__ == "__main__":
    unittest.main()
