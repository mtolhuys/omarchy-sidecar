#!/bin/bash

# Prove maintained public desktop contracts in the disposable guest before
# the production adapter relies on them.

omarchy_host_test() {
  local lab_root project_dir source_address current_theme target_theme candidate
  local palette_before palette_after background_before background_after
  lab_root="${OMARCHY_PLUGIN_LAB_ROOT:?Set OMARCHY_PLUGIN_LAB_ROOT to the disposable Plugin Lab checkout}"
  project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
  source "$lab_root/host-tests/helpers/pointer.sh"

  log "Staging the candidate for Downloads, stream, and immutable-graph spikes"
  tar -C "$project_dir" --exclude __pycache__ --exclude '*.pyc' -cf - . | ssh_guest \
    "rm -rf /tmp/sidecar-phase0 && mkdir -p /tmp/sidecar-phase0 && tar -C /tmp/sidecar-phase0 -xf -"
  ssh_session "python3 - <<'PY'
import sys
assert sys.version_info[:2] == (3, 14), sys.version
PY
cd /tmp/sidecar-phase0 && python3 -B -m unittest \
  tests.test_inbox.InboxPolicyTests.test_downloads_resolution_is_fixed_bounded_and_shell_free \
  tests.test_inbox.InboxPolicyTests.test_active_content_spoof_low_space_cancel_and_restart_cleanup_fail_closed && \
command -v xdg-open >/dev/null && command -v omarchy-notification-send >/dev/null && \
test -x \"\$OMARCHY_PATH/bin/omarchy-tailscale-receive\" && \
jq -e '.share_target.method == \"POST\" and .share_target.enctype == \"multipart/form-data\"' /tmp/sidecar-phase0/web/dist/manifest.webmanifest && \
grep -F 'sidecar-web-v1012-final' /tmp/sidecar-phase0/web/dist/sw.v1012.js >/dev/null && \
test ! -e /tmp/sidecar-phase0/service/v1008 && test ! -e /tmp/sidecar-phase0/bar-widget/v1008"

  log "Proving the mounted desktop notification route"
  ssh_session "omarchy-notification-send 'Sidecar Drop' 'Phase 0 generic file received' -g '󰉋'"
  sleep 0.8
  capture_console "success-sidecar-v1012-phase0-notification"

  qmp_key_chord() {
    local modifier="$1" key="$2" response
    response=$(qmp "\"input-send-event\", \"arguments\": {\"events\": [
      {\"type\":\"key\",\"data\":{\"down\":true,\"key\":{\"type\":\"qcode\",\"data\":\"$modifier\"}}},
      {\"type\":\"key\",\"data\":{\"down\":true,\"key\":{\"type\":\"qcode\",\"data\":\"$key\"}}},
      {\"type\":\"key\",\"data\":{\"down\":false,\"key\":{\"type\":\"qcode\",\"data\":\"$key\"}}},
      {\"type\":\"key\",\"data\":{\"down\":false,\"key\":{\"type\":\"qcode\",\"data\":\"$modifier\"}}}
    ]}")
    ! grep -q '"error"' <<<"$response"
    sleep 0.7
  }

  log "Proving addressed non-following window movement"
  qmp_key_chord meta_l 1
  qmp_key_chord meta_l ret
  qmp_key_chord meta_l 2
  qmp_key_chord meta_l ret
  qmp_key_chord meta_l 1
  wait_for_guest_state "two workspaces have mapped windows" 12 ssh_session \
    "test \"\$(hyprctl -j workspaces | jq length)\" -ge 2 && \
     test \"\$(hyprctl -j clients | jq '[.[] | select(.mapped != false and .hidden != true)] | length')\" -ge 2" || return 1
  source_address="$(ssh_session "hyprctl -j clients | jq -r \
    '[.[] | select(.mapped != false and .hidden != true and .workspace.id == 1)][0].address'")"
  [[ $source_address =~ ^0x[0-9a-fA-F]+$ ]] || return 1
  ssh_session "hyprctl dispatch 'hl.dsp.window.move({ workspace = \"2\", follow = false, window = \"address:$source_address\" })'"
  wait_for_guest_state "the addressed window moves without following" 10 ssh_session \
    "hyprctl -j clients | jq -e --arg address '$source_address' \
       'any(.[]; .address == \$address and .workspace.id == 2)' && \
     hyprctl -j activeworkspace | jq -e '.id == 1'" || return 1

  log "Proving installed themes, semantic colors, exact activation, and restoration"
  ssh_session "omarchy-theme-list | awk 'NF' | sort -u | tee /tmp/sidecar-theme-inventory | test \"\$(wc -l)\" -ge 2"
  current_theme="$(ssh_session "omarchy-theme-current")"
  palette_before="$(ssh_session "omarchy-theme-color --all | sha256sum | cut -d' ' -f1")"
  ssh_session "omarchy-theme-color --all | awk -F '\\t' '
    \$1 == \"background\" { background = 1 }
    \$1 == \"foreground\" { foreground = 1 }
    \$1 == \"accent\" || \$1 == \"blue\" || \$1 == \"yellow\" { accent = 1 }
    END { exit !(background && foreground && accent) }
  '"
  target_theme=""
  while IFS= read -r candidate; do
    if [[ $candidate != "$current_theme" ]]; then
      target_theme="$candidate"
      break
    fi
  done < <(ssh_session "cat /tmp/sidecar-theme-inventory")
  [[ -n $target_theme ]] || return 1
  ssh_session "omarchy-theme-set '$target_theme'"
  wait_for_guest_state "the exact inventory theme becomes authoritative" 20 ssh_session \
    "test \"\$(omarchy-theme-current)\" = '$target_theme'" || return 1
  palette_after="$(ssh_session "omarchy-theme-color --all | sha256sum | cut -d' ' -f1")"
  [[ $palette_after != "$palette_before" ]] || return 1
  ssh_session "omarchy-theme-set '$current_theme'"
  wait_for_guest_state "the original theme is restored" 20 ssh_session \
    "test \"\$(omarchy-theme-current)\" = '$current_theme'" || return 1

  log "Proving the fixed next-background command"
  target_theme="$(ssh_session 'for directory in "$OMARCHY_PATH"/themes/*; do
    count=$(find "$directory/backgrounds" -maxdepth 1 -type f 2>/dev/null | wc -l)
    if (( count > 1 )); then basename "$directory" | sed -E "s/(^|-)([a-z])/\1\u\2/g; s/-/ /g"; break; fi
  done')"
  [[ -n $target_theme ]] || return 1
  ssh_session "omarchy-theme-set '$target_theme'"
  background_before="$(ssh_session 'readlink "$HOME/.local/state/omarchy/current/background"')"
  ssh_session "omarchy-theme-bg-next"
  background_after="$(ssh_session 'readlink "$HOME/.local/state/omarchy/current/background"')"
  [[ -n $background_before && -n $background_after && $background_after != "$background_before" ]] || return 1
  ssh_session "omarchy-theme-set '$current_theme'"

  log "Proving fixed session lock and public lock truth"
  ssh_session "omarchy-system-lock"
  wait_for_guest_state "session enters the locked state" 12 ssh_session \
    "omarchy-hyprland-session-locked" || return 1
  capture_console "success-sidecar-v1012-contracts-locked"
  type_text "$GUEST_PASSWORD"
  press ret
  wait_for_guest_state "QMP password input unlocks the session" 15 ssh_session \
    "! omarchy-hyprland-session-locked" || return 1
  ssh_session "test -z \"\$(hyprctl configerrors)\""

  printf 'ok - addressed move, trusted themes, semantic colors, background, and lock contracts passed\n'
}
