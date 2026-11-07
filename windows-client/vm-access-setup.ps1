# Lets Claude reach the Kiwi VPN test VM through PowerShell Direct (no network, no file shares). Run in a NORMAL
# (not administrator) PowerShell on the host, after adding yourself to "Hyper-V Administrators" and signing out and in.
#
#   powershell -ExecutionPolicy Bypass -File windows-client\vm-access-setup.ps1
#
# It asks for the VM account (e.g. kiwibuild) in a Windows prompt and saves it encrypted with DPAPI for your Windows user
# only, at %APPDATA%\KiwiVPN\vm-credential.xml. Nothing is printed. Delete that file to take access away again.
$ErrorActionPreference = "Stop"
try { $vms = @(Get-VM) } catch { throw "This window can't see Hyper-V yet: add your user to 'Hyper-V Administrators', sign out and back in, then run this again." }
if (-not $vms.Count) { throw "No VM found." }
"VMs on this PC:"; $vms | ForEach-Object { "  $($_.Name)  ($($_.State))" }
$name = if ($vms.Count -eq 1) { $vms[0].Name } else { Read-Host "Name of the Kiwi VPN test VM" }
$vm = Get-VM -Name $name
if ($vm.State -ne "Running") { Start-VM -Name $name; "Started $name; waiting for Windows to come up..."; Start-Sleep -Seconds 60 }
$guest = Get-VMIntegrationService -VMName $name -Name "Guest Service Interface"
if (-not $guest.Enabled) { Enable-VMIntegrationService -VMName $name -Name "Guest Service Interface"; "Turned on file copy into the VM (Guest Service Interface)." }
$cred = Get-Credential -Message "The local administrator account INSIDE the VM (e.g. kiwibuild)"
$test = Invoke-Command -VMName $name -Credential $cred -ScriptBlock { "$env:COMPUTERNAME|$((New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))" }
$computer, $admin = $test -split "\|"
if ($admin -ne "True") { throw "That account works but isn't an administrator in the VM: add it to Administrators there." }
$dir = Join-Path $env:APPDATA "KiwiVPN"
New-Item -ItemType Directory -Force $dir | Out-Null
[pscustomobject]@{ VMName = $name; Credential = $cred } | Export-Clixml -Path (Join-Path $dir "vm-credential.xml")
"OK: '$name' (Windows name $computer) is reachable as an administrator. Access saved, encrypted for your user only."
"Tell Claude it's done. To revoke: delete $dir\vm-credential.xml (and the account in the VM)."
