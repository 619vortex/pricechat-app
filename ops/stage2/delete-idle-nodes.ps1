<#
Stage 2: delete the two unused Hetzner servers kiwi-sin1 and kiwi-hil1 and their firewalls.

Default = PREVIEW: GET requests only, nothing changes. -Execute changes things, and only after you type the exact phrase
it prints. Run it yourself in PowerShell (owner). The token is read from the Windows user environment and never printed.

  powershell -File ops\stage2\delete-idle-nodes.ps1            # preview
  powershell -File ops\stage2\delete-idle-nodes.ps1 -Execute   # asks for the phrase, then deletes

Safety checks (all must pass, in preview and before deleting):
  - each server id still has the expected name and IPv4 (nothing renamed or reassigned)
  - the IPv4 is not used by any server in the public Kiwi list (https://api.pricechat.live/api/members/vpn/stats)
  - no DNS name under vpn.pricechat.live points at it (sin1/hil1 names checked)
  - no volume attached, no floating IP assigned, no snapshot or backup image made from it
  - outbound traffic this billing period under 100 MB (a node serving people would be far above)
Order when executing: turn off delete+rebuild protection, delete the server (Hetzner detaches its firewall and deletes its
primary IPs, which have auto_delete on), wait for the action, then delete the now-unused firewall.
Not reversible. Stops about $30.68/month.
#>
param([switch]$Execute)
$ErrorActionPreference = 'Stop'

$Targets = @(
  @{ Id = 175465282; Name = 'kiwi-sin1'; Ipv4 = '5.223.43.13'; Firewall = 12016756; FirewallName = 'kiwi-sin1'; Dns = 'sin1.vpn.pricechat.live' },
  @{ Id = 175465419; Name = 'kiwi-hil1'; Ipv4 = '5.78.218.8';  Firewall = 12016766; FirewallName = 'kiwi-hil1'; Dns = 'hil1.vpn.pricechat.live' }
)
$Api = 'https://api.hetzner.cloud/v1'
$Token = [Environment]::GetEnvironmentVariable('HCLOUD_TOKEN', 'User')
if (-not $Token -or $Token.Length -lt 32) { throw 'HCLOUD_TOKEN (Windows user environment) is missing or a placeholder.' }
$H = @{ Authorization = "Bearer $Token" }
function Get-H([string]$Path) { Invoke-RestMethod -Headers $H -Uri "$Api$Path" -Method Get }

$problems = @()
$stats = Invoke-RestMethod -Uri 'https://api.pricechat.live/api/members/vpn/stats' -TimeoutSec 20
$images = (Get-H '/images?type=snapshot&type=backup&per_page=50').images
$fips = (Get-H '/floating_ips').floating_ips

foreach ($t in $Targets) {
  $s = (Get-H "/servers/$($t.Id)").server
  $out = [math]::Round($s.outgoing_traffic / 1MB, 1)
  "{0} (id {1}): status {2}, type {3}, {4}, IPv4 {5}, delete protection {6}, out this period {7} MB" -f $s.name, $s.id, $s.status, $s.server_type.name, $s.location.name, $s.public_net.ipv4.ip, $s.protection.delete, $out
  if ($s.name -ne $t.Name) { $problems += "$($t.Id): name is '$($s.name)', expected $($t.Name)" }
  if ($s.public_net.ipv4.ip -ne $t.Ipv4) { $problems += "$($t.Name): IPv4 changed" }
  if ($s.volumes.Count -gt 0) { $problems += "$($t.Name): has volumes" }
  if ($fips | Where-Object { $_.server -eq $t.Id }) { $problems += "$($t.Name): a floating IP is assigned" }
  if ($images | Where-Object { $_.created_from.id -eq $t.Id }) { $problems += "$($t.Name): a snapshot/backup image exists" }
  if ($out -gt 100) { $problems += "$($t.Name): $out MB outbound this period (expected near zero)" }
  $resolved = try { (Resolve-DnsName -Name $t.Dns -Server 1.1.1.1 -Type A -ErrorAction Stop | Where-Object Type -eq 'A').IPAddress } catch { @() }
  if ($resolved) { $problems += "$($t.Name): DNS $($t.Dns) resolves to $($resolved -join ',')" }
  $fw = (Get-H "/firewalls/$($t.Firewall)").firewall
  $applied = @($fw.applied_to | ForEach-Object { $_.server.id })
  "   firewall {0} '{1}' applied to server ids: {2}" -f $fw.id, $fw.name, ($applied -join ', ')
  if ($fw.name -ne $t.FirewallName) { $problems += "firewall $($t.Firewall): name is '$($fw.name)'" }
  if ($applied | Where-Object { $_ -ne $t.Id }) { $problems += "firewall $($fw.name) is also applied to another server" }
}
"public Kiwi servers: " + (($stats.servers | ForEach-Object { $_.name }) -join ', ')
foreach ($srv in $stats.servers) { if ($srv.name -match '\b(sin1|hil1|singapore|hillsboro|oregon|us west)\b') { $problems += "public list has '$($srv.name)'" } }

if ($problems.Count) { "`nSTOP, checks failed:"; $problems | ForEach-Object { "  - $_" }; exit 1 }
"`nAll checks passed."
if (-not $Execute) { "PREVIEW only: nothing was changed. Re-run with -Execute to delete."; exit 0 }

$phrase = 'delete kiwi-sin1 175465282 and kiwi-hil1 175465419'
if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) { throw 'Refusing: -Execute needs you at a terminal.' }
$typed = Read-Host "Type exactly: $phrase"
if ($typed -ne $phrase) { 'Phrase did not match: nothing was changed.'; exit 1 }

foreach ($t in $Targets) {
  Invoke-RestMethod -Headers $H -Method Post -Uri "$Api/servers/$($t.Id)/actions/change_protection" -ContentType 'application/json' -Body '{"delete":false,"rebuild":false}' | Out-Null
  $a = (Invoke-RestMethod -Headers $H -Method Delete -Uri "$Api/servers/$($t.Id)").action
  for ($i = 0; $i -lt 60 -and $a.status -eq 'running'; $i++) { Start-Sleep 2; $a = (Get-H "/actions/$($a.id)").action }
  "{0}: delete action {1}" -f $t.Name, $a.status
  if ($a.status -ne 'success') { throw "$($t.Name): delete did not succeed; firewall left in place" }
  for ($i = 0; $i -lt 30; $i++) {
    $fw = (Get-H "/firewalls/$($t.Firewall)").firewall
    if (-not $fw.applied_to.Count) { break }; Start-Sleep 2
  }
  Invoke-RestMethod -Headers $H -Method Delete -Uri "$Api/firewalls/$($t.Firewall)" | Out-Null
  "{0}: firewall {1} deleted" -f $t.Name, $t.Firewall
}
"`nDone. Check: GET /servers lists hel1, fsn1, ash1 only; Helsinki/Germany/Ashburn still online in /stats."
