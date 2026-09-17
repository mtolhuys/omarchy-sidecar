#!/bin/bash

# Produce deterministic real-desktop Sidecar captures in the disposable lab.
# The fake Tailscale client is isolated to the guest and owns no real identity.

omarchy_host_test() {
  local project_dir lab_root plugin_dir geometry icon_x icon_y
  local widget_width widget_height screen_width screen_height
  project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
  lab_root="${OMARCHY_PLUGIN_LAB_ROOT:?Set OMARCHY_PLUGIN_LAB_ROOT to the disposable Plugin Lab checkout}"
  plugin_dir='${HOME}/.config/omarchy/plugins/io.github.mtolhuys.sidecar'

  source "$lab_root/host-tests/helpers/pointer.sh"

  qmp_pointer_move() {
    local width="$1" height="$2" x="$3" y="$4" qx qy response
    qx=$((x * 32767 / (width - 1)))
    qy=$((y * 32767 / (height - 1)))
    response=$(qmp "\"input-send-event\", \"arguments\": {\"events\": [
      {\"type\":\"abs\",\"data\":{\"axis\":\"x\",\"value\":$qx}},
      {\"type\":\"abs\",\"data\":{\"axis\":\"y\",\"value\":$qy}}
    ]}")
    ! grep -q '"error"' <<<"$response"
    sleep 0.4
  }

  log "Staging the exact Sidecar v1 candidate for clean marketing captures"
  tar -C "$project_dir" --exclude __pycache__ --exclude '*.pyc' -cf - . | ssh_guest \
    "rm -rf /tmp/sidecar-marketing && mkdir -p /tmp/sidecar-marketing && tar -C /tmp/sidecar-marketing -xf -"
  ssh_guest "git -C /tmp/sidecar-marketing init -q && git -C /tmp/sidecar-marketing add . && \
    git -C /tmp/sidecar-marketing -c user.name=SidecarLab -c user.email=lab@invalid \
      commit -qm marketing-captures && \
    rm -f /tmp/sidecar-fake-tailscale.log /tmp/sidecar-fake-tailscale.pid \
      /tmp/sidecar-fake-tailscale-conflict /tmp/sidecar-fake-tailscale-https-pending"
  ssh_guest "echo '$GUEST_PASSWORD' | sudo -S install -m 0755 \
    /tmp/sidecar-marketing/tests/lab/fixtures/fake-tailscale /usr/local/bin/tailscale"
  ssh_session "rm -rf \"\$HOME/.config/omarchy/plugins/io.github.mtolhuys.sidecar\"; \
    omarchy-plugin-add /tmp/sidecar-marketing --enable --yes"

  wait_for_guest_state "marketing candidate reaches exact-route ready state" 30 ssh_session \
    "omarchy-shell sidecar identity | jq -e \
      '.service == \"sidecar-service-v1013\" and .widget == \"sidecar-widget-v1013\" and .state == \"ready\"' && \
     \"$plugin_dir/helper/sidecarctl\" status | jq -e \
      '.state == \"ready\" and .route.state == \"ready\" and .lastError == null and .deviceCount == 0' && \
     test -z \"\$(find \"$plugin_dir\" -type d -name __pycache__ -print -quit)\"" || return 1

  ssh_session "python3 /tmp/sidecar-marketing/tests/lab/pair_control.py --marketing \"$plugin_dir/helper/sidecarctl\""
  wait_for_guest_state "clean paired phone reaches the compact Sidecar panel" 15 ssh_session \
    "\"$plugin_dir/helper/sidecarctl\" status | jq -e '.deviceCount == 1 and .devices[0].name == \"Demo phone\" and .inbox.receivedCount == 1 and .inbox.lastKind == \"Text\" and .inbox.stagingCount == 0'" || return 1

  geometry="$(ssh_session "omarchy-shell shell debugBarGeometry | jq -r \
    '.[] | select(.id == \"io.github.mtolhuys.sidecar\" and .visible) | [.x,.y,.width,.height] | @tsv' | head -n1")"
  read -r icon_x icon_y widget_width widget_height <<<"$geometry"
  icon_x=$((icon_x + widget_width / 2))
  icon_y=$((icon_y + widget_height / 2))
  read -r screen_width screen_height < <(ssh_session \
    "hyprctl -j monitors | jq -r 'map(select(.focused))[0] // .[0] | [.width,.height] | @tsv'")

  qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  wait_for_guest_state "real bar phone opens the ready panel" 10 ssh_session \
    "omarchy-shell sidecar identity | jq -e '.open == true and .state == \"ready\"'" || return 1
  qmp_pointer_move "$screen_width" "$screen_height" 24 "$((screen_height - 24))"
  capture_console "marketing-sidecar-desktop-drop-attention"

  ssh_session "omarchy-plugin-remove io.github.mtolhuys.sidecar --yes"
  wait_for_guest_state "marketing capture cleans every product-owned process and listener" 20 ssh_session \
    "test ! -e \"$plugin_dir\" && \
     ! pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' >/dev/null && \
     ! ss -ltn | grep -F ':47991' >/dev/null && ! test -e /tmp/sidecar-fake-tailscale.pid" || return 1
  ssh_guest "echo '$GUEST_PASSWORD' | sudo -S rm -f /usr/local/bin/tailscale"

  printf 'ok - clean real-desktop Drop attention capture produced\n'
}
