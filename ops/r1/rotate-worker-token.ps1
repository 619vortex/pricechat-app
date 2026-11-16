<#
R1 (docs/hardening/CREDENTIALS.md): rotate the PC's Price Chat worker token (C1) without stopping production.

Run it yourself, at a terminal, AFTER creating the new worker in Admin > Brand Manager > Settings > AI > Workers (the new pcw_
token is shown once). The old token keeps working until you disable it in Admin (step 5), so nothing stops if this is interrupted.

  powershell -ExecutionPolicy Bypass -File ops\r1\rotate-worker-token.ps1            # preview: says what it would do, changes nothing
  powershell -ExecutionPolicy Bypass -File ops\r1\rotate-worker-token.ps1 -Apply     # do it

What -Apply does, in order (it stops at the first problem):
  1. Backs up C:\AI\price-chat\worker\worker.env to %USERPROFILE%\.pricechat-secrets\worker.env.<time> (readable by you only).
  2. Untracks worker/worker.env on main with `git rm --cached` (the file stays on disk; .gitignore already lists it) and commits
     only that path. Skipped if it is already untracked. This replaces merging chore/untrack-worker-env, which would delete the file.
  3. Asks for the new token (hidden input, never printed or logged), checks its shape, and replaces only the
     PRICECHAT_WORKER_TOKEN line; every other setting in worker.env stays as it is.
  4. Restarts the three workers that read worker.env: the scheduled tasks "Price Chat News Desk GPU" and "Price Chat World Monitor",
     and the AI worker (start_worker.pyw, Startup shortcut). Jobs pause for about a minute; nothing is lost (jobs wait in the queue).
  5. Waits up to 3 minutes and reports whether each worker came back, and whether any log shows a 401 (the token was refused).
Then, in Admin: confirm the new worker shows online, disable and delete the old worker entry. If Railway has
BM_WORKER_BOOTSTRAP_SHA256 set to the old token's hash, remove it.
Rollback: copy the backup from step 1 back over worker.env and run step 4 again (the old token works until you disable it).
#>
param([switch]$Apply)
$ErrorActionPreference = "Stop"
$repo = "C:\AI\price-chat"
$env_file = Join-Path $repo "worker\worker.env"
$tasks = @("Price Chat News Desk GPU", "Price Chat World Monitor")
$aiWorker = Join-Path $repo "worker\start_worker.pyw"
$aiPython = Join-Path $repo "worker\.venv\Scripts\pythonw.exe"
$logs = @("worker.log", "newsdesk_images.log", "newsdesk_images.out.log", "worldmonitor.out.log") | ForEach-Object { Join-Path $repo "worker\$_" }

function Say($m) { Write-Host "  $m" }
if (-not (Test-Path $env_file)) { throw "worker.env not found at $env_file" }
$tracked = (& git -C $repo ls-files -- worker/worker.env) -ne $null -and (& git -C $repo ls-files -- worker/worker.env) -ne ""
$branch = (& git -C $repo branch --show-current).Trim()
$hasLine = (Get-Content $env_file) -match '^\s*PRICECHAT_WORKER_TOKEN\s*='

Write-Host "R1 worker token rotation ($(if ($Apply) {'APPLY'} else {'preview, nothing changes'}))"
Say "worker.env: present; PRICECHAT_WORKER_TOKEN line: $(if ($hasLine) {'yes'} else {'NO'})"
Say "git: branch $branch, worker.env $(if ($tracked) {'TRACKED (will be untracked)'} else {'already untracked'})"
foreach ($t in $tasks) { $s = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue; Say "task '$t': $(if ($s) {$s.State} else {'not installed'})" }
$ai = @(Get-CimInstance Win32_Process -Filter "name like 'pythonw%'" | Where-Object { $_.CommandLine -like "*start_worker.pyw*" })
Say "AI worker processes: $($ai.Count)"
if (-not $Apply) { Write-Host "Preview only. Create the new worker in Admin first, then run again with -Apply."; return }
if (-not $hasLine) { throw "worker.env has no PRICECHAT_WORKER_TOKEN line: nothing to replace" }

# 1. backup, readable by the current user only
$dir = Join-Path $env:USERPROFILE ".pricechat-secrets"
New-Item -ItemType Directory -Force $dir | Out-Null
& icacls $dir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
$backup = Join-Path $dir ("worker.env." + (Get-Date -Format "yyyyMMdd-HHmmss"))
Copy-Item $env_file $backup
Say "1. backed up to $backup"

# 2. untrack on main (the file stays on disk)
if ($tracked) {
    if ($branch -ne "main") { throw "the main checkout is on '$branch', not main: switch it back, then run again" }
    & git -C $repo rm --cached --quiet -- worker/worker.env
    & git -C $repo commit --quiet -m "Stop tracking worker/worker.env (CREDENTIALS.md R1, step 2)" -- worker/worker.env
    if ($LASTEXITCODE -ne 0) { throw "git commit failed: worker.env is unchanged on disk; fix git and run again" }
    if (-not (Test-Path $env_file)) { Copy-Item $backup $env_file; throw "worker.env vanished after untracking: restored from the backup" }
    Say "2. untracked on main ($((& git -C $repo log --oneline -1).Trim())); the file is still on disk"
} else { Say "2. already untracked" }

# 3. the new token, hidden
$secure = Read-Host "3. Paste the NEW pcw_ token (hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
    if ($token -notmatch '^pcw_[A-Za-z0-9_\-]{20,}$') { throw "that doesn't look like a pcw_ worker token: worker.env unchanged" }
    $old = (Get-Content $env_file | Where-Object { $_ -match '^\s*PRICECHAT_WORKER_TOKEN\s*=' }) -replace '^\s*PRICECHAT_WORKER_TOKEN\s*=\s*', ''
    if ($old.Trim() -eq $token) { throw "that is the current token: create a NEW worker in Admin first" }
    $lines = Get-Content $env_file | ForEach-Object { if ($_ -match '^\s*PRICECHAT_WORKER_TOKEN\s*=') { "PRICECHAT_WORKER_TOKEN=$token" } else { $_ } }
    [IO.File]::WriteAllLines($env_file, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); $token = $null; $old = $null; $lines = $null
}
Say "3. worker.env updated (only the token line)"

# 4. restart the workers
$since = Get-Date
foreach ($t in $tasks) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    }
}
$script = @("start_worldmonitor.pyw", "start_newsdesk_images.pyw", "start_worker.pyw")
Get-CimInstance Win32_Process -Filter "name like 'pythonw%'" | Where-Object { $cl = $_.CommandLine; $script | Where-Object { $cl -like "*worker\$_*" } } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3
foreach ($t in $tasks) { if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName $t } }
if ((Test-Path $aiPython) -and (Test-Path $aiWorker)) { Start-Process -FilePath $aiPython -ArgumentList "`"$aiWorker`"" -WorkingDirectory (Split-Path $aiWorker) }
Say "4. workers restarted"

# 5. did they come back, and was the token accepted?
$deadline = (Get-Date).AddMinutes(3)
do {
    Start-Sleep -Seconds 15
    $running = @(Get-CimInstance Win32_Process -Filter "name like 'pythonw%'" | Where-Object { $cl = $_.CommandLine; $script | Where-Object { $cl -like "*worker\$_*" } })
    $refused = @($logs | Where-Object { (Test-Path $_) -and (Get-Item $_).LastWriteTime -ge $since } |
                 ForEach-Object { Get-Content $_ -Tail 200 } | Where-Object { $_ -match '\b401\b|Unauthorized|refused this worker.s token' })
} while ((Get-Date) -lt $deadline -and $running.Count -lt 3 -and $refused.Count -eq 0)
Say "5. worker processes running: $($running.Count) of 3"
if ($refused.Count) {
    Write-Host "  The server REFUSED the new token (401 in the worker logs). Restore: Copy-Item '$backup' '$env_file' and run this step again." -ForegroundColor Red
    exit 1
}
Write-Host "Done. In Admin: check the new worker is online, then disable and delete the old one. Backup: $backup" -ForegroundColor Green
