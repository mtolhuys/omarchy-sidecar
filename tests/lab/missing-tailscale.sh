#!/bin/bash

# Missing dependency, delayed availability, HTTPS consent, and QR guidance in
# the disposable guest. No real tailnet is read or changed.

omarchy_host_test() {
  local project_dir lab_root plugin_dir geometry icon_x icon_y screen_width screen_height helper_pid browser_address
  project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
  lab_root="${OMARCHY_PLUGIN_LAB_ROOT:?Set OMARCHY_PLUGIN_LAB_ROOT to the disposable Plugin Lab checkout}"
  plugin_dir='${HOME}/.config/omarchy/plugins/io.github.mtolhuys.sidecar'

  source "$lab_root/host-tests/helpers/pointer.sh"

  log "Staging Sidecar without Tailscale to exercise first-run recovery"
  tar -C "$project_dir" --exclude __pycache__ --exclude '*.pyc' -cf - . | ssh_guest \
    "rm -rf /tmp/sidecar-missing && mkdir -p /tmp/sidecar-missing && tar -C /tmp/sidecar-missing -xf -"
  ssh_guest "git -C /tmp/sidecar-missing init -q && git -C /tmp/sidecar-missing add . && \
    git -C /tmp/sidecar-missing -c user.name=SidecarLab -c user.email=lab@invalid commit -qm missing-guidance && \
    rm -f /tmp/sidecar-fake-tailscale.log /tmp/sidecar-fake-tailscale.pid \
      /tmp/sidecar-fake-tailscale-conflict /tmp/sidecar-fake-tailscale-https-pending"
  ssh_session "rm -rf \"\$HOME/.config/omarchy/plugins/io.github.mtolhuys.sidecar\"; \
    omarchy-plugin-add /tmp/sidecar-missing --enable --yes"

  wait_for_guest_state "missing Tailscale fails closed with guided automatic recovery" 30 ssh_session \
    "omarchy-shell sidecar identity | jq -e '.state == \"unavailable\"' && \
     \"$plugin_dir/helper/sidecarctl\" status | jq -e \
       '.lastError.code == \"tailscale-unavailable\" and \
        (.lastError.message | contains(\"Open Omarchy Install → Service → Tailscale\")) and \
        .route.childPid == null' && \
     test -z \"\$(find \"$plugin_dir\" -type d -name __pycache__ -print -quit)\"" || return 1
  helper_pid="$(ssh_session "pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1")"

  geometry="$(ssh_session "omarchy-shell shell debugBarGeometry | jq -r \
    '.[] | select(.id == \"io.github.mtolhuys.sidecar\" and .visible) | [.x,.y,.width,.height] | @tsv' | head -n1")"
  read -r icon_x icon_y screen_width screen_height <<<"$geometry"
  icon_x=$((icon_x + screen_width / 2))
  icon_y=$((icon_y + screen_height / 2))
  read -r screen_width screen_height < <(ssh_session \
    "hyprctl -j monitors | jq -r 'map(select(.focused))[0] // .[0] | [.width,.height] | @tsv'")
  qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  wait_for_guest_state "public pointer opens missing-Tailscale guidance" 10 ssh_session \
    "omarchy-shell sidecar identity | jq -e '.open == true'" || return 1
  capture_console "success-sidecar-missing-01-guidance"

  qmp_pointer_tap "$screen_width" "$screen_height" "$((icon_x - 180))" 240 left
  wait_for_guest_state "visible recovery opens Omarchy's neutral Service menu" 10 ssh_session \
    "hyprctl -j layers | jq -e '[.. | objects | select(.namespace? == \"omarchy-menu\")] | length >= 1'" || {
    capture_console "failure-sidecar-missing-service-button-coordinate"
    return 1
  }
  press esc
  wait_for_guest_state "Escape closes the Omarchy Service menu" 10 ssh_session \
    "hyprctl -j layers | jq -e '[.. | objects | select(.namespace? == \"omarchy-menu\")] | length == 0'" || return 1

  log "Making Tailscale appear while HTTPS consent remains deliberately pending"
  ssh_session "touch /tmp/sidecar-fake-tailscale-https-pending"
  ssh_guest "echo '$GUEST_PASSWORD' | sudo -S install -m 0755 \
      /tmp/sidecar-missing/tests/lab/fixtures/fake-tailscale /usr/local/bin/tailscale"
  wait_for_guest_state "same helper notices Tailscale but refuses a QR without the exact HTTPS route" 30 ssh_session \
    "test \"\$(pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1)\" = '$helper_pid' && \
     \"$plugin_dir/helper/sidecarctl\" status | jq -e \
       '.state == \"unavailable\" and .lastError.code == \"tailscale-https-unavailable\" and \
        .route.childPid == null and .pairing == null' && \
     ! test -e /tmp/sidecar-fake-tailscale.pid" || {
    ssh_session "\"$plugin_dir/helper/sidecarctl\" status; cat /tmp/sidecar-fake-tailscale.log" || true
    return 1
  }
  if ! ssh_session "omarchy-shell sidecar identity | jq -e '.open == true'" >/dev/null; then
    qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  fi
  wait_for_guest_state "HTTPS recovery guidance is visibly open" 10 ssh_session \
    "omarchy-shell sidecar identity | jq -e '.open == true'" || return 1
  capture_console "success-sidecar-missing-02-https-guidance"

  qmp_pointer_tap "$screen_width" "$screen_height" "$((icon_x - 180))" 240 left
  wait_for_guest_state "visible HTTPS action launches the fixed official browser handoff" 10 ssh_session \
    "hyprctl -j clients | jq -e 'any(.[]; .mapped != false and .hidden != true)'" || {
    capture_console "failure-sidecar-missing-https-button-coordinate"
    return 1
  }

  # A pristine Omarchy browser may put its first-run terms dialog under an
  # exclusive input grab. Close only the just-launched disposable client by
  # its compositor address before asserting another layer-shell click.
  browser_address="$(ssh_session "hyprctl -j activewindow | jq -r '.address // empty'")"
  [[ "$browser_address" =~ ^0x[0-9a-fA-F]+$ ]] || {
    printf 'not ok - browser handoff did not expose a safe active-window address\n' >&2
    return 1
  }
  ssh_session "hyprctl dispatch 'hl.dsp.window.close({ window = \"address:$browser_address\" })'"
  wait_for_guest_state "browser handoff releases its input grab" 10 ssh_session \
    "hyprctl -j clients | jq -e 'all(.[]; .address != \"$browser_address\")'" || return 1
  if ! ssh_session "omarchy-shell sidecar identity | jq -e '.open == true'" >/dev/null; then
    qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  fi
  wait_for_guest_state "Sidecar panel regains input after the browser handoff" 10 ssh_session \
    "omarchy-shell sidecar identity | jq -e '.open == true'" || return 1

  log "Completing HTTPS consent and proving automatic recovery without a click or helper restart"
  ssh_session "rm -f /tmp/sidecar-fake-tailscale-https-pending"
  wait_for_guest_state "same helper activates and verifies only its exact Serve route" 30 ssh_session \
    "test \"\$(pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1)\" = '$helper_pid' && \
     \"$plugin_dir/helper/sidecarctl\" status | jq -e \
       '.state == \"ready\" and .lastError == null and .route.state == \"ready\" and \
        .route.childPid != null and .pairing == null' && \
     tailscale serve status --json | jq -e \
       '.Web[\"sidecar-lab.example.ts.net:48719\"].Handlers[\"/\"].Proxy == \"http://127.0.0.1:47991\"'" || {
    ssh_session "\"$plugin_dir/helper/sidecarctl\" status; tailscale serve status --json; cat /tmp/sidecar-fake-tailscale.log" || true
    return 1
  }

  # Reopen the compact ready state through its public bar icon. This clears
  # any stale layer focus left behind by the first-run browser handoff before
  # the Pair button itself is exercised.
  if ssh_session "omarchy-shell sidecar identity | jq -e '.open == true'" >/dev/null; then
    qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
    wait_for_guest_state "bar icon closes the recovered panel" 10 ssh_session \
      "omarchy-shell sidecar identity | jq -e '.open == false'" || return 1
  fi
  qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  wait_for_guest_state "bar icon reopens the compact ready panel" 10 ssh_session \
    "omarchy-shell sidecar identity | jq -e '.open == true'" || return 1
  qmp_pointer_tap "$screen_width" "$screen_height" "$((icon_x + 25))" 114 left
  if ! ssh_session "\"$plugin_dir/helper/sidecarctl\" status | jq -e '.pairing.active'" >/dev/null; then
    press tab
    press ret
  fi
  wait_for_guest_state "visible phone acknowledgement opens a real pairing session" 10 ssh_session \
    "\"$plugin_dir/helper/sidecarctl\" status | jq -e \
      '.pairing.active and .pairing.endpoint == \"https://sidecar-lab.example.ts.net:48719\" and \
       (.pairing.qrDataUrl | startswith(\"data:image/svg+xml;base64,\"))'" || {
    capture_console "failure-sidecar-missing-pair-button-coordinate"
    ssh_session "\"$plugin_dir/helper/sidecarctl\" status; omarchy-shell sidecar identity" || true
    return 1
  }
  capture_console "success-sidecar-missing-03-phone-check-and-qr"

  ssh_session "omarchy-plugin-remove io.github.mtolhuys.sidecar --yes"
  wait_for_guest_state "recovery test removes every product-owned process and listener" 20 ssh_session \
    "test ! -e \"$plugin_dir\" && \
     ! pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' >/dev/null && \
     ! ss -ltn | grep -F ':47991' >/dev/null && ! test -e /tmp/sidecar-fake-tailscale.pid" || return 1
  ssh_guest "echo '$GUEST_PASSWORD' | sudo -S rm -f /usr/local/bin/tailscale"

  printf 'ok - delayed Tailscale, HTTPS consent, exact-route readiness, phone guidance, QR, and cleanup passed\n'
}
