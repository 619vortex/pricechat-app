# Registers the Windows Task Scheduler task that keeps the World Monitor bridge running 24/7 on this PC: it reads the World Monitor
# desktop app's local feeds and sends them to Admin > World Monitor > Desktop app (worker/pricechat_worker/worldmonitor.py).
# Runs as the signed-in user, at logon and every 15 minutes as a watchdog; a second copy exits at once (start_worldmonitor.pyw holds a
# local port), so the repeat only revives a bridge that died. Re-run to update; -Remove deletes it.
param([switch]$Remove, [string]$Python = "D:\pc-gpu\venv\Scripts\pythonw.exe")
$name = "Price Chat World Monitor"
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; return }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "start_worldmonitor.pyw"
if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`"" -WorkingDirectory $here
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $watch) -Settings $settings -Principal $principal `
    -Description "Price Chat: sends the World Monitor desktop app's feeds to Admin > World Monitor (worker/pricechat_worker/worldmonitor.py)" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State
