# TEST ONLY. Kiwi VPN 2.2 signed auto-update, end to end on a fresh GitHub Windows runner, with no VPN account:
#   install the "previous version" (2.1.99, built from this source with the update-test feature: it trusts only the TEST manifest
#   keys, the "test" channel, host kiwi-update.test and the throwaway certificate pinned at build time), serve test manifests from a
#   local HTTPS server, check every refusal, then update to this build's 2.2.0 (signed with the pinned throwaway certificate) and
#   check the service, the kept tunnel folder, the protected state and the kill switch afterwards.
# Nothing here touches pricechat.live or any real key. The runner is thrown away afterwards.
#   powershell -File run.ps1 -Build <public build artifact> -UpTest <update-test artifact> -Tools <test tools> [-Report file]
param([Parameter(Mandatory)] [string]$Build, [Parameter(Mandatory)] [string]$UpTest, [Parameter(Mandatory)] [string]$Tools, [string]$Report = "update-report.json")
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Rows = New-Object System.Collections.ArrayList
function Row([string]$Name, [bool]$Ok, [string]$Detail) {
    $r = if ($Ok) { "PASS" } else { "FAIL" }
    Write-Host "$r $Name : $Detail"
    [void]$Rows.Add([pscustomobject]@{ name = $Name; result = $r; detail = $Detail })
    $Rows | ConvertTo-Json -Depth 4 | Set-Content $Report -Encoding utf8
}
$Client = (Resolve-Path "$Tools\update_client.exe").Path
function Ask([string]$What) {
    $out = & $Client $What
    $line = @($out) | Where-Object { $_ -like "{*" } | Select-Object -Last 1
    if (-not $line) { return $null }
    try { return $line | ConvertFrom-Json } catch { return $null }
}
function Wait-Until([int]$Seconds, [scriptblock]$Test) {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
        $v = & $Test
        if ($v) { return $v }
        Start-Sleep 2
    }
    return $null
}
function Online { & $Client online; return $LASTEXITCODE -eq 0 }

$Site = "$env:RUNNER_TEMP\kiwi-site"
$Dir = "$Site\downloads\kiwi\windows"
New-Item -ItemType Directory -Force $Dir | Out-Null

# --- 1. this runner trusts the throwaway certificates and serves https://kiwi-update.test ---------------------------------
foreach ($c in "pinned.cer", "other.cer") { Import-Certificate -FilePath "$UpTest\$c" -CertStoreLocation Cert:\LocalMachine\Root | Out-Null }
$srv = New-SelfSignedCertificate -DnsName "kiwi-update.test" -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddDays(2) -KeyExportPolicy Exportable -Subject "CN=kiwi-update.test (Kiwi CI, TEST ONLY)"
$pw = [guid]::NewGuid().ToString()
Export-PfxCertificate -Cert $srv -FilePath "$env:RUNNER_TEMP\server.pfx" -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -CryptoAlgorithmOption AES256_SHA256 | Out-Null
Export-Certificate -Cert $srv -FilePath "$env:RUNNER_TEMP\server.cer" | Out-Null
Import-Certificate -FilePath "$env:RUNNER_TEMP\server.cer" -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Add-Content "$env:SystemRoot\System32\drivers\etc\hosts" "`r`n127.0.0.1 kiwi-update.test`r`n"
Set-Content "$Dir\ping.txt" "ok"
$server = Start-Process node -ArgumentList "`"$Here\server.mjs`"", "`"$Site`"", "`"$env:RUNNER_TEMP\server.pfx`"", $pw -PassThru -RedirectStandardOutput "$env:RUNNER_TEMP\server.out" -RedirectStandardError "$env:RUNNER_TEMP\server.err"
Start-Sleep 3
$served = $false
try { $served = (Invoke-WebRequest -UseBasicParsing "https://kiwi-update.test/downloads/kiwi/windows/ping.txt").StatusCode -eq 200 } catch { Write-Host $_ }
Row "setup: the test HTTPS server answers as kiwi-update.test, with a certificate this runner trusts" $served "server process $($server.Id)"
if (-not $served) { Get-Content "$env:RUNNER_TEMP\server.err" -ErrorAction SilentlyContinue; exit 1 }

# --- 2. the previous version ------------------------------------------------------------------------------------------------
$old = Get-Item "$UpTest\KiwiVPN-2.1.99-updatetest-Setup.exe"
$p = Start-Process $old.FullName -ArgumentList "/S" -Wait -PassThru
Start-Sleep 5
$st = Wait-Until 30 { $s = Ask status; if ($s -and $s.version) { $s } }
Row "setup: the previous version (2.1.99, update-test build) is installed and its service answers" ($p.ExitCode -eq 0 -and $st.version -eq "2.1.99" -and ($st.features -contains "update")) "exit $($p.ExitCode), service version $($st.version), features $($st.features -join ',')"

# from here on a failed check is a FAIL row, not the end of the run
$ErrorActionPreference = "Continue"

# --- 3. manifests the service must refuse --------------------------------------------------------------------------------------
$M = "$Here\manifest.mjs"
$script:Seq = 10
function Publish([string[]]$Opts, [string]$Key = "t1", [string]$Claim = "") {
    $script:Seq++
    $all = @($Opts) + @("--seq", "$script:Seq")
    & node $M payload "$env:RUNNER_TEMP\payload.json" @all
    if ($LASTEXITCODE -ne 0) { throw "manifest.mjs payload failed" }
    $claimed = if ($Claim) { $Claim } else { $Key }
    & node $M sign "$env:RUNNER_TEMP\payload.json" "$Dir\test.json" $Key update $claimed
    if ($LASTEXITCODE -ne 0) { throw "manifest.mjs sign failed" }
}
function Serve([string]$Source, [string]$Name) {
    Copy-Item $Source "$Dir\$Name" -Force
    $f = Get-Item "$Dir\$Name"
    return @{ url = "https://kiwi-update.test/downloads/kiwi/windows/$Name"; size = $f.Length; sha = (Get-FileHash $f.FullName -Algorithm SHA256).Hash }
}
function Opts($File, [string]$Version = "2.2.0", [string[]]$Extra = @()) { @("--version", $Version, "--url", $File.url, "--size", "$($File.size)", "--sha256", $File.sha) + $Extra }

$good = Serve "$UpTest\KiwiVPN-2.2.0-pinned-Setup.exe" "KiwiVPN-2.2.0-Setup.exe"
$unsigned = Serve "$UpTest\KiwiVPN-2.2.0-unsigned-Setup.exe" "KiwiVPN-2.2.0-u-Setup.exe"
$other = Serve "$UpTest\KiwiVPN-2.2.0-other-Setup.exe" "KiwiVPN-2.2.0-o-Setup.exe"
$pinnedSig = Get-AuthenticodeSignature "$Dir\KiwiVPN-2.2.0-Setup.exe"
Row "setup: the update is signed by the pinned throwaway certificate, with a timestamp" ($pinnedSig.Status -eq "Valid" -and [bool]$pinnedSig.TimeStamperCertificate) "$($pinnedSig.Status), $($pinnedSig.SignerCertificate.Thumbprint), timestamp $([bool]$pinnedSig.TimeStamperCertificate)"

function Refused([string]$Name, [string]$Code) {
    $s = Ask check
    $u = $s.update
    $ok = $u -and $u.state -notin @("available", "required") -and "$($u.error)" -like "*$Code*"
    Row "refused: $Name" $ok "state $($u.state), says: $($u.error)"
}
function Nothing([string]$Name) {
    $s = Ask check
    $u = $s.update
    Row "nothing offered: $Name" ($u -and $u.state -eq "none") "state $($u.state), says: $($u.error)"
}

Publish (Opts $good) "t2" "t1"; Refused "a manifest signed by another key than it claims" "bad-signature"
Publish (Opts $good) "t1" "k9"; Refused "a manifest signed by an unknown key" "unknown-key"
Publish (Opts $good -Extra @("--expired")); Refused "an expired manifest" "expired"
Publish (Opts $good -Extra @("--future")); Refused "a manifest signed in the future" "signed-in-future"
Publish (Opts $good -Extra @("--channel", "stable")); Refused "a manifest for another channel" "channel"
Publish (Opts $good -Extra @("--product", "kiwivpn-android")); Refused "a manifest for another product" "product"
Publish (Opts @{ url = "https://evil.example/downloads/kiwi/windows/KiwiVPN-2.2.0-Setup.exe"; size = $good.size; sha = $good.sha }); Refused "an installer on another host" "url"
Publish (Opts @{ url = "http://kiwi-update.test/downloads/kiwi/windows/KiwiVPN-2.2.0-Setup.exe"; size = $good.size; sha = $good.sha }); Refused "an installer over plain HTTP" "url"
Publish (Opts $good -Version "2.1.98"); Nothing "a lower version (downgrade)"
Publish (Opts $good -Version "2.1.99"); Nothing "the same version"
Publish (Opts $good -Extra @("--paused")); Nothing "a paused release"
Publish (Opts $good -Extra @("--rollout", "0")); Nothing "a release at 0% rollout"
$script:Seq = 100; Publish (Opts $good -Version "2.1.98")
$null = Ask check
$script:Seq = 50; Publish (Opts $good); Refused "an older manifest replayed after a newer one" "old-seq"
$script:Seq = 200
Set-Content "$Dir\test.json.redirect" "https://kiwi-update.test/downloads/kiwi/windows/other.json"
$s = Ask check
Row "refused: a redirect is never followed" ("$($s.update.error)" -like "*Couldn't check*" -and $s.update.state -notin @("available", "required")) "state $($s.update.state), says: $($s.update.error)"
Remove-Item "$Dir\test.json.redirect"

# --- 4. installers the service must refuse (the manifest is valid; the file isn't) ---------------------------------------------
function Install-Refused([string]$Name, $File, [string]$Code, [string[]]$Extra = @(), [string]$Size = "") {
    $o = Opts $File -Extra $Extra
    if ($Size) { $o[5] = $Size }
    Publish $o
    $c = Ask check
    if ($c.update.state -ne "available") { Row "refused: $Name" $false "the manifest wasn't accepted: $($c.update.state) $($c.update.error)"; return }
    $null = Ask install
    $f = Wait-Until 180 { $s = Ask status; if ($s -and $s.update.state -eq "failed") { $s } }
    $after = Ask status
    $ok = $f -and "$($f.update.error)" -like "*$Code*" -and $after.version -eq "2.1.99" -and -not (Get-ItemProperty HKLM:\SOFTWARE\KiwiVPN\Update -Name Pending -ErrorAction SilentlyContinue)
    Row "refused: $Name" $ok "says: $($f.update.error); still running $($after.version)"
}
Install-Refused "an installer whose SHA-256 isn't the manifest's" @{ url = $good.url; size = $good.size; sha = $unsigned.sha } "hash-mismatch"
Install-Refused "a truncated download (shorter than the manifest says)" $good "download-truncated" -Size "$($good.size + 4096)"
Install-Refused "a download longer than the manifest says" $good "larger than allowed" -Size "$($good.size - 4096)"
Install-Refused "an unsigned installer (no thumbprint at all)" $unsigned "not-signed"
Install-Refused "an installer signed by a trusted but not pinned certificate" $other "wrong-signer"
$left = @(Get-ChildItem "C:\Program Files\KiwiVPN\updates" -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne "KiwiVPN-2.2.0-Setup.exe" })
Row "refused installers are deleted, nothing half-downloaded is left" ($left.Count -eq 0) "left: $($left.Name -join ', ')"
$acl = (Get-Acl "C:\Program Files\KiwiVPN\updates")
$users = @($acl.Access | Where-Object { $_.IdentityReference -match "Users|Everyone|Authenticated" })
Row "the updates folder is SYSTEM and Administrators only" ($users.Count -eq 0 -and $acl.Owner -match "SYSTEM|Administrators") "owner $($acl.Owner); $(@($acl.Access | ForEach-Object { "$($_.IdentityReference):$($_.FileSystemRights)" }) -join '; ')"

# --- 5. the real update ------------------------------------------------------------------------------------------------------------
New-Item -ItemType Directory -Force "C:\Program Files\KiwiVPN\tunnels" | Out-Null
Set-Content "C:\Program Files\KiwiVPN\tunnels\ci-marker.txt" "kept"
Publish (Opts $good)
$c = Ask check
Row "offered: the signed 2.2.0" ($c.update.state -eq "available" -and $c.update.version -eq "2.2.0") "state $($c.update.state), version $($c.update.version)"
$t0 = Get-Date
$null = Ask install
$done = Wait-Until 420 { $s = Ask status; if ($s -and $s.version -eq "2.2.0") { $s } }
$secs = [int]((Get-Date) - $t0).TotalSeconds
Row "updated: the service runs 2.2.0" ([bool]$done) "after $secs s"
Start-Sleep 5
$svc = Get-Service KiwiVPN -ErrorAction SilentlyContinue
$exe = (Get-Item "C:\Program Files\KiwiVPN\KiwiVPN.exe" -ErrorAction SilentlyContinue).VersionInfo.ProductVersion
Row "updated: service running, KiwiVPN.exe is the new version" ($svc.Status -eq "Running" -and "$exe" -like "2.2.0*") "service $($svc.Status), KiwiVPN.exe $exe"
$reg = Get-ItemProperty HKLM:\SOFTWARE\KiwiVPN\Update -ErrorAction SilentlyContinue
Row "updated: committed in the protected state, nothing pending" ($reg.HighestCommitted -eq "2.2.0" -and -not $reg.Pending -and [int]$reg.ManifestSeq -ge 200) "HighestCommitted $($reg.HighestCommitted), ManifestSeq $($reg.ManifestSeq), Pending '$($reg.Pending)'"
$tasks = @(@("KiwiVPN Update Recovery", "KiwiVPN Update Recovery at boot") | Where-Object { cmd /c "schtasks.exe /Query /TN `"$_`" >nul 2>&1"; $LASTEXITCODE -eq 0 })
Row "updated: the recovery tasks are gone" ($tasks.Count -eq 0) "left: $($tasks -join ', ')"
Row "updated: the verified installer is kept as the repair copy" (Test-Path "C:\Program Files\KiwiVPN\updates\KiwiVPN-2.2.0-Setup.exe") ""
Row "updated: the tunnels folder was kept through the upgrade" (Test-Path "C:\Program Files\KiwiVPN\tunnels\ci-marker.txt") ""
Remove-Item "C:\Program Files\KiwiVPN\tunnels\ci-marker.txt" -ErrorAction SilentlyContinue
$entries = @(Get-ChildItem HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall | Get-ItemProperty | Where-Object { $_.DisplayName -like "Kiwi*" })
Row "updated: one Kiwi VPN entry in Apps, at 2.2.0" ($entries.Count -eq 1 -and $entries[0].DisplayVersion -eq "2.2.0") "$(($entries | ForEach-Object { "$($_.DisplayName) $($_.DisplayVersion)" }) -join '; ')"

# --- 6. the kill switch after the update -----------------------------------------------------------------------------------------
$k = Ask connect-nowhere
Start-Sleep 3
$leak = Online
Row "after the update: the kill switch blocks" ($k.kill_switch -and -not $leak) "blocking $($k.kill_switch), reached the internet: $leak"
$null = Ask disconnect
$back = Wait-Until 15 { Online }
Row "after the update: Disconnect gives the internet back" ([bool]$back) ""

# --- 7. the public build itself refuses every manifest until the owner's keys are in ------------------------------------------
$s = Ask check
Row "the public 2.2.0 build has no manifest keys yet and offers nothing" ($s.update.state -eq "unknown" -and "$($s.update.error)" -like "*aren't set up*") "state $($s.update.state), says: $($s.update.error)"

Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
Get-Content "$Site\requests.log" -ErrorAction SilentlyContinue | Select-Object -Last 40
$failed = @($Rows | Where-Object { $_.result -eq "FAIL" }).Count
Write-Host "`n$($Rows.Count) checks, $failed failed"
if ($failed) { exit 1 }
