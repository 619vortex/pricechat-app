# Puts "Price Chat" (start) and "Stop Price Chat" icons on the desktop. Safe to run again: it just refreshes them.
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
foreach ($s in @(
    @{ Name = "Price Chat"; Script = "start-price-chat.ps1"; Tip = "Start Price Chat and open the dashboard" },
    @{ Name = "Stop Price Chat"; Script = "stop-price-chat.ps1"; Tip = "Stop the Price Chat backend and dashboard" }
)) {
    $lnk = $shell.CreateShortcut((Join-Path $desktop "$($s.Name).lnk"))
    $lnk.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $root "scripts\$($s.Script)")`""
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = (Join-Path $root "scripts\price-chat.ico") + ",0"
    $lnk.Description = $s.Tip
    $lnk.Save()
    Write-Host "  Created: $(Join-Path $desktop "$($s.Name).lnk")"
}
