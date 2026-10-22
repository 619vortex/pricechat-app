# Starts the API on http://127.0.0.1:8000 with auto-reload.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "backend")
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
