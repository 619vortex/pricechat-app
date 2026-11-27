# OPTIONAL, run by the owner only, at a terminal, after the token is set up (docs/intelligence/pc-worker.md).
# Registers the Windows Task Scheduler task that keeps the Terminal Intelligence Engine worker running on this PC.
# Runs as the signed-in user, at logon and every 15 minutes as a watchdog; a second copy exits at once (start_intel.pyw holds a
# 127.0.0.1 lock port), so the repeat only revives a worker that died. Re-run to update; -Remove deletes it.
param([switch]$Remove, [string]$Python = "D:\pc-gpu\venv\Scripts\pythonw.exe")
$name = "Price Chat Intel Worker"
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; return }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "start_intel.pyw"
if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
if (-not (Select-String -Path (Join-Path $here "worker.env") -Pattern "^\s*INTEL_WORKER_TOKEN=pci_" -Quiet)) { throw "Put INTEL_WORKER_TOKEN=pci_... in worker.env first" }
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`"" -WorkingDirectory $here
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $watch) -Settings $settings -Principal $principal `
    -Description "Price Chat: Terminal Intelligence Engine labels on this PC's Ollama (worker/pricechat_worker/intel.py), outbound only" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State
