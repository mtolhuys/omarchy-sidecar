"""Bounded phone-to-desktop inbox with streamed, no-overwrite commits."""

from __future__ import annotations

import codecs
import collections
import binascii
import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .constants import (
    INBOX_CHUNK_BYTES,
    INBOX_DEVICE_BYTES_PER_HOUR,
    INBOX_DEVICE_FILES_PER_HOUR,
    INBOX_FREE_SPACE_RESERVE_BYTES,
    INBOX_INTENT_LIFETIME_SECONDS,
    MAX_INBOX_BATCH_BYTES,
    MAX_INBOX_FILE_BYTES,
    MAX_INBOX_FILENAME_CHARS,
    MAX_INBOX_FILES,
    MAX_INBOX_PENDING_GLOBAL,
    MAX_INBOX_PENDING_PER_DEVICE,
    MAX_INBOX_UPLOADS_GLOBAL,
)
from .util import canonical_json, normalize_id, require_exact_object, utc_now


MEDIA_POLICY: dict[str, tuple[tuple[str, ...], str]] = {
    "image/png": ((".png",), "PNG"),
    "image/jpeg": ((".jpg", ".jpeg"), "JPEG"),
    "image/webp": ((".webp",), "WebP"),
    "image/gif": ((".gif",), "GIF"),
    "application/pdf": ((".pdf",), "PDF"),
    "text/plain": ((".txt",), "Text"),
}

_BIDI_CONTROLS = frozenset(chr(value) for value in (
    0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
))


class InboxError(RuntimeError):
    def __init__(self, code: str, status: int = 400, message: str = ""):
        self.code = code
        self.status = status
        self.message = message
        super().__init__(message or code)


@dataclass
class UploadResult:
    receipt: dict[str, Any]
    replay: bool = False


def resolve_downloads_directory(home: Path | None = None, environ: dict[str, str] | None = None) -> Path:
    """Resolve the inspected Omarchy XDG Downloads contract without a shell."""
    home = (home or Path.home()).resolve()
    environ = environ or os.environ
    configured = environ.get("XDG_DOWNLOAD_DIR", "")
    if not configured:
        user_dirs = home / ".config" / "user-dirs.dirs"
        try:
            for line in user_dirs.read_text(encoding="utf-8").splitlines():
                match = re.fullmatch(r'XDG_DOWNLOAD_DIR="([^"\n]*)"', line.strip())
                if match:
                    configured = match.group(1)
                    break
        except (FileNotFoundError, OSError, UnicodeError):
            pass
    if configured.startswith("$HOME/"):
        candidate = home / configured[6:]
    elif configured == "$HOME":
        candidate = home
    elif configured.startswith("/"):
        candidate = Path(configured)
    else:
        candidate = home / "Downloads"
    candidate = candidate.expanduser()
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(home)
    except (OSError, ValueError):
        resolved = home / "Downloads"
    return resolved


def sanitize_filename(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, str) or not value or len(value) > MAX_INBOX_FILENAME_CHARS * 4:
        raise InboxError("upload_rejected", 415, "Choose a file with a shorter readable name.")
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = []
    for character in normalized:
        category = unicodedata.category(character)
        if character in _BIDI_CONTROLS or category in {"Cc", "Cf", "Cs"}:
            continue
        cleaned.append("_" if character in "/\\" else character)
    name = "".join(cleaned).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if name.startswith("-"):
        name = "file_" + name[1:]
    if not name or name in {".", ".."}:
        raise InboxError("upload_rejected", 415, "Choose a file with a readable name.")
    if len(name) > MAX_INBOX_FILENAME_CHARS:
        suffix = Path(name).suffix[:12]
        name = name[: MAX_INBOX_FILENAME_CHARS - len(suffix)].rstrip(" .") + suffix
    extension = Path(name).suffix.lower()
    stem = name[: -len(extension)] if extension else name
    if not stem:
        raise InboxError("upload_rejected", 415, "Choose a file with a readable name.")
    return name, stem, extension


def _validate_declared_file(value: Any) -> dict[str, Any]:
    item = require_exact_object(value, {"name", "mediaType", "size", "sha256"})
    name, stem, extension = sanitize_filename(item["name"])
    media_type = item["mediaType"]
    if not isinstance(media_type, str) or media_type not in MEDIA_POLICY:
        raise InboxError("upload_rejected", 415, "Sidecar accepts PNG, JPEG, WebP, GIF, PDF, and UTF-8 text files.")
    if extension not in MEDIA_POLICY[media_type][0]:
        raise InboxError("upload_rejected", 415, "The filename extension and declared file type must agree.")
    size = item["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 1 or size > MAX_INBOX_FILE_BYTES:
        raise InboxError("upload_rejected", 413, "Each file must be between 1 byte and 25 MiB.")
    digest = item["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise InboxError("upload_rejected", 400, "The file digest was not valid.")
    return {"name": name, "stem": stem, "extension": extension, "mediaType": media_type, "size": size, "sha256": digest}


def _content_matches(path: Path, media_type: str) -> bool:
    size = path.stat().st_size
    with path.open("rb", buffering=0) as handle:
        head = handle.read(32)
        handle.seek(max(0, size - 64))
        tail = handle.read(64)
    if media_type == "image/png":
        try:
            with path.open("rb", buffering=0) as handle:
                if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                    return False
                first = True
                while True:
                    length_raw = handle.read(4)
                    if len(length_raw) != 4:
                        return False
                    length = int.from_bytes(length_raw, "big")
                    if length > MAX_INBOX_FILE_BYTES:
                        return False
                    chunk_type = handle.read(4)
                    data = handle.read(length)
                    crc = handle.read(4)
                    if len(chunk_type) != 4 or len(data) != length or len(crc) != 4:
                        return False
                    if int.from_bytes(crc, "big") != (binascii.crc32(chunk_type + data) & 0xFFFFFFFF):
                        return False
                    if first and (chunk_type != b"IHDR" or length != 13 or int.from_bytes(data[:4], "big") < 1 or int.from_bytes(data[4:8], "big") < 1):
                        return False
                    first = False
                    if chunk_type == b"IEND":
                        return length == 0 and handle.read(1) == b""
        except OSError:
            return False
    if media_type == "image/jpeg":
        markers: set[bytes] = set()
        overlap = b""
        with path.open("rb", buffering=0) as handle:
            while chunk := handle.read(INBOX_CHUNK_BYTES):
                block = overlap + chunk
                for marker in (b"\xff\xc0", b"\xff\xc1", b"\xff\xc2", b"\xff\xda"):
                    if marker in block:
                        markers.add(marker)
                overlap = block[-1:]
        return (head.startswith(b"\xff\xd8\xff") and tail.endswith(b"\xff\xd9")
                and b"\xff\xda" in markers and bool(markers & {b"\xff\xc0", b"\xff\xc1", b"\xff\xc2"}))
    if media_type == "image/webp":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP" and int.from_bytes(head[4:8], "little") + 8 == size
    if media_type == "image/gif":
        return (head.startswith((b"GIF87a", b"GIF89a")) and len(head) >= 10
                and int.from_bytes(head[6:8], "little") > 0 and int.from_bytes(head[8:10], "little") > 0
                and tail.endswith(b";"))
    if media_type == "application/pdf":
        if not re.match(br"%PDF-1\.[0-7](?:\r?\n|\r)", head) or not re.search(br"%%EOF[\x00\x09\x0a\x0c\x0d\x20]*$", tail):
            return False
        active_names = {
            b"aa", b"acroform", b"action", b"embeddedfile", b"encrypt", b"gotoe", b"gotor",
            b"importdata", b"javascript", b"js", b"launch", b"movie", b"objstm", b"openaction",
            b"rendition", b"richmedia", b"sound", b"submitform", b"3d", b"uri", b"xfa",
        }
        overlap = b""
        with path.open("rb", buffering=0) as handle:
            while chunk := handle.read(INBOX_CHUNK_BYTES):
                lowered = (overlap + chunk).lower()
                for encoded_name in re.findall(br"/([^\x00\x09\x0a\x0c\x0d\x20()<>\[\]{}/%]+)", lowered):
                    decoded_name = re.sub(
                        br"#([0-9a-f]{2})",
                        lambda match: bytes((int(match.group(1), 16),)),
                        encoded_name,
                    ).lower()
                    if decoded_name in active_names:
                        return False
                overlap = lowered[-128:]
        return True
    return True


class InboxManager:
    def __init__(self, home: Path | None = None, environ: dict[str, str] | None = None, *, announce: bool = True):
        self.downloads = resolve_downloads_directory(home, environ)
        self.inbox = self.downloads / "Sidecar"
        self.staging = self.inbox / ".sidecar-staging"
        self._lock = threading.RLock()
        self._uploads = threading.BoundedSemaphore(MAX_INBOX_UPLOADS_GLOBAL)
        self._intents: dict[str, dict[str, Any]] = {}
        self._upload_index: dict[str, str] = {}
        self._request_replay: dict[tuple[str, str], tuple[float, bytes, dict[str, Any]]] = {}
        self._rate: dict[str, collections.deque[tuple[float, int]]] = {}
        self._reserved_bytes = 0
        self._attention_seq = 0
        self._received_count = 0
        self._last_kind = ""
        self._announce = announce
        self._prepare_directories()
        self.cleanup_staging()

    def _prepare_directories(self) -> None:
        self.downloads.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.downloads.is_symlink() or not self.downloads.is_dir():
            raise RuntimeError("Downloads directory is not a trusted directory")
        self.inbox.mkdir(mode=0o700, exist_ok=True)
        self.staging.mkdir(mode=0o700, exist_ok=True)
        for directory in (self.inbox, self.staging):
            descriptor = self._open_private_directory(directory)
            try:
                os.fchmod(descriptor, 0o700)
            finally:
                os.close(descriptor)

    @staticmethod
    def _open_private_directory(directory: Path) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(directory, flags)
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
            os.close(descriptor)
            raise RuntimeError("Sidecar Inbox directory is not private and user-owned")
        return descriptor

    def cleanup_staging(self) -> None:
        try:
            directory_fd = self._open_private_directory(self.staging)
        except FileNotFoundError:
            return
        try:
            for name in os.listdir(directory_fd):
                try:
                    mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
                    if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                        os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory_fd)

    def _expire_locked(self) -> None:
        now = time.monotonic()
        expired = [intent_id for intent_id, intent in self._intents.items() if now >= intent["deadline"]]
        for intent_id in expired:
            self._invalidate_locked(intent_id)
        stale = [key for key, value in self._request_replay.items() if now - value[0] > INBOX_INTENT_LIFETIME_SECONDS]
        for key in stale:
            del self._request_replay[key]

    def _invalidate_locked(self, intent_id: str) -> None:
        intent = self._intents.pop(intent_id, None)
        if not intent:
            return
        for file in intent["files"]:
            self._upload_index.pop(file["uploadId"], None)
            if file["status"] not in {"completed", "failed"}:
                self._reserved_bytes = max(0, self._reserved_bytes - file["size"])
            if file.get("staging"):
                try:
                    staging_fd = self._open_private_directory(self.staging)
                    try:
                        os.unlink(file["staging"], dir_fd=staging_fd)
                    finally:
                        os.close(staging_fd)
                except (FileNotFoundError, OSError):
                    pass

    def invalidate_device(self, device_id: str) -> None:
        with self._lock:
            for intent_id in [key for key, value in self._intents.items() if value["deviceId"] == device_id]:
                self._invalidate_locked(intent_id)

    def invalidate_all(self) -> None:
        with self._lock:
            for intent_id in list(self._intents):
                self._invalidate_locked(intent_id)

    def _check_rate_locked(self, device_id: str, sizes: list[int]) -> None:
        now = time.monotonic()
        window = self._rate.setdefault(device_id, collections.deque())
        while window and now - window[0][0] >= 3600:
            window.popleft()
        if len(window) + len(sizes) > INBOX_DEVICE_FILES_PER_HOUR or sum(item[1] for item in window) + sum(sizes) > INBOX_DEVICE_BYTES_PER_HOUR:
            raise InboxError("rate_limited", 429, "This phone reached Sidecar's hourly Drop limit. Try again later.")
        window.extend((now, size) for size in sizes)

    def create_intent(self, device_id: str, body: Any) -> dict[str, Any]:
        request = require_exact_object(body, {"requestId", "files"})
        request_id = normalize_id(request["requestId"], "request id")
        files_raw = request["files"]
        if not isinstance(files_raw, list) or not (1 <= len(files_raw) <= MAX_INBOX_FILES):
            raise InboxError("upload_rejected", 413, "Choose between 1 and 5 files.")
        files = [_validate_declared_file(value) for value in files_raw]
        total = sum(file["size"] for file in files)
        if total > MAX_INBOX_BATCH_BYTES:
            raise InboxError("upload_rejected", 413, "A Drop can contain at most 50 MiB.")
        fingerprint = canonical_json(request)
        with self._lock:
            self._expire_locked()
            replay_key = (device_id, request_id)
            prior = self._request_replay.get(replay_key)
            if prior:
                if prior[1] != fingerprint:
                    raise InboxError("upload_rejected", 409, "That request ID was already used for a different Drop.")
                if prior[2]["intentId"] in self._intents:
                    return prior[2]
                del self._request_replay[replay_key]
            active_intents = [value for value in self._intents.values() if any(file["status"] in {"pending", "uploading"} for file in value["files"])]
            device_pending = sum(1 for value in active_intents if value["deviceId"] == device_id)
            if device_pending >= MAX_INBOX_PENDING_PER_DEVICE or len(active_intents) >= MAX_INBOX_PENDING_GLOBAL:
                raise InboxError("upload_busy", 429, "Too many Drops are waiting. Cancel one or wait for it to expire.")
            self._check_rate_locked(device_id, [file["size"] for file in files])
            free = shutil.disk_usage(self.inbox).free
            if free - self._reserved_bytes - total < INBOX_FREE_SPACE_RESERVE_BYTES:
                raise InboxError("inbox_full", 507)
            intent_id = "intent_" + secrets.token_urlsafe(18)
            expiry = time.time() + INBOX_INTENT_LIFETIME_SECONDS
            public_files = []
            for file in files:
                upload_id = "upload_" + secrets.token_urlsafe(24)
                file.update({"uploadId": upload_id, "status": "pending", "staging": "", "receipt": None})
                self._upload_index[upload_id] = intent_id
                public_files.append({key: file[key] for key in ("name", "mediaType", "size", "sha256", "uploadId")})
            self._reserved_bytes += total
            self._intents[intent_id] = {
                "id": intent_id, "deviceId": device_id, "requestId": request_id,
                "deadline": time.monotonic() + INBOX_INTENT_LIFETIME_SECONDS,
                "expiresAt": expiry, "files": files,
            }
            result = {"intentId": intent_id, "expiresAt": expiry, "destination": "Sidecar Inbox", "files": public_files}
            self._request_replay[replay_key] = (time.monotonic(), fingerprint, result)
            return result

    def cancel(self, device_id: str, body: Any) -> dict[str, Any]:
        request = require_exact_object(body, {"intentId"})
        intent_id = normalize_id(request["intentId"], "intent id")
        with self._lock:
            intent = self._intents.get(intent_id)
            if not intent or intent["deviceId"] != device_id:
                raise InboxError("upload_expired", 410)
            self._invalidate_locked(intent_id)
        return {"status": "cancelled"}

    def _lookup_upload_locked(self, device_id: str, upload_id: str, length: int, media_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
        self._expire_locked()
        intent_id = self._upload_index.get(upload_id)
        intent = self._intents.get(intent_id or "")
        if not intent or intent["deviceId"] != device_id:
            raise InboxError("upload_expired", 410)
        file = next((value for value in intent["files"] if value["uploadId"] == upload_id), None)
        if not file:
            raise InboxError("upload_expired", 410)
        if file["size"] != length or file["mediaType"] != media_type:
            raise InboxError("upload_rejected", 409, "The upload no longer matches its approved intent.")
        return intent, file

    def completed_replay(self, device_id: str, upload_id: str, length: int, media_type: str) -> dict[str, Any] | None:
        with self._lock:
            _intent, file = self._lookup_upload_locked(device_id, upload_id, length, media_type)
            return file["receipt"] if file["status"] == "completed" else None

    def upload(
        self,
        device_id: str,
        upload_id: str,
        length: int,
        media_type: str,
        body: BinaryIO,
        policy_check: Callable[[], None],
        commit_guard: Callable[[Callable[[], dict[str, Any]]], dict[str, Any]],
    ) -> UploadResult:
        normalize_id(upload_id, "upload id")
        if not self._uploads.acquire(blocking=False):
            raise InboxError("upload_busy", 429)
        staging_name = ""
        staging_fd: int | None = None
        file: dict[str, Any] | None = None
        try:
            with self._lock:
                _intent, file = self._lookup_upload_locked(device_id, upload_id, length, media_type)
                if file["status"] == "completed":
                    return UploadResult(file["receipt"], replay=True)
                if file["status"] != "pending":
                    raise InboxError("upload_busy", 409, "This file is already being received.")
                file["status"] = "uploading"
                staging_name = "part_" + secrets.token_urlsafe(24)
                file["staging"] = staging_name
            staging_fd = self._open_private_directory(self.staging)
            descriptor = os.open(
                staging_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=staging_fd,
            )
            digest = hashlib.sha256()
            remaining = length
            decoder = codecs.getincrementaldecoder("utf-8")("strict") if media_type == "text/plain" else None
            with os.fdopen(descriptor, "wb", buffering=0) as output:
                while remaining:
                    policy_check()
                    try:
                        chunk = body.read(min(INBOX_CHUNK_BYTES, remaining))
                    except TimeoutError as error:
                        raise InboxError("upload_interrupted", 408) from error
                    if not chunk:
                        raise InboxError("upload_interrupted", 400)
                    if decoder is not None:
                        if b"\x00" in chunk:
                            raise InboxError("upload_rejected", 415, "Plain text cannot contain binary NUL bytes.")
                        try:
                            decoder.decode(chunk, final=False)
                        except UnicodeDecodeError as error:
                            raise InboxError("upload_rejected", 415, "Plain text must be valid UTF-8.") from error
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                if decoder is not None:
                    try:
                        decoder.decode(b"", final=True)
                    except UnicodeDecodeError as error:
                        raise InboxError("upload_rejected", 415, "Plain text must be valid UTF-8.") from error
                output.flush()
                os.fsync(output.fileno())
            policy_check()
            try:
                read_fd = os.open(
                    staging_name,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=staging_fd,
                )
            except FileNotFoundError as error:
                raise InboxError("upload_expired", 410) from error
            try:
                policy_check()
                if digest.hexdigest() != file["sha256"] or not _content_matches(Path(f"/proc/self/fd/{read_fd}"), media_type):
                    raise InboxError("upload_rejected", 415, "The received bytes did not match the declared file.")
                policy_check()
                receipt = commit_guard(
                    lambda: self._commit(device_id, upload_id, staging_name, staging_fd, read_fd, policy_check)
                )
            finally:
                os.close(read_fd)
            staging_name = ""
            return UploadResult(receipt)
        except Exception:
            if file is not None:
                with self._lock:
                    if file["status"] != "completed":
                        file["status"] = "failed"
                        self._reserved_bytes = max(0, self._reserved_bytes - file["size"])
            raise
        finally:
            if staging_name and staging_fd is not None:
                try:
                    os.unlink(staging_name, dir_fd=staging_fd)
                except FileNotFoundError:
                    pass
            if staging_fd is not None:
                os.close(staging_fd)
            self._uploads.release()

    def _commit(
        self,
        device_id: str,
        upload_id: str,
        staged_name: str,
        staging_fd: int,
        staged_read_fd: int,
        policy_check: Callable[[], None],
    ) -> dict[str, Any]:
        policy_check()
        with self._lock:
            intent_id = self._upload_index.get(upload_id)
            intent = self._intents.get(intent_id or "")
            if not intent or intent["deviceId"] != device_id:
                raise InboxError("upload_expired", 410)
            file = next(value for value in intent["files"] if value["uploadId"] == upload_id)
            if file["status"] != "uploading":
                raise InboxError("upload_expired", 410)
            source_stat = os.fstat(staged_read_fd)
            try:
                named_source_stat = os.stat(staged_name, dir_fd=staging_fd, follow_symlinks=False)
            except FileNotFoundError as error:
                raise InboxError("upload_expired", 410) from error
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1 or source_stat.st_uid != os.geteuid():
                raise InboxError("upload_rejected", 409, "Sidecar staging was not a private regular file.")
            if (named_source_stat.st_dev, named_source_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino):
                raise InboxError("upload_rejected", 409, "Sidecar staging changed before commit.")
            inbox_fd = self._open_private_directory(self.inbox)
            try:
                target_name = ""
                for index in range(1000):
                    suffix = "" if index == 0 else "-" + secrets.token_hex(4)
                    candidate_name = f"{file['stem']}{suffix}{file['extension']}"
                    try:
                        os.link(
                            staged_name,
                            candidate_name,
                            src_dir_fd=staging_fd,
                            dst_dir_fd=inbox_fd,
                            follow_symlinks=False,
                        )
                        target_name = candidate_name
                        break
                    except FileExistsError:
                        continue
                if not target_name:
                    raise InboxError("upload_rejected", 409, "Sidecar could not choose a collision-free filename.")
                try:
                    os.chmod(target_name, 0o600, dir_fd=inbox_fd, follow_symlinks=False)
                    target_fd = os.open(
                        target_name,
                        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=inbox_fd,
                    )
                    try:
                        final_stat = os.fstat(target_fd)
                        if (
                            not stat.S_ISREG(final_stat.st_mode)
                            or stat.S_IMODE(final_stat.st_mode) != 0o600
                            or final_stat.st_nlink != 2
                            or (final_stat.st_dev, final_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino)
                        ):
                            raise InboxError("upload_rejected", 409, "Sidecar could not verify the final file.")
                        os.fsync(target_fd)
                    finally:
                        os.close(target_fd)
                    os.unlink(staged_name, dir_fd=staging_fd)
                    final_stat = os.stat(target_name, dir_fd=inbox_fd, follow_symlinks=False)
                    if not stat.S_ISREG(final_stat.st_mode) or final_stat.st_nlink != 1:
                        raise InboxError("upload_rejected", 409, "Sidecar could not verify the final file.")
                    os.fsync(inbox_fd)
                except Exception:
                    try:
                        os.unlink(target_name, dir_fd=inbox_fd)
                    except FileNotFoundError:
                        pass
                    raise
            finally:
                os.close(inbox_fd)
            receipt = {
                "uploadId": upload_id,
                "status": "saved",
                "name": target_name,
                "mediaType": file["mediaType"],
                "size": file["size"],
                "sha256": file["sha256"],
                "destination": "Sidecar Inbox",
                "savedAt": utc_now(),
            }
            file["receipt"] = receipt
            file["status"] = "completed"
            file["staging"] = ""
            self._reserved_bytes = max(0, self._reserved_bytes - file["size"])
            self._attention_seq += 1
            self._received_count += 1
            self._last_kind = MEDIA_POLICY[file["mediaType"]][1]
            if all(value["status"] in {"completed", "failed"} for value in intent["files"]):
                # Keep completed receipts replayable until their five-minute deadline.
                pass
        self._notify_received()
        return receipt

    def _notify_received(self) -> None:
        if not self._announce:
            return
        try:
            subprocess.Popen(
                ["omarchy-notification-send", "Sidecar Drop", "Saved to Sidecar Inbox", "-g", "󰉋", "--exec", "xdg-open", str(self.inbox)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError:
            pass

    def open_inbox(self) -> dict[str, Any]:
        self._prepare_directories()
        subprocess.Popen(["xdg-open", str(self.inbox)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"status": "opened"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            staging_fd = self._open_private_directory(self.staging)
            try:
                staging_count = len(os.listdir(staging_fd))
            finally:
                os.close(staging_fd)
            return {
                "destination": "Sidecar Inbox",
                "attentionSeq": self._attention_seq,
                "receivedCount": self._received_count,
                "lastKind": self._last_kind,
                "pendingIntents": sum(1 for intent in self._intents.values() if any(file["status"] in {"pending", "uploading"} for file in intent["files"])),
                "activeUploads": sum(1 for intent in self._intents.values() for file in intent["files"] if file["status"] == "uploading"),
                "stagingCount": staging_count,
            }
