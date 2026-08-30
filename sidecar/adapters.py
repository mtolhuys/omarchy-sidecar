"""Deterministic fake and current public Omarchy desktop adapters."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import threading
import time
import unicodedata
import zlib
from pathlib import Path
from typing import Any

from .constants import (
    ADAPTER_TIMEOUT_SECONDS,
    MAX_THEMES,
    MAX_THEME_NAME,
    MAX_THEME_PREVIEW_BYTES,
    MAX_WINDOWS,
    MAX_WORKSPACES,
)
from .util import sanitized_color


class AdapterError(RuntimeError):
    pass


def _safe_label(value: Any, fallback: str = "Application") -> str:
    if not isinstance(value, str):
        return fallback
    text = unicodedata.normalize("NFC", value).strip()
    bidi_controls = {"\u061c", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"}
    text = "".join(
        character for character in text
        if unicodedata.category(character) not in {"Cc", "Cs"} and character not in bidi_controls
    )
    return text[:48] or fallback


def _safe_theme_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", value).strip()
    if not text or len(text) > MAX_THEME_NAME:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+()-]*", text):
        return None
    return text


THEME_PREVIEW_ACCENTS = ("#8b5cf6", "#3b82f6", "#14b8a6", "#f97316", "#ec4899", "#84cc16")
THEME_PREVIEW_FILES = ("preview.png", "preview.jpg", "preview.jpeg", "preview.webp", "preview.gif", "preview.bmp")
THEME_PREVIEW_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
MAX_THEME_PREVIEW_PIXELS = 16 * 1024 * 1024


def _image_dimensions(body: bytes, mime: str) -> tuple[int, int] | None:
    try:
        if mime == "image/png" and body.startswith(b"\x89PNG\r\n\x1a\n") and len(body) >= 24:
            return struct.unpack(">II", body[16:24])
        if mime == "image/gif" and body[:6] in {b"GIF87a", b"GIF89a"} and len(body) >= 10:
            return struct.unpack("<HH", body[6:10])
        if mime == "image/bmp" and body.startswith(b"BM") and len(body) >= 26:
            width, height = struct.unpack("<ii", body[18:26])
            return width, abs(height)
        if mime == "image/webp" and body.startswith(b"RIFF") and body[8:12] == b"WEBP" and len(body) >= 30:
            kind = body[12:16]
            if kind == b"VP8X":
                return 1 + int.from_bytes(body[24:27], "little"), 1 + int.from_bytes(body[27:30], "little")
            if kind == b"VP8 " and body[23:26] == b"\x9d\x01\x2a":
                return int.from_bytes(body[26:28], "little") & 0x3fff, int.from_bytes(body[28:30], "little") & 0x3fff
            if kind == b"VP8L" and body[20] == 0x2f:
                bits = int.from_bytes(body[21:25], "little")
                return 1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff)
        if mime == "image/jpeg" and body.startswith(b"\xff\xd8"):
            index = 2
            while index + 4 <= len(body):
                if body[index] != 0xff:
                    index += 1
                    continue
                marker = body[index + 1]
                index += 2
                if marker in {0x01, *range(0xd0, 0xd9)}:
                    continue
                length = int.from_bytes(body[index:index + 2], "big")
                if length < 2 or index + length > len(body):
                    return None
                if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf} and length >= 7:
                    return int.from_bytes(body[index + 3:index + 5], "big"), int.from_bytes(body[index + 5:index + 7], "big")
                index += length
    except (IndexError, struct.error):
        return None
    return None


def _safe_preview_image(body: bytes, mime: str) -> bool:
    dimensions = _image_dimensions(body, mime)
    if dimensions is None:
        return False
    width, height = dimensions
    return 0 < width <= 8192 and 0 < height <= 8192 and width * height <= MAX_THEME_PREVIEW_PIXELS


def _fake_preview_png(accent: str) -> bytes:
    """Produce a deterministic wallpaper-like PNG without a runtime image dependency."""
    red, green, blue = (int(accent[index:index + 2], 16) for index in (1, 3, 5))
    width, height = 320, 180
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            glow = max(0.0, 1.0 - (((x - 240) / 190) ** 2 + ((y - 42) / 145) ** 2))
            ridge = max(0.0, 1.0 - abs(y - (116 + 15 * ((x % 83) / 82))) / 38)
            rows.extend((
                min(255, int(13 + red * (.18 + glow * .55) + ridge * 18)),
                min(255, int(15 + green * (.16 + glow * .48) + ridge * 12)),
                min(255, int(20 + blue * (.20 + glow * .54) + ridge * 22)),
            ))
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


class FakeAdapter:
    """A deterministic in-memory desktop for protocol and browser acceptance."""

    def __init__(self):
        self._lock = threading.RLock()
        self.locked = False
        self.lock_known = True
        self.active_workspace = "ws_1"
        self.current_theme = "theme_aurora"
        self.background_index = 0
        self._workspaces = [
            {"id": "ws_1", "label": "1", "active": True, "urgent": False},
            {"id": "ws_2", "label": "2", "active": False, "urgent": False},
            {"id": "ws_3", "label": "3", "active": False, "urgent": True},
        ]
        self._windows = [
            {"id": "win_editor", "app": "Editor", "workspaceId": "ws_1", "focused": True},
            {"id": "win_browser", "app": "Chromium", "workspaceId": "ws_2", "focused": False},
        ]
        self._media = {"available": True, "playing": False, "volume": 0.45, "canPrevious": True, "canNext": True}
        self._themes = [
            {"id": "theme_aurora", "name": "Aurora", "previewAccent": "#8b5cf6", "previewAvailable": True},
            {"id": "theme_ember", "name": "Ember", "previewAccent": "#f97316", "previewAvailable": True},
            {"id": "theme_lagoon", "name": "Lagoon", "previewAccent": "#14b8a6", "previewAvailable": True},
            {"id": "theme_lupine", "name": "Lupine", "previewAccent": "#2563eb", "previewAvailable": True},
        ]

    def _theme(self) -> dict[str, Any]:
        accent = next(item["previewAccent"] for item in self._themes if item["id"] == self.current_theme)
        if self.current_theme == "theme_lupine":
            return {
                "scheme": "light", "background": "#f3f3f1", "surface": "#e5e5e2",
                "surfaceElevated": "#d4d4d0", "text": "#191919", "textMuted": "#666663",
                "accent": accent, "success": "#16803c", "warning": "#9a6700",
                "danger": "#c52b36", "radius": 18,
            }
        return {
            "scheme": "dark", "background": "#111116", "surface": "#23232b",
            "surfaceElevated": "#373743", "text": "#fafafa", "textMuted": "#aaaab7",
            "accent": accent, "success": "#4ade80", "warning": "#facc15",
            "danger": "#fb7185", "radius": 22,
        }

    def lock_state(self) -> tuple[bool, bool]:
        with self._lock:
            return self.lock_known, self.locked

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            workspaces = [dict(item, active=item["id"] == self.active_workspace) for item in self._workspaces]
            return {
                "theme": self._theme(),
                "themes": {"items": [dict(item) for item in self._themes], "currentId": self.current_theme,
                           "currentName": next(item["name"] for item in self._themes if item["id"] == self.current_theme)},
                "workspaces": workspaces,
                "windows": [dict(item) for item in self._windows],
                "media": dict(self._media),
                "controls": {},
                "capabilities": {
                    "workspaces": True, "windows": True, "media": True, "theme": True, "windowMove": True,
                    "morph": True, "lock": True, "beam": True,
                },
            }

    def theme_preview(self, theme_id: str) -> tuple[bytes, str]:
        with self._lock:
            theme = next((item for item in self._themes if item["id"] == theme_id), None)
            if theme is None:
                raise AdapterError("target_unavailable")
            return _fake_preview_png(theme["previewAccent"]), "image/png"

    def perform(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if action == "workspace.focus":
                target = parameters["workspaceId"]
                if not any(item["id"] == target for item in self._workspaces):
                    raise AdapterError("target_unavailable")
                self.active_workspace = target
                return {"focusedWorkspaceId": target}
            if action == "window.focus":
                target = parameters["windowId"]
                window = next((item for item in self._windows if item["id"] == target), None)
                if window is None:
                    raise AdapterError("target_unavailable")
                self.active_workspace = window["workspaceId"]
                for item in self._windows:
                    item["focused"] = item["id"] == target
                return {"focusedWindowId": target}
            if action == "window.moveToWorkspace":
                window = next((item for item in self._windows if item["id"] == parameters["windowId"]), None)
                if window is None or not any(item["id"] == parameters["workspaceId"] for item in self._workspaces):
                    raise AdapterError("target_unavailable")
                window["workspaceId"] = parameters["workspaceId"]
                return {"movedWindowId": window["id"], "workspaceId": window["workspaceId"]}
            if action == "media.playPause":
                self._media["playing"] = not self._media["playing"]
                return {"playing": self._media["playing"]}
            if action in {"media.previous", "media.next"}:
                return {"transport": action.split(".")[1]}
            if action == "media.setVolume":
                self._media["volume"] = parameters["volume"]
                return {"volume": self._media["volume"]}
            if action == "theme.set":
                target = parameters["themeId"]
                if not any(item["id"] == target for item in self._themes):
                    raise AdapterError("target_unavailable")
                self.current_theme = target
                selected = next(item for item in self._themes if item["id"] == target)
                return {"themeId": target, "themeName": selected["name"]}
            if action == "theme.backgroundNext":
                self.background_index = (self.background_index + 1) % 3
                return {"backgroundChanged": True}
            if action == "desktop.lock":
                self.locked = True
                return {"locked": True}
            raise AdapterError("target_unavailable")


class OmarchyAdapter:
    """Adapter restricted to public Omarchy commands and fixed Hyprland calls."""

    def __init__(self):
        self._key = secrets.token_bytes(32)
        self._lock = threading.RLock()
        self._workspace_targets: dict[str, str] = {}
        self._window_targets: dict[str, str] = {}
        self._theme_targets: dict[str, str] = {}

    def _run(self, argv: list[str], timeout: float = ADAPTER_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
        executable = shutil.which(argv[0]) if argv else None
        if executable is None:
            raise AdapterError("adapter_unavailable")
        try:
            return subprocess.run([executable, *argv[1:]], text=True, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AdapterError("adapter_unavailable") from error

    def _run_json(self, argv: list[str]) -> Any:
        result = self._run(argv)
        if result.returncode != 0 or len(result.stdout) > 512 * 1024:
            raise AdapterError("adapter_unavailable")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AdapterError("adapter_unavailable") from error

    def lock_state(self) -> tuple[bool, bool]:
        try:
            result = self._run(["omarchy-hyprland-session-locked"], timeout=0.7)
        except AdapterError:
            return False, True
        if result.returncode == 0:
            return True, True
        if result.returncode == 1:
            return True, False
        return False, True

    def _opaque(self, prefix: str, raw: str) -> str:
        digest = hashlib.blake2s(raw.encode("utf-8", "replace"), key=self._key, digest_size=12).hexdigest()
        return f"{prefix}_{digest}"

    def _desktop_state(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        workspaces_raw = self._run_json(["hyprctl", "-j", "workspaces"])
        clients_raw = self._run_json(["hyprctl", "-j", "clients"])
        active_raw = self._run_json(["hyprctl", "-j", "activeworkspace"])
        if not isinstance(workspaces_raw, list) or not isinstance(clients_raw, list) or not isinstance(active_raw, dict):
            raise AdapterError("adapter_unavailable")
        active_raw_id = active_raw.get("id")
        workspace_targets: dict[str, str] = {}
        workspaces: list[dict[str, Any]] = []
        ordered = sorted((item for item in workspaces_raw if isinstance(item, dict)), key=lambda item: (int(item.get("id", 0)) if str(item.get("id", "")).lstrip("-").isdigit() else 10_000, str(item.get("name", ""))))
        for item in ordered[:MAX_WORKSPACES]:
            raw_id = str(item.get("id", ""))
            raw_name = str(item.get("name", raw_id))
            if not raw_id.isdigit() or not 1 <= int(raw_id) <= 99 or len(raw_name) > 64:
                continue
            opaque = self._opaque("ws", raw_id)
            # The target is always a compositor-supplied bounded integer. It
            # can therefore be embedded in Omarchy's fixed typed dispatcher
            # template without accepting a remote dispatch expression.
            workspace_targets[opaque] = raw_id
            label = _safe_label(raw_name, raw_id)[:12]
            workspaces.append({"id": opaque, "label": label, "active": item.get("id") == active_raw_id, "urgent": False})

        window_targets: dict[str, str] = {}
        windows: list[dict[str, Any]] = []
        focused_address = ""
        active_window = self._run(["hyprctl", "-j", "activewindow"])
        if active_window.returncode == 0:
            try:
                focused_address = str(json.loads(active_window.stdout).get("address", ""))
            except (json.JSONDecodeError, AttributeError):
                pass
        raw_to_workspace = {target: opaque for opaque, target in workspace_targets.items()}
        raw_id_to_opaque = {str(item.get("id")): self._opaque("ws", str(item.get("id"))) for item in ordered if isinstance(item, dict)}
        for client in clients_raw:
            if not isinstance(client, dict) or client.get("mapped") is False or client.get("hidden") is True:
                continue
            address = str(client.get("address", ""))
            workspace = client.get("workspace") if isinstance(client.get("workspace"), dict) else {}
            workspace_id = raw_id_to_opaque.get(str(workspace.get("id", "")))
            if not re.fullmatch(r"0x[0-9a-fA-F]+", address) or workspace_id not in workspace_targets:
                continue
            opaque = self._opaque("win", address)
            window_targets[opaque] = address
            windows.append({
                "id": opaque,
                "app": _safe_label(client.get("class") or client.get("initialClass")),
                "workspaceId": workspace_id,
                "focused": address == focused_address,
            })
            if len(windows) >= MAX_WINDOWS:
                break
        with self._lock:
            self._workspace_targets = workspace_targets
            self._window_targets = window_targets
        return workspaces, windows

    def _media(self) -> dict[str, Any]:
        try:
            result = self._run(["omarchy-shell", "media", "status"])
            raw = json.loads(result.stdout) if result.returncode == 0 else {}
        except (AdapterError, json.JSONDecodeError):
            raw = {}
        available = bool(raw.get("hasPlayer"))
        volume = self._volume()
        return {
            "available": available,
            "playing": bool(raw.get("playing")) if available else False,
            "volume": volume,
            "canPrevious": bool(raw.get("canGoPrevious")) if available else False,
            "canNext": bool(raw.get("canGoNext")) if available else False,
        }

    def _volume(self) -> float:
        try:
            sink_name = self._sink_name()
            result = self._run(["pactl", "get-sink-volume", sink_name])
            match = re.search(r"\b(\d{1,3})%", result.stdout)
            return min(1.0, max(0.0, int(match.group(1)) / 100)) if match else 0.0
        except (AdapterError, ValueError):
            return 0.0

    def _sink_name(self) -> str:
        sink = self._run(["omarchy-audio-output-sink"])
        sink_name = sink.stdout.strip()
        if sink.returncode != 0 or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,256}", sink_name) or sink_name.startswith("-"):
            raise AdapterError("adapter_unavailable")
        return sink_name

    def _theme(self) -> dict[str, Any]:
        raw: dict[str, str] = {}
        try:
            result = self._run(["omarchy-theme-color", "--all"])
            if result.returncode == 0:
                for line in result.stdout.splitlines()[:256]:
                    key, separator, value = line.partition("\t")
                    if separator and len(key) <= 48 and len(value) <= 32:
                        raw[key] = value
        except AdapterError:
            pass
        background = sanitized_color(raw.get("background"), "#18181b")
        foreground = sanitized_color(raw.get("foreground"), "#fafafa")
        return {
            "scheme": raw.get("mode") if raw.get("mode") in {"light", "dark"} else "dark",
            "background": background,
            "surface": sanitized_color(raw.get("lighter_background"), background),
            "surfaceElevated": sanitized_color(raw.get("selection"), sanitized_color(raw.get("lighter_background"), background)),
            "text": foreground,
            "textMuted": sanitized_color(raw.get("muted"), sanitized_color(raw.get("dark_foreground"), foreground)),
            "accent": sanitized_color(raw.get("accent"), "#a78bfa"),
            "success": sanitized_color(raw.get("green"), "#4ade80"),
            "warning": sanitized_color(raw.get("yellow"), "#facc15"),
            "danger": sanitized_color(raw.get("red"), "#fb7185"),
            "radius": 18,
        }

    def _themes(self) -> dict[str, Any]:
        try:
            listed = self._run(["omarchy-theme-list"])
            current_result = self._run(["omarchy-theme-current"])
        except AdapterError:
            return {"items": [], "currentId": "", "currentName": ""}
        if listed.returncode != 0 or current_result.returncode != 0 or len(listed.stdout) > 64 * 1024:
            return {"items": [], "currentId": "", "currentName": ""}
        names: list[str] = []
        for line in listed.stdout.splitlines():
            name = _safe_theme_name(line)
            if name and name not in names:
                names.append(name)
            if len(names) >= MAX_THEMES:
                break
        current_name = _safe_theme_name(current_result.stdout) or ""
        targets: dict[str, str] = {}
        items: list[dict[str, Any]] = []
        for name in names:
            opaque = self._opaque("theme", name)
            targets[opaque] = name
            digest = hashlib.blake2s(name.encode("utf-8"), digest_size=2).digest()
            accent = THEME_PREVIEW_ACCENTS[int.from_bytes(digest, "big") % len(THEME_PREVIEW_ACCENTS)]
            items.append({
                "id": opaque,
                "name": name,
                "previewAccent": accent,
                "previewAvailable": self._theme_preview(name, include_body=False) is not None,
            })
        with self._lock:
            self._theme_targets = targets
        current_id = next((identifier for identifier, name in targets.items() if name == current_name), "")
        return {"items": items, "currentId": current_id, "currentName": current_name}

    @staticmethod
    def _theme_slug(name: str) -> str | None:
        slug = name.lower().replace(" ", "-")
        return slug if re.fullmatch(r"[a-z0-9][a-z0-9._+()-]{0,63}", slug) else None

    @staticmethod
    def _read_preview_entry(directory_fd: int, filename: str, include_body: bool) -> tuple[bytes, str] | None:
        suffix = Path(filename).suffix.lower()
        mime = THEME_PREVIEW_MIMES.get(suffix)
        if mime is None:
            return None
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(filename, flags, dir_fd=directory_fd)
        except OSError:
            return None
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_size < 1 or details.st_size > MAX_THEME_PREVIEW_BYTES:
                return None
            if not include_body:
                return b"", mime
            body = bytearray()
            while len(body) <= MAX_THEME_PREVIEW_BYTES:
                part = os.read(descriptor, min(64 * 1024, MAX_THEME_PREVIEW_BYTES + 1 - len(body)))
                if not part:
                    break
                body.extend(part)
            if len(body) != details.st_size or len(body) > MAX_THEME_PREVIEW_BYTES:
                return None
            return bytes(body), mime
        finally:
            os.close(descriptor)

    def _preview_from_theme_root(self, root: Path, include_body: bool) -> tuple[bytes, str] | None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            root_fd = os.open(root, flags)
        except OSError:
            return None
        try:
            names = os.listdir(root_fd)
            by_lower = {name.lower(): name for name in names if isinstance(name, str)}
            for expected in THEME_PREVIEW_FILES:
                actual = by_lower.get(expected)
                if actual is not None:
                    preview = self._read_preview_entry(root_fd, actual, include_body)
                    if preview is not None:
                        return preview
            try:
                backgrounds_fd = os.open("backgrounds", flags, dir_fd=root_fd)
            except OSError:
                return None
            try:
                for name in sorted(os.listdir(backgrounds_fd)):
                    preview = self._read_preview_entry(backgrounds_fd, name, include_body)
                    if preview is not None:
                        return preview
            finally:
                os.close(backgrounds_fd)
        finally:
            os.close(root_fd)
        return None

    def _theme_preview(self, name: str, include_body: bool) -> tuple[bytes, str] | None:
        slug = self._theme_slug(name)
        if slug is None:
            return None
        roots = [Path.home() / ".config" / "omarchy" / "themes" / slug]
        omarchy_root = os.environ.get("OMARCHY_PATH", "")
        if omarchy_root and Path(omarchy_root).is_absolute():
            roots.append(Path(omarchy_root) / "themes" / slug)
        for root in roots:
            preview = self._preview_from_theme_root(root, include_body)
            if preview is not None:
                return preview
        return None

    def theme_preview(self, theme_id: str) -> tuple[bytes, str]:
        # Refresh the trusted inventory so a removed or renamed theme cannot be
        # addressed through an old opaque identifier.
        themes = self._themes()
        name = self._target(self._theme_targets, theme_id)
        if not any(item.get("id") == theme_id and item.get("previewAvailable") for item in themes["items"]):
            raise AdapterError("target_unavailable")
        preview = self._theme_preview(name, include_body=True)
        if preview is None or not _safe_preview_image(*preview):
            raise AdapterError("target_unavailable")
        return preview

    def snapshot(self) -> dict[str, Any]:
        capabilities = {"workspaces": False, "windows": False, "media": False, "theme": True, "windowMove": False, "morph": False, "lock": True, "beam": False}
        try:
            workspaces, windows = self._desktop_state()
            capabilities["workspaces"] = True
            capabilities["windows"] = True
            capabilities["windowMove"] = True
        except AdapterError:
            workspaces, windows = [], []
        media = self._media()
        capabilities["media"] = media["available"]
        capabilities["beam"] = media["available"]
        themes = self._themes()
        capabilities["morph"] = bool(themes["items"] and themes["currentId"])
        return {
            "theme": self._theme(),
            "themes": themes,
            "workspaces": workspaces,
            "windows": windows,
            "media": media,
            "controls": {},
            "capabilities": capabilities,
        }

    def _target(self, collection: dict[str, str], opaque_id: Any) -> str:
        if not isinstance(opaque_id, str):
            raise AdapterError("target_unavailable")
        with self._lock:
            target = collection.get(opaque_id)
        if not target:
            raise AdapterError("target_unavailable")
        return target

    def perform(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if action == "workspace.focus":
            target = self._target(self._workspace_targets, parameters["workspaceId"])
            if not target.isdigit() or not 1 <= int(target) <= 99:
                raise AdapterError("target_unavailable")
            result = self._run(["hyprctl", "dispatch", f'hl.dsp.focus({{ workspace = "{target}" }})'])
            if result.returncode != 0:
                result = self._run(["hyprctl", "dispatch", "workspace", target])
            if result.returncode != 0:
                raise AdapterError("action_failed")
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                active = self._run_json(["hyprctl", "-j", "activeworkspace"])
                if isinstance(active, dict) and str(active.get("id")) == target:
                    return {"focusedWorkspaceId": parameters["workspaceId"]}
                time.sleep(0.05)
            raise AdapterError("action_failed")
        if action == "window.focus":
            target = self._target(self._window_targets, parameters["windowId"])
            if not re.fullmatch(r"0x[0-9a-fA-F]+", target):
                raise AdapterError("target_unavailable")
            result = self._run(["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "address:{target}" }})'])
            if result.returncode != 0:
                result = self._run(["hyprctl", "dispatch", "focuswindow", f"address:{target}"])
            if result.returncode != 0:
                raise AdapterError("action_failed")
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                active = self._run_json(["hyprctl", "-j", "activewindow"])
                if isinstance(active, dict) and str(active.get("address")) == target:
                    return {"focusedWindowId": parameters["windowId"]}
                time.sleep(0.05)
            raise AdapterError("action_failed")
        if action == "window.moveToWorkspace":
            # Refresh both maps immediately before resolving the remote opaque
            # identifiers. The fixed typed dispatcher receives only current
            # compositor-supplied numeric workspace/address values.
            self._desktop_state()
            workspace = self._target(self._workspace_targets, parameters["workspaceId"])
            window = self._target(self._window_targets, parameters["windowId"])
            if not workspace.isdigit() or not 1 <= int(workspace) <= 99 or not re.fullmatch(r"0x[0-9a-fA-F]+", window):
                raise AdapterError("target_unavailable")
            result = self._run([
                "hyprctl", "dispatch",
                f'hl.dsp.window.move({{ workspace = "{workspace}", follow = false, window = "address:{window}" }})',
            ])
            if result.returncode != 0:
                raise AdapterError("action_failed")
            deadline = time.monotonic() + 1.0
            confirmed = False
            while time.monotonic() < deadline:
                clients = self._run_json(["hyprctl", "-j", "clients"])
                confirmed = isinstance(clients, list) and any(
                    isinstance(item, dict) and str(item.get("address")) == window
                    and isinstance(item.get("workspace"), dict)
                    and str(item["workspace"].get("id")) == workspace
                    for item in clients
                )
                if confirmed:
                    break
                time.sleep(0.05)
            if not confirmed:
                raise AdapterError("action_failed")
            return {"movedWindowId": parameters["windowId"], "workspaceId": parameters["workspaceId"]}
        if action in {"media.playPause", "media.previous", "media.next"}:
            method = {"media.playPause": "playPause", "media.previous": "previous", "media.next": "next"}[action]
            result = self._run(["omarchy-shell", "media", method])
            if result.returncode != 0 or result.stdout.strip() not in {"ok", ""}:
                raise AdapterError("action_failed")
            return {"transport": method}
        if action == "media.setVolume":
            percent = int(round(parameters["volume"] * 100))
            try:
                sink_name = self._sink_name()
            except AdapterError:
                raise AdapterError("action_failed")
            result = self._run(["pactl", "set-sink-volume", sink_name, f"{percent}%"])
            if result.returncode != 0:
                raise AdapterError("action_failed")
            self._run(["pactl", "set-sink-mute", sink_name, "0"])
            return {"volume": percent / 100}
        if action == "theme.set":
            # Rebuild the inventory at execution time so an uninstalled or
            # renamed theme cannot remain addressable through a stale map.
            themes = self._themes()
            target = self._target(self._theme_targets, parameters["themeId"])
            if not any(item.get("id") == parameters["themeId"] for item in themes["items"]):
                raise AdapterError("target_unavailable")
            result = self._run(["omarchy-theme-set", target], timeout=45.0)
            if result.returncode != 0:
                raise AdapterError("action_failed")
            current = self._run(["omarchy-theme-current"], timeout=2.0)
            if current.returncode != 0 or current.stdout.strip() != target:
                raise AdapterError("action_failed")
            return {"themeId": parameters["themeId"], "themeName": target}
        if action == "theme.backgroundNext":
            result = self._run(["omarchy-theme-bg-next"], timeout=10.0)
            if result.returncode != 0:
                raise AdapterError("action_failed")
            return {"backgroundChanged": True}
        if action == "desktop.lock":
            result = self._run(["omarchy-system-lock"], timeout=3.0)
            if result.returncode != 0:
                raise AdapterError("action_failed")
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                known, locked = self.lock_state()
                if known and locked:
                    return {"locked": True}
                time.sleep(0.05)
            raise AdapterError("action_failed")
        raise AdapterError("target_unavailable")
