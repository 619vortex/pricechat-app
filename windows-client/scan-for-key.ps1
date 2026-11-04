# Run by ipc-and-key-test.ps1 with administrator approval. Looks for the config's private key where it mustn't be, and reports only yes or no: it never prints the key.
param([Parameter(Mandatory)][string]$Config, [Parameter(Mandatory)][string]$Out, [Parameter(Mandatory)][string]$AppData)
$ErrorActionPreference = "Continue"
$lines = @()
$key = (Select-String -Path $Config -Pattern '^\s*PrivateKey\s*=\s*(\S+)\s*$').Matches.Groups[1].Value
function Holds($folder) {
  foreach ($f in Get-ChildItem $folder -Recurse -File -Force -ErrorAction SilentlyContinue) {
    if ($f.Length -gt 200MB) { continue }
    try {
      $bytes = [IO.File]::ReadAllBytes($f.FullName)
      $ascii = [Text.Encoding]::ASCII.GetString($bytes)
      $utf16 = [Text.Encoding]::Unicode.GetString($bytes)
      if ($ascii.Contains($key) -or $utf16.Contains($key)) { return $true }
    } catch { }
  }
  return $false
}
$lines += if (Test-Path "C:\Program Files\KiwiVPN\tunnels\kiwi.conf") { "conf: STILL THERE" } else { "conf: gone" }
$lines += if (Holds "C:\Program Files\KiwiVPN") { "install folder: FOUND" } else { "install folder: clean" }
$data = "C:\Program Files\WireGuard\Data"
$lines += if (-not (Test-Path $data)) { "log: none" } elseif (Holds $data) { "log: FOUND" } else { "log: clean" }
$lines += if (-not (Test-Path $AppData)) { "app data: none" } elseif (Holds $AppData) { "app data: FOUND" } else { "app data: clean" }
$lines += "done"
Set-Content -Path $Out -Value $lines -Encoding ascii
