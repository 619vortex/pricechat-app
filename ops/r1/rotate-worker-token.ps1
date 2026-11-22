<#
R1 (docs/hardening/CREDENTIALS.md): rotate the PC's Price Chat worker token (C1) without stopping production.

Run it yourself, at a terminal, AFTER creating the new worker in Admin > Brand Manager > Settings > AI > Workers (the new pcw_
token is shown once). The old token keeps working until you disable it in Admin, so nothing stops if this is interrupted.

  powershell -ExecutionPolicy Bypass -File ops\r1\rotate-worker-token.ps1              # preview: says what it would do, changes nothing
  powershell -ExecutionPolicy Bypass -File ops\r1\rotate-worker-token.ps1 -Apply       # do it
  powershell -ExecutionPolicy Bypass -File ops\r1\rotate-worker-token.ps1 -Apply -FromClipboard   # token read from the clipboard
  powershell -ExecutionPolicy Bypass -File ops\r1\rotate-worker-token.ps1 -Rollback    # put the newest backup back and restart

Entering the token (step 3). Pasting into a hidden Read-Host prompt is unreliable in the Windows PowerShell 5.1 console (Ctrl+V is
often ignored there, and copied text can carry invisible characters). The reliable way: copy the token in Admin, then run with
-FromClipboard. The token is read from the clipboard and never shown, written to the screen, a transcript or a log, and the
clipboard is cleared once it has been read. Without -FromClipboard the hidden prompt is still offered; pressing Enter on an empty
prompt also reads the clipboard. Either way the input may contain spaces, line breaks, quotes, a "PRICECHAT_WORKER_TOKEN=" prefix
or invisible characters: exactly one pcw_ token must be found in it, or nothing changes. Windows clipboard history (Win+V), if you
turned it on, keeps its own copy: delete that entry there after the rotation.

What -Apply does, in order (it stops at the first problem):
  1. Backs up C:\AI\price-chat\worker\worker.env to %USERPROFILE%\.pricechat-secrets\worker.env.<time> (readable by you only).
  2. Untracks worker/worker.env on main with `git rm --cached` (the file stays on disk; .gitignore already lists it) and commits
     only that removal. It refuses if anything else is already staged, so none of your other work is swept into the commit.
     Skipped if it is already untracked. (Fixed 2026-11-17: the earlier `git commit -- worker/worker.env` always failed.)
  3. Asks for the new token (hidden input, never printed or logged), checks its shape, and replaces only the
     PRICECHAT_WORKER_TOKEN line; every other setting in worker.env stays as it is.
  4. Restarts the three workers that read worker.env: the scheduled tasks "Price Chat News Desk GPU" and "Price Chat World Monitor",
     and the AI worker (start_worker.pyw). Jobs pause for about a minute; nothing is lost (jobs wait in the queue).
  5. Waits up to 3 minutes for proof: all 3 workers running AND the Price Chat server (worker.env PRICECHAT_SERVER) answering a
     worker request with 2xx in a log line written AFTER the restart. Only the server's own replies count: a 401/403 from it (or
     the worker's "refused this worker's token") means refused, and the backup is put back and the workers restarted, so the old
     token keeps working. 401s from local programs (the World Monitor desktop app's 127.0.0.1 API answers 401 for a few seconds
     after each restart) are reported and ignored. No reply and no refusal within 3 minutes: the new token stays, nothing is
     rolled back, and you check Admin (-Rollback is there if needed).
Then, in Admin: confirm the new worker shows online, and only then disable and delete the old worker entry. If Railway has
BM_WORKER_BOOTSTRAP_SHA256 set to the old token's hash, remove it.

Rollback (-Rollback, or -Rollback -Backup <file>): copies the newest backup (or the one named) over worker.env, restarts the
workers and checks them. This only works while the old worker is still enabled in Admin, which is why you disable it last.
The step-2 untrack commit is never undone: the file must stay out of git either way.
#>
param([switch]$Apply, [switch]$Rollback, [switch]$FromClipboard, [string]$Backup = "", [string]$Repo = "C:\AI\price-chat")
$ErrorActionPreference = "Stop"
$repo = $Repo
$env_file = Join-Path $repo "worker\worker.env"
$tasks = @("Price Chat News Desk GPU", "Price Chat World Monitor")
$aiWorker = Join-Path $repo "worker\start_worker.pyw"
$aiPython = Join-Path $repo "worker\.venv\Scripts\pythonw.exe"
$logs = @("worker.log", "newsdesk_images.log", "newsdesk_images.out.log", "worldmonitor.out.log") | ForEach-Object { Join-Path $repo "worker\$_" }
$scripts = @("start_worldmonitor.pyw", "start_newsdesk_images.pyw", "start_worker.pyw")
$secrets = Join-Path $env:USERPROFILE ".pricechat-secrets"

function Say($m) { Write-Host "  $m" }

# The one pcw_ token in whatever was pasted or copied: invisible characters (zero-width, BOM, non-breaking space, soft hyphen)
# are dropped first, then exactly one distinct token must be found. Returns "" when there is none or more than one; never prints it.
function Find-WorkerToken([string]$text) {
    if (-not $text) { return "" }
    $clean = $text -replace "[\u200B-\u200D\u2060\uFEFF\u00AD]", "" -replace "\u00A0", " "
    $found = @([regex]::Matches($clean, 'pcw_[A-Za-z0-9_\-]{20,}') | ForEach-Object { $_.Value } | Sort-Object -Unique)
    if ($found.Count -eq 1) { return $found[0] }
    return ""
}

# Why no token was found, without saying anything about the token itself.
function Explain-NoToken([string]$text, [string]$where) {
    if (-not $text) { return "the $where was empty" }
    $clean = $text -replace "[\u200B-\u200D\u2060\uFEFF\u00AD]", ""
    $n = @([regex]::Matches($clean, 'pcw_[A-Za-z0-9_\-]{20,}') | ForEach-Object { $_.Value } | Sort-Object -Unique).Count
    if ($n -gt 1) { return "the $where holds more than one different pcw_ token: copy only the new one" }
    if ($clean -match 'pcw_') { return "the $where has 'pcw_' but not a whole token after it (cut short, or broken by other characters): copy it again" }
    return "no pcw_ token in the $where ($($text.Length) characters): copy the token itself from Admin"
}

function Clear-ClipboardQuietly {
    try { Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::Clear() } catch { }
}

# The new token, never echoed: from the clipboard (-FromClipboard, or Enter on an empty prompt) or from the hidden prompt.
function Read-NewToken {
    $text = ""
    $where = "clipboard"
    if (-not $FromClipboard) {
        $secure = Read-Host "3. Paste the NEW pcw_ token (hidden), or press Enter to read it from the clipboard" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $text = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        $secure.Dispose()
        if ($text) { $where = "pasted text" }
    }
    if ($where -eq "clipboard") {
        try { $text = [string](Get-Clipboard -Raw -ErrorAction Stop) } catch { throw "the clipboard couldn't be read ($($_.Exception.GetType().Name)): worker.env unchanged" }
    }
    $token = Find-WorkerToken $text
    if (-not $token) { $why = Explain-NoToken $text $where; $text = $null; throw "${why}: worker.env unchanged" }
    $text = $null
    if ($where -eq "clipboard") { Clear-ClipboardQuietly; Say "3. token read from the clipboard ($($token.Length) characters, not shown); clipboard cleared" }
    else { Say "3. token read from the prompt ($($token.Length) characters, not shown)" }
    return $token
}

function Get-Workers {
    @(Get-CimInstance Win32_Process -Filter "name like 'pythonw%'" | Where-Object { $cl = $_.CommandLine; $scripts | Where-Object { $cl -like "*worker\$_*" } })
}

# How many of the three worker scripts are running (a venv's pythonw launches two processes per script).
function Count-Workers { @(Get-Workers | ForEach-Object { $cl = $_.CommandLine; $scripts | Where-Object { $cl -like "*worker\$_*" } } | Sort-Object -Unique).Count }

function Restart-Workers {
    foreach ($t in $tasks) { if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue } }
    Get-Workers | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 3
    foreach ($t in $tasks) { if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName $t } }
    if ((Test-Path $aiPython) -and (Test-Path $aiWorker)) { Start-Process -FilePath $aiPython -ArgumentList "`"$aiWorker`"" -WorkingDirectory (Split-Path $aiWorker) }
}

# Only what the logs gain after the restart counts: an old 401 line from before must not look like a refusal of the new token.
function Get-LogSizes { $s = @{}; foreach ($l in $logs) { $s[$l] = if (Test-Path $l) { (Get-Item $l).Length } else { 0 } }; $s }
function Get-NewLogText($sizes) {
    foreach ($l in $logs) {
        if (-not (Test-Path $l)) { continue }
        $start = [int64]$sizes[$l]
        if ((Get-Item $l).Length -lt $start) { $start = 0 }  # the log was rotated: all of it is new
        $fs = [IO.File]::Open($l, 'Open', 'Read', 'ReadWrite')
        try { [void]$fs.Seek($start, 'Begin'); (New-Object IO.StreamReader($fs)).ReadToEnd() -split "`n" } finally { $fs.Dispose() }
    }
}

# The Price Chat server the workers talk to (worker.env PRICECHAT_SERVER; no secret in it).
function Get-ServerHost {
    $line = Get-Content $env_file | Where-Object { $_ -match '^\s*PRICECHAT_SERVER\s*=' } | Select-Object -First 1
    $url = if ($line) { ($line -replace '^\s*PRICECHAT_SERVER\s*=\s*', '').Trim().Trim('"') } else { "" }
    try { if ($url) { return ([uri]$url).Host } } catch { }
    return "api.pricechat.live"
}

# "accepted": all 3 workers run and the Price Chat server answered at least one worker request with 2xx, nothing refused.
# "refused": the Price Chat server answered 401/403, or a worker logged that its token was refused.
# "unconfirmed": no refusal, but no accepted reply (or not all 3 workers) within 3 minutes.
# Production 2026-11-22: the first version counted ANY "401" in the logs, and the World Monitor desktop app's own local API
# (http://127.0.0.1:...) answers 401 for a few seconds after every restart. A working new token was rolled back because of it.
# Only replies from the server's own host count now; local ones are reported and ignored.
function Wait-Workers($sizes) {
    $server = Get-ServerHost
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 15
        $running = Count-Workers
        $new = @(Get-NewLogText $sizes)
        $fromServer = @($new | Where-Object { $_ -like "*$server*" })
        $refused = @($fromServer | Where-Object { $_ -match '"HTTP/1\.[01] 40[13]|\b40[13] (Unauthorized|Forbidden)\b' }) + @($new | Where-Object { $_ -match "refused this worker.s token" })
        $accepted = @($fromServer | Where-Object { $_ -match '"HTTP/1\.[01] 2\d\d' })
        $localNoise = @($new | Where-Object { $_ -match '127\.0\.0\.1|localhost' -and $_ -match '\b401\b' })
    } while ((Get-Date) -lt $deadline -and $refused.Count -eq 0 -and ($running -lt 3 -or $accepted.Count -eq 0))
    Say "worker scripts running: $running of 3; $server accepted $($accepted.Count) worker request(s), refused $($refused.Count)"
    if ($localNoise.Count) { Say "(ignored $($localNoise.Count) 401 line(s) from local programs such as the World Monitor desktop app: not the Price Chat server)" }
    if ($refused.Count) { return "refused" }
    if ($running -ge 3 -and $accepted.Count -gt 0) { return "accepted" }
    return "unconfirmed"
}

if (-not (Test-Path $env_file)) { throw "worker.env not found at $env_file" }

if ($Rollback) {
    $from = if ($Backup) { $Backup } else { Get-ChildItem $secrets -Filter "worker.env.*" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName }
    if (-not $from -or -not (Test-Path $from)) { throw "no backup found in ${secrets}: nothing to roll back to" }
    Write-Host "R1 ROLLBACK: restoring $from"
    Copy-Item $from $env_file -Force
    $sizes = Get-LogSizes
    Restart-Workers
    $result = Wait-Workers $sizes
    if ($result -eq "accepted") { Write-Host "Rolled back, and the server accepts the restored token." -ForegroundColor Green }
    elseif ($result -eq "refused") { Write-Host "The server refuses the restored token: that worker is probably disabled or deleted in Admin. Re-enable it, or create a new worker and run -Apply." -ForegroundColor Red; exit 1 }
    else { Write-Host "Rolled back, but no accepted server reply was seen within 3 minutes: check the workers are running and Admin shows the worker online." -ForegroundColor Yellow; exit 1 }
    return
}

$tracked = [bool](& git -C $repo ls-files -- worker/worker.env)
$branch = (& git -C $repo branch --show-current).Trim()
$staged = [bool](& git -C $repo diff --cached --name-only)
$hasLine = (Get-Content $env_file) -match '^\s*PRICECHAT_WORKER_TOKEN\s*='

Write-Host "R1 worker token rotation ($(if ($Apply) {'APPLY'} else {'preview, nothing changes'}))"
Say "worker.env: present; PRICECHAT_WORKER_TOKEN line: $(if ($hasLine) {'yes'} else {'NO'})"
Say "git: branch $branch, worker.env $(if ($tracked) {'TRACKED (will be untracked)'} else {'already untracked'}), other staged changes: $(if ($staged) {'YES (step 2 would stop)'} else {'none'})"
foreach ($t in $tasks) { $s = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue; Say "task '$t': $(if ($s) {$s.State} else {'not installed'})" }
Say "worker scripts running: $(Count-Workers) of 3"
Say "backups so far: $(@(Get-ChildItem $secrets -Filter 'worker.env.*' -ErrorAction SilentlyContinue).Count) in $secrets"
if (-not $Apply) { Write-Host "Preview only. Create the new worker in Admin first, then run again with -Apply."; return }
if (-not $hasLine) { throw "worker.env has no PRICECHAT_WORKER_TOKEN line: nothing to replace" }
if ($tracked -and $branch -ne "main") { throw "the main checkout is on '$branch', not main: switch it back, then run again" }
if ($tracked -and $staged) { throw "other changes are staged in ${repo}; commit or unstage them first so step 2 commits only the untrack" }

# 1. backup, readable by the current user only
New-Item -ItemType Directory -Force $secrets | Out-Null
& icacls $secrets /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
$backup = Join-Path $secrets ("worker.env." + (Get-Date -Format "yyyyMMdd-HHmmss"))
Copy-Item $env_file $backup
Say "1. backed up to $backup"

# 2. untrack on main (the file stays on disk); only the staged removal is committed
if ($tracked) {
    & git -C $repo rm --cached --quiet -- worker/worker.env
    & git -C $repo commit --quiet -m "Stop tracking worker/worker.env (CREDENTIALS.md R1, step 2)"
    if ($LASTEXITCODE -ne 0) { & git -C $repo reset --quiet -- worker/worker.env; throw "git commit failed: the index is put back and worker.env is unchanged; fix git and run again" }
    if (-not (Test-Path $env_file)) { Copy-Item $backup $env_file; throw "worker.env vanished after untracking: restored from the backup" }
    Say "2. untracked on main ($((& git -C $repo log --oneline -1).Trim())); the file is still on disk"
} else { Say "2. already untracked" }

# 3. the new token, hidden
$token = $null; $old = $null; $lines = $null
try {
    $token = Read-NewToken
    $old = (Get-Content $env_file | Where-Object { $_ -match '^\s*PRICECHAT_WORKER_TOKEN\s*=' }) -replace '^\s*PRICECHAT_WORKER_TOKEN\s*=\s*', ''
    if ((Find-WorkerToken $old) -eq $token) { throw "that is the current token: create a NEW worker in Admin first; worker.env unchanged" }
    $lines = Get-Content $env_file | ForEach-Object { if ($_ -match '^\s*PRICECHAT_WORKER_TOKEN\s*=') { "PRICECHAT_WORKER_TOKEN=$token" } else { $_ } }
    [IO.File]::WriteAllLines($env_file, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
    if ((Find-WorkerToken ((Get-Content $env_file | Where-Object { $_ -match '^\s*PRICECHAT_WORKER_TOKEN\s*=' }) -join "")) -ne $token) {
        Copy-Item $backup $env_file -Force
        throw "worker.env didn't read back with the new token: restored from the backup"
    }
} finally {
    $token = $null; $old = $null; $lines = $null
}
Say "3. worker.env updated (only the token line)"

# 4 and 5. restart, then check; refused means automatic rollback to the old token
$sizes = Get-LogSizes
Restart-Workers
Say "4. workers restarted"
$result = Wait-Workers $sizes
if ($result -eq "refused") {
    Write-Host "  The Price Chat server refused the new token. Rolling back to the old one ($backup)..." -ForegroundColor Red
    Copy-Item $backup $env_file -Force
    $sizes = Get-LogSizes
    Restart-Workers
    $again = Wait-Workers $sizes
    Write-Host "  Rolled back ($again). Check the new worker in Admin is enabled and its token was copied whole, then run -Apply again." -ForegroundColor Red
    exit 1
}
if ($result -eq "unconfirmed") {
    # no refusal and no reply: the old token wouldn't get a reply either, so the new one stays; nothing is deleted
    Write-Host "  No accepted reply from the server within 3 minutes, and no refusal. The new token stays in place." -ForegroundColor Yellow
    Write-Host "  Check Admin shows the new worker online before touching the old one. To go back: run this script with -Rollback." -ForegroundColor Yellow
    exit 1
}
Write-Host "Done: the server accepts the new token. In Admin: check the new worker is online, then disable and delete the old one. Backup: $backup" -ForegroundColor Green
Write-Host "If anything misbehaves before you disable the old worker: run this script with -Rollback." -ForegroundColor Green
