# Starts the dashboard on http://127.0.0.1:5173 (proxies /api to the backend).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")
npm run dev
