param([switch]$DisableLegacyFirewallRule)
$ErrorActionPreference = 'Stop'
# Loopback entry needs neither a hosts edit nor an inbound LAN port-80 rule.
if ($DisableLegacyFirewallRule) {
    # Optional migration, run as Administrator. Only touch the old named Orion rule.
    $rule = Get-NetFirewallRule -Name 'Orion-Hub-HTTP-Shortcuts' -ErrorAction SilentlyContinue
    if ($rule) { $rule | Disable-NetFirewallRule | Out-Null }
    Write-Output 'Disabled the legacy Orion Hub LAN port-80 firewall rule.'
}
Write-Output 'Local entry: http://localhost/ ; chat: /hub ; owner panel: /panel'
Write-Output 'Local management: http://127.0.0.1:8910/manage ; Router shortcut: /orionrouter'
Write-Output 'LAN web uses HTTPS 8911; mobile uses pinned HTTPS 8443 and mDNS (UDP 5353).'
