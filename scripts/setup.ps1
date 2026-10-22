# One-time setup: Python venv + backend deps, frontend deps, and .env.
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "==> Backend" -ForegroundColor Cyan
Push-Location "$root\backend"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Pop-Location

Write-Host "==> Frontend" -ForegroundColor Cyan
Push-Location "$root\frontend"
npm install
Pop-Location

if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "Created .env from .env.example" -ForegroundColor Yellow
}

$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama -and (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if ($ollama) {
    Write-Host "==> Ollama models" -ForegroundColor Cyan
    & $ollama pull llama3.1:8b
    & $ollama pull nomic-embed-text
} else {
    Write-Host "Ollama not found. Install it (winget install --id Ollama.Ollama -e), then run: ollama pull llama3.1:8b; ollama pull nomic-embed-text" -ForegroundColor Yellow
}

Write-Host "Setup complete." -ForegroundColor Green
