# Runs backend tests and the frontend type-check/build.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location "$root\backend"
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

Push-Location "$root\frontend"
npm run build
$code = $LASTEXITCODE
Pop-Location
exit $code
