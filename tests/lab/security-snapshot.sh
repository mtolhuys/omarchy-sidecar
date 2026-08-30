#!/bin/bash

# Read-only snapshot of host trust boundaries. The lifecycle harness invokes
# this inside the disposable guest with elevation solely so protected metadata
# can be compared before and after the product test.

set -euo pipefail

digest_output() {
  "$@" 2>/dev/null | LC_ALL=C sort | sha256sum | cut -d' ' -f1
}

sudoers=$({ find /etc -maxdepth 2 \( -path /etc/sudoers -o -path '/etc/sudoers.d/*' \) -type f -exec sha256sum {} + 2>/dev/null || true; } | LC_ALL=C sort | sha256sum | cut -d' ' -f1)
ssh_files=$({ for directory in /etc/ssh /root/.ssh /home/omarchy/.ssh; do test ! -e "$directory" || find "$directory" -xdev -type f -exec sha256sum {} +; done; } 2>/dev/null | LC_ALL=C sort | sha256sum | cut -d' ' -f1)
system_units=$({ find /etc/systemd /usr/lib/systemd -xdev -type f \( -name '*.service' -o -name '*.timer' \) -exec sha256sum {} + 2>/dev/null || true; } | LC_ALL=C sort | sha256sum | cut -d' ' -f1)
running_system_service_names=$({ systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null || true; } | awk '{print $1}' | LC_ALL=C sort)
running_system_services=$(sha256sum <<<"$running_system_service_names" | cut -d' ' -f1)
suid_files=$({ find /etc /usr /opt -xdev -type f -perm /6000 -printf '%m %u %g %p\n' 2>/dev/null || true; } | LC_ALL=C sort | sha256sum | cut -d' ' -f1)
firewall=$({ command -v nft >/dev/null && nft --stateless list ruleset 2>/dev/null || true; } | sha256sum | cut -d' ' -f1)
listeners=$(ss -Hltn | awk '{print $4}' | LC_ALL=C sort | sha256sum | cut -d' ' -f1)
accounts=$(sha256sum /etc/passwd /etc/group | LC_ALL=C sort | sha256sum | cut -d' ' -f1)

jq -cn \
  --arg sudoers "$sudoers" \
  --arg sshFiles "$ssh_files" \
  --arg systemUnits "$system_units" \
  --arg runningSystemServices "$running_system_services" \
  --arg runningSystemServiceNames "$running_system_service_names" \
  --arg suidFiles "$suid_files" \
  --arg firewall "$firewall" \
  --arg listeners "$listeners" \
  --arg accounts "$accounts" \
  '{sudoers:$sudoers,sshFiles:$sshFiles,systemUnits:$systemUnits,runningSystemServices:$runningSystemServices,runningSystemServiceNames:($runningSystemServiceNames | split("\n") | map(select(length > 0))),suidFiles:$suidFiles,firewall:$firewall,listeners:$listeners,accounts:$accounts}'
