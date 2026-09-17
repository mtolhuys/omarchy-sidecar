"""Small validation, persistence, and redaction helpers."""

from __future__ import annotations

import functools
import importlib.util
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .constants import MAX_FRIENDLY_NAME, MAX_ID, MAX_PLATFORM

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PLATFORM = re.compile(r"^[a-z0-9-]{1,32}$")


class DuplicateKeyError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate field: {key}")
        result[key] = value
    return result


def _reject_surrogates(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("invalid Unicode surrogate")
    elif isinstance(value, list):
        for item in value:
            _reject_surrogates(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_surrogates(key)
            _reject_surrogates(item)


def strict_json_loads(data: bytes | str) -> Any:
    text = data.decode("utf-8", "strict") if isinstance(data, bytes) else data
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting is too deep")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("invalid JSON nesting")

    def finite_float(token: str) -> float:
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("JSON number is out of range")
        return value

    value = json.loads(
        text,
        object_pairs_hook=_pairs_no_duplicates,
        parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"invalid number: {token}")),
        parse_float=finite_float,
    )
    _reject_surrogates(value)
    return value


def normalize_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("device name must be text")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized) > MAX_FRIENDLY_NAME:
        raise ValueError(f"device name must contain 1 through {MAX_FRIENDLY_NAME} characters")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized):
        raise ValueError("device name contains unsupported control characters")
    return normalized


def normalize_platform(value: Any) -> str:
    if not isinstance(value, str) or len(value) > MAX_PLATFORM or not PLATFORM.fullmatch(value):
        raise ValueError("platform is not supported")
    return value


def normalize_id(value: Any, label: str = "id") -> str:
    if not isinstance(value, str) or len(value) > MAX_ID or not OPAQUE_ID.fullmatch(value):
        raise ValueError(f"{label} is not valid")
    return value


def require_exact_object(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    optional = optional or set()
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        raise ValueError(f"missing field: {sorted(missing)[0]}")
    if extra:
        raise ValueError(f"unexpected field: {sorted(extra)[0]}")
    return value


# The Store block (omakit/store-helper.py, omakit's blocks/store, MIT) is
# the transaction the state file goes through: a descriptor walk from HOME
# with O_NOFOLLOW at every step, checks on every descriptor after the open
# and never before, a capped read, an exclusive 0600 staging file renamed
# into place. Imported here, since the daemon lives long and does not shell
# out per write; Store.qml is the same file's front for QML plugins.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
STATE_PLUGIN = "omarchy-sidecar"
STATE_MAX_BYTES = 512 * 1024


@functools.lru_cache(maxsize=1)
def store_block():
    path = PLUGIN_ROOT / "omakit" / "store-helper.py"
    spec = importlib.util.spec_from_file_location("omakit_store_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StateDirectory:
    """The plugin's private state file, through the Store block's transaction.

    The walk starts at HOME when the state base lies under it (the daemon:
    $XDG_STATE_HOME or ~/.local/state, the directories on the way created
    0700 where missing), and at the base's parent otherwise (the tests'
    temporary root), never at the process environment, so a DeviceStore
    given a temporary root stays in it. Every call is one transaction: the
    walk, the check on every descriptor, the read or the staged write.
    """

    def __init__(self, state_root: Path) -> None:
        self.block = store_block()
        home = Path.home()
        walk_from = home if state_root.is_relative_to(home) and state_root != home else state_root.parent
        self.environ = {"HOME": str(walk_from), "XDG_STATE_HOME": str(state_root)}
        self.path = str(state_root / STATE_PLUGIN)

    def _operate(self, op: str, name: str, **extra: str) -> dict[str, Any]:
        opts = {"op": op, "kind": "state", "plugin": STATE_PLUGIN, "name": name, "max_bytes": str(STATE_MAX_BYTES), **extra}
        return self.block.result_of(opts, self.environ)

    def read(self, name: str) -> dict[str, Any]:
        """{state: ok|missing|invalid|refused|overflow|failed, value?, bytes?, reason?}, the way Store.qml reports it."""
        return self._operate("read", name)

    def write(self, name: str, value: Any) -> dict[str, Any]:
        return self._operate("write", name, value=json.dumps(value, ensure_ascii=False, sort_keys=True))

    def move_aside(self, name: str, new_name: str) -> None:
        """Rename by name inside the directory descriptor, following nothing: what a load quarantines."""
        fds, _path = self.block.open_private_directory("state", STATE_PLUGIN, self.environ)
        try:
            os.rename(name, new_name, src_dir_fd=fds[-1], dst_dir_fd=fds[-1])
        finally:
            for fd in fds:
                os.close(fd)


def state_directory(state_root: Path) -> StateDirectory:
    return StateDirectory(state_root)


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"private directory is a symlink: {path}")
    info = path.stat()
    if info.st_uid != os.geteuid():
        raise RuntimeError(f"private directory has the wrong owner: {path}")
    if stat.S_IMODE(info.st_mode) != 0o700:
        path.chmod(0o700)
    return path


def atomic_json_write(path: Path, value: Any) -> None:
    private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_private_json(path: Path) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise RuntimeError("state file ownership is not safe")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("state file permissions are not private")
        if info.st_size > 512 * 1024:
            raise RuntimeError("state file is too large")
        data = bytearray()
        while len(data) <= 512 * 1024:
            chunk = os.read(descriptor, min(64 * 1024, 512 * 1024 + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != info.st_size or len(data) > 512 * 1024:
            raise RuntimeError("state file changed while it was being read")
        return strict_json_loads(bytes(data))
    finally:
        os.close(descriptor)


def sanitized_color(value: Any, fallback: str) -> str:
    return value.lower() if isinstance(value, str) and HEX_COLOR.fullmatch(value) else fallback


def json_safe_log(event: str, **fields: Any) -> str:
    allowed: dict[str, Any] = {"time": utc_now(), "event": event}
    for key, value in fields.items():
        if key in {"build", "protocol", "code", "adapter", "state", "count", "latencyMs", "device"}:
            allowed[key] = value
    return json.dumps(allowed, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
