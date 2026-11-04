# Run by ipc-and-key-test.ps1 with administrator approval, while connected. Takes the key from the service's protected config and looks for it where it mustn't be. Reports only yes or no: it never prints the key.
param([Parameter(Mandatory)][string]$Out, [Parameter(Mandatory)][string]$AppData)
$ErrorActionPreference = "Continue"
$lines = @()
$conf = "C:\Program Files\KiwiVPN\tunnels\kiwi.conf"
$key = if (Test-Path $conf) { (Select-String -Path $conf -Pattern '^\s*PrivateKey\s*=\s*(\S+)\s*$').Matches.Groups[1].Value } else { "" }
if (-not $key) { Set-Content -Path $Out -Value "key: NOT FOUND" -Encoding ascii; exit 1 }
$lines += "key: read"
function Holds($folder) {
  foreach ($f in Get-ChildItem $folder -Recurse -File -Force -ErrorAction SilentlyContinue) {
    if ($f.FullName -eq $conf -or $f.Length -gt 200MB) { continue }
    try {
      $bytes = [IO.File]::ReadAllBytes($f.FullName)
      if ([Text.Encoding]::ASCII.GetString($bytes).Contains($key) -or [Text.Encoding]::Unicode.GetString($bytes).Contains($key)) { return $true }
    } catch { }
  }
  return $false
}
$lines += if (Holds "C:\Program Files\KiwiVPN") { "install folder: FOUND" } else { "install folder: clean" }
$data = "C:\Program Files\WireGuard\Data"
$lines += if (-not (Test-Path $data)) { "log: none" } elseif (Holds $data) { "log: FOUND" } else { "log: clean" }
$lines += if (-not (Test-Path $AppData)) { "app data: none" } elseif (Holds $AppData) { "app data: FOUND" } else { "app data: clean" }
Set-Content -Path $Out -Value $lines -Encoding ascii
