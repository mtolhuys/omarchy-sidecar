"""Central protocol, build, and resource limits."""

from __future__ import annotations

VERSION = "0.2.1"
PROTOCOL = 1
HELPER_BUILD_ID = "sidecard-v1012"
WEB_BUILD_ID = "sidecar-web-v1012"
SERVICE_BUILD_ID = "sidecar-service-v1012"
WIDGET_BUILD_ID = "sidecar-widget-v1012"

LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 47991
TAILSCALE_HTTPS_PORT = 48719

MAX_DEVICES = 8
MAX_STREAMS = 4
MAX_HTTP_CONNECTIONS = 16
MAX_PENDING_REQUESTS = 4
MAX_REQUEST_BODY = 16 * 1024
MAX_CONTROL_REQUEST = 16 * 1024
MAX_SNAPSHOT_BYTES = 128 * 1024
MAX_FRIENDLY_NAME = 64
MAX_PLATFORM = 32
MAX_ID = 128
MAX_WORKSPACES = 10
MAX_WINDOWS = 16
MAX_THEMES = 48
MAX_THEME_NAME = 64
MAX_THEME_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_INBOX_FILES = 5
MAX_INBOX_FILE_BYTES = 25 * 1024 * 1024
MAX_INBOX_BATCH_BYTES = 50 * 1024 * 1024
MAX_INBOX_FILENAME_CHARS = 120
MAX_INBOX_PENDING_PER_DEVICE = 4
MAX_INBOX_PENDING_GLOBAL = 16
MAX_INBOX_UPLOADS_GLOBAL = 2
INBOX_INTENT_LIFETIME_SECONDS = 300
INBOX_CHUNK_BYTES = 64 * 1024
INBOX_FREE_SPACE_RESERVE_BYTES = 256 * 1024 * 1024
INBOX_DEVICE_FILES_PER_HOUR = 20
INBOX_DEVICE_BYTES_PER_HOUR = 100 * 1024 * 1024
MAX_CAPABILITY_REQUESTS = 4
MAX_REPLAY_PER_DEVICE = 128
MAX_EVENT_QUEUE_ITEMS = 32
MAX_EVENT_QUEUE_BYTES = 512 * 1024
PAIRING_LIFETIME_SECONDS = 120
PENDING_LIFETIME_SECONDS = 120
REPLAY_LIFETIME_SECONDS = 300
HEADER_TIMEOUT_SECONDS = 5
BODY_TIMEOUT_SECONDS = 5
ADAPTER_TIMEOUT_SECONDS = 1.5
EVENT_HEARTBEAT_SECONDS = 15

DEFAULT_SCOPES = (
    "read:desktop",
    "control:workspace",
    "control:window-focus",
    "control:window-move",
    "control:media",
    "control:theme",
    "control:lock",
)

# Retired scopes remain parseable so an existing credential is never
# corrupted by an update. They authorize no action and are never offered to a
# new phone or accepted through remote capability upgrade.
RETIRED_SCOPES = (
    "read:carry",
    "notify:carry",
    "respond:carry-choice",
    "control:focus-mode",
    "control:agent-focus",
    "control:presentation",
)
INBOX_SCOPE = "write:inbox"
ALL_SCOPES = DEFAULT_SCOPES + (INBOX_SCOPE,) + RETIRED_SCOPES
REQUESTABLE_SCOPES = (
    "control:window-move",
    "control:theme",
    "control:lock",
    INBOX_SCOPE,
)

SCOPE_LABELS = {
    "read:desktop": "See workspace and app identity",
    "control:workspace": "Switch Portal workspaces",
    "control:window-focus": "Focus Portal apps",
    "control:window-move": "Move apps between workspaces",
    "control:media": "Use the media Beam",
    "control:theme": "Switch installed themes and backgrounds",
    "control:lock": "Lock this desktop",
    INBOX_SCOPE: "Send allowed files to Sidecar Inbox",
}

ACTION_SCOPES = {
    "workspace.focus": "control:workspace",
    "window.focus": "control:window-focus",
    "window.moveToWorkspace": "control:window-move",
    "media.playPause": "control:media",
    "media.previous": "control:media",
    "media.next": "control:media",
    "theme.set": "control:theme",
    "theme.backgroundNext": "control:theme",
    "desktop.lock": "control:lock",
}

SAFE_ERROR_MESSAGES = {
    "bad_request": "That request was not valid.",
    "not_authenticated": "This phone is not paired with Sidecar.",
    "permission_denied": "This device does not have that permission.",
    "rate_limited": "Too many requests. Wait a moment and try again.",
    "pairing_expired": "This pairing code expired. Ask the desktop for a new code.",
    "pairing_consumed": "This pairing code was already used. Ask the desktop for a new code.",
    "pairing_pending": "Waiting for approval on the desktop.",
    "pairing_denied": "The pairing request was denied on the desktop.",
    "desktop_locked": "Your desktop locked. Controls are paused.",
    "sidecar_paused": "Sidecar is paused on the desktop.",
    "target_unavailable": "That desktop target is no longer available.",
    "action_failed": "The desktop could not complete that action.",
    "protocol_mismatch": "The phone and desktop Sidecar versions do not match.",
    "session_revoked": "This phone was unpaired from the desktop.",
    "service_unavailable": "Sidecar is not ready yet.",
    "capability_pending": "Waiting for capability approval on the desktop.",
    "capability_denied": "That capability request was denied on the desktop.",
    "theme_unavailable": "That installed theme is no longer available.",
    "upload_expired": "This Drop expired. Choose the files again.",
    "upload_rejected": "That file does not match Sidecar's allowed file policy.",
    "upload_interrupted": "The transfer stopped before the desktop received the complete file.",
    "inbox_full": "Sidecar Inbox needs more free disk space before receiving this Drop.",
    "upload_busy": "Sidecar is receiving other files. Wait a moment and try again.",
}
