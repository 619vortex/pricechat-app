# Lets Claude reach the Kiwi VPN test VM through PowerShell Direct (no network, no file shares).
# Run it in a NORMAL (not administrator) Windows PowerShell window on the main PC, after adding yourself to
# "Hyper-V Administrators" and signing out and back in:
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI\price-chat\windows-client\vm-access-setup.ps1"
#
# It asks for the VM's administrator account in a Windows prompt and saves it encrypted with DPAPI for your Windows
# user only, at %APPDATA%\KiwiVPN\vm-credential.xml. No password is printed. Delete that file to take access away.
# Changes nothing on the main PC's security settings (Smart App Control, firewall, Defender).
$ErrorActionPreference = "Stop"

try {
    $vms = @(Get-VM)
} catch {
    throw "This window can't see Hyper-V yet: add your user to 'Hyper-V Administrators', sign out and back in, then run this again."
}
if ($vms.Count -eq 0) { throw "No VM found on this PC." }

Write-Host "VMs on this PC:"
foreach ($v in $vms) { Write-Host ("  {0}  ({1})" -f $v.Name, $v.State) }

if ($vms.Count -eq 1) {
    $vmName = $vms[0].Name
} else {
    $vmName = Read-Host "Name of the Kiwi VPN test VM"
}
$vm = Get-VM -Name $vmName

if ($vm.State -ne "Running") {
    Start-VM -Name $vmName
    Write-Host "Started $vmName. Waiting 60 seconds for Windows to come up..."
    Start-Sleep -Seconds 60
}

$guest = Get-VMIntegrationService -VMName $vmName -Name "Guest Service Interface"
if (-not $guest.Enabled) {
    Enable-VMIntegrationService -VMName $vmName -Name "Guest Service Interface"
    Write-Host "Turned on file copy into the VM (Guest Service Interface)."
}

$vmCred = Get-Credential -Message "The administrator account INSIDE the VM (the one signed in to its desktop)"
if ($null -eq $vmCred) { throw "No account entered. Nothing was saved." }

# Ask the VM who we are and whether that account is an administrator there.
$check = {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal -ArgumentList $identity
    $adminRole = [System.Security.Principal.WindowsBuiltInRole]::Administrator
    [pscustomobject]@{
        Computer = $env:COMPUTERNAME
        IsAdmin  = $principal.IsInRole($adminRole)
    }
}
$answer = Invoke-Command -VMName $vmName -Credential $vmCred -ScriptBlock $check

if (-not $answer.IsAdmin) { throw "That account signs in, but it isn't an administrator in the VM. Add it to Administrators there, then run this again." }

$dir = Join-Path $env:APPDATA "KiwiVPN"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$saved = Join-Path $dir "vm-credential.xml"
[pscustomobject]@{ VMName = $vmName; Credential = $vmCred } | Export-Clixml -Path $saved

Write-Host ("OK: '{0}' (Windows name {1}) is reachable as an administrator. Access saved, encrypted for your user only." -f $vmName, $answer.Computer)
Write-Host "Tell Claude it's done. To revoke: delete $saved (and the account in the VM)."
