# The build and test VM

**Why a VM builds as well as tests:** this PC's Smart App Control blocked Cargo's compiled build helpers ("An Application Control policy has blocked this file", os error 4551), and the WireGuard build runs unsigned tools too. Don't turn Smart App Control off on the host: Microsoft documents that as hard to undo. A VM is also what keeps testing away from `client1`.

Everything goes on `D:`. `C:` has about 39 GB free, which is too tight for a VM.

## 1. Turn on Hyper-V (once, needs a restart)

In PowerShell as administrator on the host:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
```

Restart when it asks. Plan for the moment: the PC, and `client1`, go down during the restart.

## 2. Get Windows

Download the Windows 11 Enterprise evaluation ISO (free, 90 days) from the Microsoft Evaluation Center, and save it as `D:\VMs\win11-eval.iso`.

## 3. Create the VM

In PowerShell as administrator:

```powershell
$n = "KiwiVPN-Test"
New-Item -ItemType Directory -Force D:\VMs | Out-Null
New-VM -Name $n -Generation 2 -MemoryStartupBytes 8GB -NewVHDPath "D:\VMs\$n.vhdx" -NewVHDSizeBytes 120GB -SwitchName "Default Switch"
Set-VMProcessor -VMName $n -Count 4
Set-VMKeyProtector -VMName $n -NewLocalKeyProtector
Enable-VMTPM -VMName $n
Add-VMDvdDrive -VMName $n -Path D:\VMs\win11-eval.iso
Set-VMFirmware -VMName $n -FirstBootDevice (Get-VMDvdDrive -VMName $n)
Start-VM -Name $n
vmconnect localhost $n
```

Install Windows, then in the VM's Windows Security turn Smart App Control off if it's on. It's safe there: the VM is disposable.

## 4. Save a clean point

Before installing anything:

```powershell
Checkpoint-VM -Name KiwiVPN-Test -SnapshotName "clean"
```

Every install and uninstall test starts from this.

## 5. Build inside the VM

Copy `windows-client` into the VM (Enhanced Session lets you paste files), install Git for Windows there, then run `build-in-vm.ps1`.

## Reading the test results

The VM's traffic leaves through the host, and the host goes through `client1`, whose exit is Helsinki (`2.29.149.29`). So **an IP check on Finland proves nothing**: the VM already looks like Helsinki. Germany is the real test, because its address differs. For Finland, confirm the tunnel's own handshake and byte counters too, using `kiwivpn-service status`.
