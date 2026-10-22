# Price Chat AI worker (Brand Manager)

Turns Brand Manager jobs into finished TikTok videos: script (local LLM) → voiceover → scenes → video with animated
captions → quality check → upload. It runs on the **DGX Spark** (or any machine with Python 3.11+), connects **out**
to the Price Chat backend over HTTPS with its own token, and exposes nothing to the internet.

## Set up on the DGX Spark

```bash
# 1. the worker
git clone <this repo> && cd price-chat/worker      # or copy the worker/ folder
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg fonts-dejavu-core           # optional: a system FFmpeg (else imageio-ffmpeg's static build is used)

# 2. script writing: Ollama with a capable model
ollama pull gemma4:31b                              # the script writer (worker/model_bakeoff.py compares models); or set OLLAMA_MODEL

# 3. a natural voice: Piper (local neural TTS)
pip install piper-tts
mkdir -p ~/voices && cd ~/voices && \
  wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx{,.json}
export PIPER_MODEL=~/voices/en_US-ryan-high.onnx

# 4. register it: Dashboard › Brand Manager › Settings › AI › Add worker (copy the token, shown once)
python -m pricechat_worker --server https://api.pricechat.live --token pcw_...
```

Run it as a service so it survives reboots (`systemd`):

```ini
# /etc/systemd/system/pricechat-worker.service
[Unit]
Description=Price Chat AI worker
After=network-online.target ollama.service

[Service]
WorkingDirectory=/home/USER/price-chat/worker
Environment=PRICECHAT_SERVER=https://api.pricechat.live
Environment=PRICECHAT_WORKER_TOKEN=pcw_...
Environment=PIPER_MODEL=/home/USER/voices/en_US-ryan-high.onnx
ExecStart=/home/USER/price-chat/worker/.venv/bin/python -m pricechat_worker
Restart=always
RestartSec=10
MemoryMax=8G
CPUQuota=400%

[Install]
WantedBy=multi-user.target
```

## Short Market (3D story videos)

The same worker also makes Short Market's videos (`docs/SHORT_MARKET.md`): it asks the backend for Brand Manager jobs
first, then for shorts. Nothing extra to register. For animated scenes and the natural voice it needs a second Python
environment with PyTorch, and two models:

```powershell
py -3.12 -m venv .venv-gpu          # 3.12: on Windows, Smart App Control blocks PyTorch's Python 3.14 build
.venv-gpu\Scripts\python -m pip install --prefer-binary torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
.venv-gpu\Scripts\python -m pip install --prefer-binary -r requirements-gpu.txt
```

| Setting (worker.env) | Default | What |
|---|---|---|
| `PCW_GPU_PYTHON` | `.venv-gpu` next to this file | the Python that has PyTorch |
| `PCW_VIDEO_MODEL` | `models/Wan2.2-TI2V-5B-Diffusers` | the folder of `Wan-AI/Wan2.2-TI2V-5B-Diffusers` (~34 GB) |
| `KOKORO_MODEL`, `KOKORO_VOICES` | `models/kokoro/kokoro-v1.0.onnx`, `voices-v1.0.bin` | the Kokoro voice (~350 MB) |

Without them the worker still finishes shorts with still scenes and the system voice, and the dashboard says so.
The video model runs in its own process per short (about 14 GB of GPU memory, released when the short is done); the
worker unloads the Ollama writing model first so both fit.

## Engines

| Setting | Engines (first available) |
|---|---|
| Script (`strategy.llm`) | `ollama` (OLLAMA_URL, OLLAMA_MODEL) → `anthropic` (ANTHROPIC_API_KEY on the worker) → `template` (no AI; facts only) |
| Voice (`strategy.voice`) | `piper` (PIPER_MODEL) → `openai` (OPENAI_API_KEY, OPENAI_VOICE) → `system` (Windows voice / espeak-ng) → `kokoro` (only when asked for by name; Short Market does) |
| Video | Pillow scenes + FFmpeg (`FFMPEG`, PATH, or imageio-ffmpeg) |
| Fonts | `FONT_BOLD` / `FONT_REGULAR`, else Segoe UI / Arial (Windows) or DejaVu (Linux) |

The backend checks every script (no invented numbers, no financial claims, no banned topics) and the worker rewrites
with that feedback; after three tries it falls back to the facts-only template script.

## Safety and reliability

- Only the worker's token hash is stored on the server; disable or delete a worker in Settings › AI at any time.
- Each job has a lease: progress reports extend it; if the worker crashes, the job returns to the queue.
- STOP AUTO: the worker gets no new jobs. EMERGENCY STOP: the running job is told to stop at its next progress report.
- Spark offline: jobs wait in the queue. A cloud fallback worker (see `Dockerfile`) can be registered as "cloud".

## Test

```bash
PYTHONPATH=. python -m pytest tests -q
python -m pricechat_worker --server http://127.0.0.1:8000 --token pcw_... --once --keep ./out   # one job, keep the MP4
```
