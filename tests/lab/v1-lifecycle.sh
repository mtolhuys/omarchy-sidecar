#!/bin/bash

# Complete Sidecar lifecycle acceptance in the disposable guest.

omarchy_host_test() {
  local project_dir lab_root plugin_dir geometry icon_x icon_y screen_width screen_height
  local pending_json request_id capability_json capability_request_id device_id
  local drop_name collision_name drop_digest
  local helper_pid replacement_pid route_pid helper_uid
  local security_before security_after tailscale_identity_before tailscale_identity_after
  local unrelated_serve_before unrelated_serve_after
  project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
  lab_root="${OMARCHY_PLUGIN_LAB_ROOT:?Set OMARCHY_PLUGIN_LAB_ROOT to the disposable Plugin Lab checkout}"
  plugin_dir='${HOME}/.config/omarchy/plugins/io.github.mtolhuys.sidecar'
  source "$lab_root/host-tests/helpers/pointer.sh"

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

  log "Staging v1013 and an isolated fake Tailscale contract"
  tar -C "$project_dir" --exclude __pycache__ --exclude '*.pyc' -cf - . | ssh_guest "rm -rf /tmp/sidecar-v1 && mkdir -p /tmp/sidecar-v1 && tar -C /tmp/sidecar-v1 -xf -"
  ssh_guest "git -C /tmp/sidecar-v1 init -q && git -C /tmp/sidecar-v1 add . && git -C /tmp/sidecar-v1 -c user.name=SidecarLab -c user.email=lab@invalid commit -qm v1013"

  security_before="$(ssh_guest "echo '$GUEST_PASSWORD' | sudo -S -p '' bash /tmp/sidecar-v1/tests/lab/security-snapshot.sh")"
  jq -e '.sudoers and .sshFiles and .systemUnits and .runningSystemServices and .suidFiles and .firewall and .listeners and .accounts' <<<"$security_before" >/dev/null
  ssh_guest "echo '$GUEST_PASSWORD' | sudo -S install -m 0755 /tmp/sidecar-v1/tests/lab/fixtures/fake-tailscale /usr/local/bin/tailscale && rm -f /tmp/sidecar-fake-tailscale.log /tmp/sidecar-fake-tailscale.pid /tmp/sidecar-fake-tailscale-conflict /tmp/sidecar-fake-tailscale-https-pending"
  tailscale_identity_before="$(ssh_session "tailscale status --json | jq -c '{BackendState,Self:{DNSName:.Self.DNSName}}'")"
  unrelated_serve_before="$(ssh_session "tailscale serve status --json | jq -c 'del(.TCP[\"48719\"])'")"

  ssh_session "rm -rf \"\$HOME/.config/omarchy/plugins/io.github.mtolhuys.sidecar\"; omarchy-plugin-add /tmp/sidecar-v1 --enable --yes"
  wait_for_guest_state "service, widget, helper, and web graph v1013 agree" 30 ssh_session "omarchy-shell sidecar identity | jq -e '.service == \"sidecar-service-v1013\" and .widget == \"sidecar-widget-v1013\" and .helper == \"sidecard-v1013\" and .web == \"sidecar-web-v1013\" and .state == \"ready\"'" || {
    ssh_session "omarchy-shell shell listPlugins | jq '.[] | select(.id == \"io.github.mtolhuys.sidecar\")'; journalctl --user --since '-3 minutes' --no-pager | tail -n 180" || true
    return 1
  }

  helper_pid="$(ssh_session "pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1")"
  helper_uid="$(ssh_session "ps -o uid= -p '$helper_pid' | tr -d ' '")"
  route_pid="$(ssh_session "cat /tmp/sidecar-fake-tailscale.pid")"
  ssh_session "test \"\$(pgrep -fc '[h]elper/sidecard.*io.github.mtolhuys.sidecar')\" -eq 1 && test '$helper_uid' = \"\$(id -u)\" && test '$helper_uid' != 0 && ss -ltnp | grep -F '127.0.0.1:47991' && test \"\$(tr '\\0' '\\n' <'/proc/$route_pid/cmdline' | tail -n +2 | paste -sd ' ' -)\" = '/usr/local/bin/tailscale serve --yes --https=48719 http://127.0.0.1:47991' && test -z \"\$(find \"$plugin_dir\" -type f -perm /6000 -print -quit)\" && test -z \"\$(find \"$plugin_dir\" -type d -name __pycache__ -print -quit)\""
  ssh_session "\"$plugin_dir/helper/sidecarctl\" diagnostics | jq -e '.securityReceipt.healthy and .securityReceipt.nonRoot and .securityReceipt.loopbackOnly and .securityReceipt.existingTailnetOnly and .securityReceipt.tailscaleSsh == \"not used or changed\" and .securityReceipt.serveTarget == \"http://127.0.0.1:47991\" and (.securityReceipt.serveRoute | contains(\"https://sidecar-lab.example.ts.net:48719 -> http://127.0.0.1:47991\")) and (.securityReceipt.authorization | contains(\"credentials and scopes\"))'"
  ssh_session "test ! -S \"\$XDG_RUNTIME_DIR/omarchy-sidecar/provider.sock\" && test ! -d \"$plugin_dir/codex\""

  log "Creating two observable workspaces through QMP input"
  qmp_key_chord meta_l 1
  qmp_key_chord meta_l ret
  qmp_key_chord meta_l 2
  qmp_key_chord meta_l ret
  qmp_key_chord meta_l 1
  wait_for_guest_state "two real workspaces and windows exist" 12 ssh_session "test \"\$(hyprctl -j workspaces | jq length)\" -ge 2 && test \"\$(hyprctl -j clients | jq '[.[] | select(.mapped != false and .hidden != true)] | length')\" -ge 2" || return 1

  geometry="$(ssh_session "omarchy-shell shell debugBarGeometry | jq -r '.[] | select(.id == \"io.github.mtolhuys.sidecar\" and .visible) | [.x,.y,.width,.height] | @tsv' | head -n1")"
  read -r icon_x icon_y screen_width screen_height <<<"$geometry"
  icon_x=$((icon_x + screen_width / 2))
  icon_y=$((icon_y + screen_height / 2))
  read -r screen_width screen_height < <(ssh_session "hyprctl -j monitors | jq -r 'map(select(.focused))[0] // .[0] | [.width,.height] | @tsv'")
  qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  wait_for_guest_state "QMP pointer opens the Sidecar panel" 10 ssh_session "omarchy-shell sidecar identity | jq -e '.open == true'" || return 1

  ssh_session "rm -f /tmp/sidecar-client-pending.json /tmp/sidecar-client-result.json /tmp/sidecar-capability-pending.json /tmp/sidecar-lock-trigger /tmp/sidecar-lock-result.json /tmp/sidecar-revoke-result.json /tmp/sidecar-client.log; nohup python3 /tmp/sidecar-v1/tests/lab/pair_control.py \"$plugin_dir/helper/sidecarctl\" /tmp/sidecar-client-pending.json /tmp/sidecar-client-result.json /tmp/sidecar-capability-pending.json /tmp/sidecar-lock-trigger /tmp/sidecar-lock-result.json /tmp/sidecar-revoke-result.json >/tmp/sidecar-client.log 2>&1 &"
  wait_for_guest_state "in-memory client submits a pairing request" 12 ssh_session "jq -e '.requestId and (.verificationPhrase | length == 3)' /tmp/sidecar-client-pending.json" || return 1
  pending_json="$(ssh_session "cat /tmp/sidecar-client-pending.json")"
  request_id="$(jq -r .requestId <<<"$pending_json")"
  ssh_session "\"$plugin_dir/helper/sidecarctl\" status | jq -e --arg id '$request_id' --argjson phrase '$(jq -c .verificationPhrase <<<"$pending_json")' '.pending | any(.id == \$id and .verificationPhrase == \$phrase)'"
  capture_console "success-sidecar-v1013-01-pending"

  qmp_pointer_tap "$screen_width" "$screen_height" "$((icon_x - 240))" 230 left
  wait_for_guest_state "QMP pointer approves the exact phone" 12 ssh_session "\"$plugin_dir/helper/sidecarctl\" status | jq -e '.deviceCount == 1 and .pending == [] and .devices[0].name == \"<b>Lab Phone</b>\"'" || {
    capture_console "failure-sidecar-v1013-allow-coordinate"
    return 1
  }
  device_id="$(ssh_session "\"$plugin_dir/helper/sidecarctl\" status | jq -r '.devices[0].id'")"

  ssh_session "\"$plugin_dir/helper/sidecarctl\" device-rescope '$device_id' --scopes read:desktop,control:workspace,control:window-focus,control:media"
  wait_for_guest_state "the same phone requests exact missing abilities" 25 ssh_session "jq -e '.requestId and .status == \"pending\"' /tmp/sidecar-capability-pending.json && omarchy-shell sidecar identity | jq -e '.capabilities == 1'" || {
    ssh_session "cat /tmp/sidecar-client-result.json /tmp/sidecar-client.log 2>/dev/null; \"$plugin_dir/helper/sidecarctl\" status" || true
    return 1
  }
  capability_json="$(ssh_session "cat /tmp/sidecar-capability-pending.json")"
  capability_request_id="$(jq -r .requestId <<<"$capability_json")"
  ssh_session "\"$plugin_dir/helper/sidecarctl\" status | jq -e --arg id '$capability_request_id' '.capabilityRequests | any(.id == \$id and .scopes == [\"control:window-move\",\"control:theme\",\"control:lock\",\"write:inbox\"])'"
  capture_console "success-sidecar-v1013-02-capability-request"
  qmp_pointer_tap "$screen_width" "$screen_height" "$((icon_x - 240))" 205 left
  wait_for_guest_state "QMP pointer approves Drop and abilities on the same credential" 15 ssh_session "omarchy-shell sidecar identity | jq -e '.capabilities == 0' && \"$plugin_dir/helper/sidecarctl\" status | jq -e --arg id '$device_id' '.devices[0].id == \$id and ([\"control:window-move\",\"control:theme\",\"control:lock\",\"write:inbox\"] - .devices[0].scopes | length == 0)'" || {
    capture_console "failure-sidecar-v1013-capability-coordinate"
    return 1
  }

  wait_for_guest_state "Portal, Morph, and bounded Drop complete safely" 90 ssh_session "jq -e '.ok and .capabilityUpgraded and .workspaceChanged and .windowChanged and .focusOrderStable and .windowMoved and .moveDidNotFollow and .themePreviewDelivered and .themeChanged and .themeRestored and .resultsCompleted and .dropCommitted and .noOverwrite and .pauseMidUploadDenied and .rescopeMidUploadDenied' /tmp/sidecar-client-result.json" || {
    ssh_session "cat /tmp/sidecar-client-result.json /tmp/sidecar-client.log 2>/dev/null; hyprctl -j workspaces; hyprctl -j clients; omarchy-theme-current" || true
    return 1
  }
  ssh_session "result=\$(cat /tmp/sidecar-client-result.json); first=\$(jq -r .dropName <<<\"\$result\"); second=\$(jq -r .collisionName <<<\"\$result\"); expected=\$(jq -r .dropSha256 <<<\"\$result\"); inbox=\"\$HOME/Downloads/Sidecar\"; test -n \"\$first\" && test -n \"\$second\" && test \"\$first\" != \"\$second\" && test \"\$(sha256sum \"\$inbox/\$first\" | cut -d' ' -f1)\" = \"\$expected\" && test \"\$(sha256sum \"\$inbox/\$second\" | cut -d' ' -f1)\" = \"\$expected\" && test \"\$(stat -c '%a:%h:%U' \"\$inbox/\$first\")\" = \"600:1:\$(id -un)\" && test -z \"\$(find \"\$inbox/.sidecar-staging\" -mindepth 1 -print -quit)\""
  drop_name="$(ssh_session "jq -r .dropName /tmp/sidecar-client-result.json")"
  collision_name="$(ssh_session "jq -r .collisionName /tmp/sidecar-client-result.json")"
  drop_digest="$(ssh_session "jq -r .dropSha256 /tmp/sidecar-client-result.json")"
  if ! ssh_session "omarchy-shell sidecar identity | jq -e '.open == true'" >/dev/null 2>&1; then
    qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  fi
  wait_for_guest_state "desktop panel renders bounded Drop attention" 15 ssh_session "omarchy-shell sidecar identity | jq -e '.open == true' && \"$plugin_dir/helper/sidecarctl\" status | jq -e '.inbox.receivedCount == 2 and .inbox.lastKind == \"Text\" and .inbox.stagingCount == 0'" || return 1
  capture_console "success-sidecar-v1013-03-drop-attention"

  qmp_pointer_tap "$screen_width" "$screen_height" "$((icon_x + 25))" 123 left
  wait_for_guest_state "rendered Open inbox control reveals the local fixed directory" 15 ssh_session "hyprctl -j clients | jq -e 'any(.[]; ((.class // \"\") | test(\"nautilus\"; \"i\")) and ((.title // \"\") | contains(\"Sidecar\")))'" || {
    capture_console "failure-sidecar-v1013-open-inbox-coordinate"
    ssh_session "hyprctl -j clients | jq -c '.[] | {class, title}'; pgrep -af '[n]autilus|[x]dg-open' ; xdg-mime query default inode/directory; \"$plugin_dir/helper/sidecarctl\" status | jq -c '{state, inbox}'; journalctl --user --since '-2 minutes' --no-pager | grep -i -E 'sidecar|nautilus|xdg|inbox' | tail -n 40" || true
    return 1
  }
  capture_console "success-sidecar-v1013-04-local-reveal"

  ssh_session "touch /tmp/sidecar-lock-trigger"
  wait_for_guest_state "typed lock redacts state and stops the active upload" 25 ssh_session "jq -e '.ok and .lockedRedacted and .actionDenied and .lockMidUploadDenied' /tmp/sidecar-lock-result.json && \"$plugin_dir/helper/sidecarctl\" status | jq -e '.locked == true and .inbox.stagingCount == 0'" || return 1
  type_text "$GUEST_PASSWORD"
  press ret
  wait_for_guest_state "QMP password input unlocks the session" 15 ssh_session "! omarchy-hyprland-session-locked" || return 1
  wait_for_guest_state "revocation stops an active upload and clears staging" 20 ssh_session "jq -e '.ok and .revokeMidUploadDenied and .stagingClean' /tmp/sidecar-revoke-result.json && \"$plugin_dir/helper/sidecarctl\" status | jq -e '.deviceCount == 0 and .inbox.stagingCount == 0'" || return 1

  log "Re-pairing the exact same browser installation"
  if ! ssh_session "omarchy-shell sidecar identity | jq -e '.open == true'" >/dev/null 2>&1; then
    qmp_pointer_tap "$screen_width" "$screen_height" "$icon_x" "$icon_y" left
  fi
  wait_for_guest_state "Sidecar panel is visible for replacement approval" 10 ssh_session "omarchy-shell sidecar identity | jq -e '.open == true'" || return 1
  ssh_session "rm -f /tmp/sidecar-replace-pending.json /tmp/sidecar-replace-result.json /tmp/sidecar-replace.log; nohup python3 /tmp/sidecar-v1/tests/lab/pair_control.py --replace \"$plugin_dir/helper/sidecarctl\" /tmp/sidecar-replace-pending.json /tmp/sidecar-replace-result.json '$device_id' >/tmp/sidecar-replace.log 2>&1 &"
  wait_for_guest_state "same browser request reaches the rendered approval card" 15 ssh_session "jq -e '.requestId' /tmp/sidecar-replace-pending.json && \"$plugin_dir/helper/sidecarctl\" status | jq -e '.pending | length == 1' && omarchy-shell sidecar identity | jq -e '.pending == 1 and .open == true'" || return 1
  qmp_pointer_tap "$screen_width" "$screen_height" "$((icon_x - 240))" 287 left
  wait_for_guest_state "replacement leaves one new credential and removes the old pairing" 20 ssh_session "jq -e '.ok and .oldDeviceRemoved and .deviceCount == 1 and .newDeviceId != \"$device_id\"' /tmp/sidecar-replace-result.json && \"$plugin_dir/helper/sidecarctl\" status | jq -e --arg id \"$device_id\" '.deviceCount == 1 and all(.devices[]; .id != \$id)'" || {
    ssh_session "cat /tmp/sidecar-replace-result.json /tmp/sidecar-replace.log 2>/dev/null; \"$plugin_dir/helper/sidecarctl\" status" || true
    return 1
  }
  device_id="$(ssh_session "jq -r .newDeviceId /tmp/sidecar-replace-result.json")"
  capture_console "success-sidecar-v1013-05-clean-repair"

  helper_pid="$(ssh_session "pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1")"
  ssh_session "kill -9 '$helper_pid'"
  wait_for_guest_state "service restarts one crashed helper" 20 ssh_session "new=\$(pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1); test -n \"\$new\" && test \"\$new\" != '$helper_pid' && test \"\$(pgrep -fc '[h]elper/sidecard.*io.github.mtolhuys.sidecar')\" -eq 1 && omarchy-shell sidecar identity | jq -e '.state == \"ready\"'" || return 1
  replacement_pid="$(ssh_session "pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1")"

  log "Applying a committed same-path runtime edit to prove replacement and policy preservation"
  ssh_guest "sed -i 's/sidecar-service-v1013/sidecar-service-v1013-labupdate/' /tmp/sidecar-v1/service/v1013/Service.qml && sed -i 's/sidecar-widget-v1013/sidecar-widget-v1013-labupdate/' /tmp/sidecar-v1/bar-widget/v1013/BarWidget.qml && git -C /tmp/sidecar-v1 add service/v1013/Service.qml bar-widget/v1013/BarWidget.qml && git -C /tmp/sidecar-v1 -c user.name=SidecarLab -c user.email=lab@invalid commit -qm v1013-labupdate"
  ssh_session "omarchy-plugin-update io.github.mtolhuys.sidecar --yes"
  wait_for_guest_state "hot update replaces identities and preserves device policy" 35 ssh_session "omarchy-shell sidecar identity | jq -e '.service == \"sidecar-service-v1013-labupdate\" and .widget == \"sidecar-widget-v1013-labupdate\" and .helper == \"sidecard-v1013\" and .web == \"sidecar-web-v1013\" and .state == \"ready\"' && \"$plugin_dir/helper/sidecarctl\" status | jq -e --arg id '$device_id' '.deviceCount == 1 and .devices[0].id == \$id and ([\"control:window-move\",\"control:theme\",\"control:lock\"] - .devices[0].scopes | length == 0)' && curl -fsS http://127.0.0.1:47991/app/app.v1013.js | grep -F 'sidecar-web-v1013' >/dev/null && current=\$(pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' | head -n1); test \"\$current\" != '$replacement_pid'" || {
    ssh_session "omarchy-shell sidecar identity; \"$plugin_dir/helper/sidecarctl\" status; pgrep -af '[h]elper/sidecard.*io.github.mtolhuys.sidecar'" || true
    return 1
  }
  capture_console "success-sidecar-v1013-06-hot-update"

  ssh_session "\"$plugin_dir/helper/sidecarctl\" device-revoke '$device_id'"
  wait_for_guest_state "local revoke removes the exact credential" 12 ssh_session "\"$plugin_dir/helper/sidecarctl\" diagnostics | jq -e '.deviceCount == 0'" || return 1
  ssh_session "omarchy-plugin-disable io.github.mtolhuys.sidecar"
  wait_for_guest_state "disable removes helper, listener, and owned route" 25 ssh_session "! pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' >/dev/null && ! ss -ltn | grep -F ':47991' >/dev/null && ! test -e /tmp/sidecar-fake-tailscale.pid" || return 1
  ssh_session "omarchy-plugin-enable io.github.mtolhuys.sidecar"
  wait_for_guest_state "re-enable restores one helper and durable revocation" 30 ssh_session "omarchy-shell sidecar identity | jq -e '.state == \"ready\"' && \"$plugin_dir/helper/sidecarctl\" status | jq -e '.deviceCount == 0'" || return 1

  ssh_session "omarchy-plugin-disable io.github.mtolhuys.sidecar; touch /tmp/sidecar-fake-tailscale-conflict; omarchy-plugin-enable io.github.mtolhuys.sidecar"
  wait_for_guest_state "exact route conflict fails closed" 30 ssh_session "omarchy-shell sidecar identity | jq -e '.state == \"unavailable\"' && \"$plugin_dir/helper/sidecarctl\" status | jq -e '.lastError.code == \"tailscale-route-conflict\" and .route.childPid == null' && ! test -e /tmp/sidecar-fake-tailscale.pid" || return 1
  ssh_session "! grep -E '(^| )(up|login|ssh|funnel|down|logout|set|switch|configure|cert|reset|off)( |$)' /tmp/sidecar-fake-tailscale.log"
  ssh_session "rm -f /tmp/sidecar-fake-tailscale-conflict; omarchy-plugin-disable io.github.mtolhuys.sidecar; omarchy-plugin-enable io.github.mtolhuys.sidecar"
  wait_for_guest_state "explicit retry recovers the owned route" 30 ssh_session "omarchy-shell sidecar identity | jq -e '.state == \"ready\"'" || return 1

  ssh_session "omarchy-plugin-remove io.github.mtolhuys.sidecar --yes"
  wait_for_guest_state "remove cleans runtime but preserves completed user files" 35 ssh_session "test ! -e \"$plugin_dir\" && ! pgrep -f '[h]elper/sidecard.*io.github.mtolhuys.sidecar' >/dev/null && ! ss -ltn | grep -F ':47991' >/dev/null && ! test -e /tmp/sidecar-fake-tailscale.pid && omarchy-plugin-list --json | jq -e 'all(.[]; .id != \"io.github.mtolhuys.sidecar\")' && test \"\$(sha256sum \"\$HOME/Downloads/Sidecar/$drop_name\" | cut -d' ' -f1)\" = '$drop_digest' && test \"\$(sha256sum \"\$HOME/Downloads/Sidecar/$collision_name\" | cut -d' ' -f1)\" = '$drop_digest' && test -z \"\$(find \"\$HOME/Downloads/Sidecar/.sidecar-staging\" -mindepth 1 -print -quit)\"" || return 1
  ssh_session "test -z \"\$(hyprctl configerrors)\""
  ssh_session "nautilus -q >/dev/null 2>&1 || true"
  sleep 1

  tailscale_identity_after="$(ssh_session "tailscale status --json | jq -c '{BackendState,Self:{DNSName:.Self.DNSName}}'")"
  unrelated_serve_after="$(ssh_session "tailscale serve status --json | jq -c 'del(.TCP[\"48719\"])'")"
  [[ "$tailscale_identity_after" == "$tailscale_identity_before" ]] || return 1
  [[ "$unrelated_serve_after" == "$unrelated_serve_before" ]] || return 1
  ssh_guest "echo '$GUEST_PASSWORD' | sudo -S rm -f /usr/local/bin/tailscale"
  wait_for_guest_state "transient hostname lookup service returns to its baseline" 45 ssh_guest \
    "! systemctl is-active --quiet systemd-hostnamed.service" || return 1
  security_after="$(ssh_guest "echo '$GUEST_PASSWORD' | sudo -S -p '' bash /tmp/sidecar-v1/tests/lab/security-snapshot.sh")"
  if [[ "$security_after" != "$security_before" ]]; then
    log "Protected trust boundaries changed: before=$security_before after=$security_after"
    return 1
  fi
  capture_console "success-sidecar-v1013-07-removed"
  printf 'ok - v1013 Portal/Morph, scopes, lock, update, conflict, trust boundaries, and cleanup passed\n'
}
