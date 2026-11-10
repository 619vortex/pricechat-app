# Registers the Windows Task Scheduler task that keeps the News Desk GPU picture worker running 24/7 on this PC.
# Runs as the signed-in user (the GPU and the model files are theirs), at logon and every 15 minutes as a watchdog; a second copy exits
# at once (start_newsdesk_images.pyw holds a local port), so the repeat only revives a worker that died. Re-run to update; -Remove deletes it.
param([switch]$Remove, [string]$Python = "D:\pc-gpu\venv\Scripts\pythonw.exe")
$name = "Price Chat News Desk GPU"
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; return }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "start_newsdesk_images.pyw"
if (-not (Test-Path $Python)) { throw "GPU Python not found: $Python" }
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`"" -WorkingDirectory $here
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $watch) -Settings $settings -Principal $principal `
    -Description "Price Chat: paints News Desk picture backgrounds on this PC's GPU (worker/pricechat_worker/newsdesk_images.py)" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State
