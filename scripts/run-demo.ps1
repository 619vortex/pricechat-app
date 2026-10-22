# Community + X intelligence demo WITHOUT real Telegram or X accounts (and without any X API cost).
# Starts: a mock Telegram Bot API (127.0.0.1:8081), a mock X API (127.0.0.1:8082), a mock YouTube API (127.0.0.1:8083), a demo backend on
# 127.0.0.1:8001 using a SEPARATE database (data\demo.db) and a SEPARATE secret store
# (data\demo-secrets.dpapi), and a dashboard on http://127.0.0.1:5174. Your real data and keys are never touched.
# Stop everything with Ctrl+C in each window.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "$root\backend\.venv\Scripts\python.exe"

Start-Process -FilePath $py -ArgumentList "$root\scripts\mock_telegram.py" -WorkingDirectory $root
Start-Process -FilePath $py -ArgumentList "$root\scripts\mock_x.py" -WorkingDirectory $root
Start-Process -FilePath $py -ArgumentList "$root\scripts\mock_youtube.py" -WorkingDirectory $root

$env:DATABASE_URL = "sqlite:///$($root -replace '\\','/')/data/demo.db"
$env:SECRETS_FILE = "$root\data\demo-secrets.dpapi"
$env:TELEGRAM_BOT_TOKEN = "100000001:MockTokenForLocalDevelopmentOnly_xyz"
$env:TELEGRAM_API_BASE = "http://127.0.0.1:8081"
$env:TELEGRAM_LONG_POLL_SECONDS = "0"
$env:X_API_BASE = "http://127.0.0.1:8082"
$env:X_BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAAMockBearerForLocalDemoOnly000000"
$env:X_ACCOUNT_USERNAME = "pricechat"
$env:YOUTUBE_API_BASE = "http://127.0.0.1:8083/youtube/v3"
Start-Process -FilePath $py -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8001" -WorkingDirectory "$root\backend"

$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:8001"
Set-Location "$root\frontend"
Write-Host "Demo dashboard: http://127.0.0.1:5174" -ForegroundColor Green
Write-Host "  Community > Setup & chats: authorise 'Price Chat Community (demo)'" -ForegroundColor Green
Write-Host "  YouTube: connect with key AIzaMockYouTubeKeyForLocalDemoOnly000000 and channel @pricechat_demo, then Sync now" -ForegroundColor Green
Write-Host "  X > Connection: press 'Test connection', then add monitored topics such as 'funding rates'" -ForegroundColor Green
npx vite --port 5174 --strictPort
