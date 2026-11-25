# Intel PC worker (owner's PC side of the Terminal Intelligence Engine)

`worker/pricechat_worker/intel.py` runs on the owner's Windows PC (RTX 5090, Ollama on `http://127.0.0.1:11434`). It takes analysis
and embedding jobs from Railway and answers them with local models. It only ever makes outbound calls. The server side is
`backend/app/intel/channel.py` and `backend/app/api/routes/intel_worker.py`.

## Setup (owner, at a terminal)

1. **Make the token** (on any machine with the backend venv):
   `cd backend && python -m app.intel.tokens`
   This prints two lines once: `INTEL_WORKER_TOKEN=pci_...` (secret) and `INTEL_WORKER_TOKEN_SHA256=...` (only a hash).
2. **Railway:** set `INTEL_WORKER_TOKEN_SHA256` to the hash. Railway never sees the token itself. While `INTEL_ENABLED` is false
   (the default), the worker can heartbeat but is never offered a job.
3. **PC:** in `worker/worker.env` (git-ignored, see `worker.env.example`) set:
   - `INTEL_WORKER_TOKEN=pci_...`
   - optionally `INTEL_MODEL` (default `qwen3.8:27b`, the primary chosen from the Phase 0 benchmark; `qwen3.6:27b` is the fallback), `INTEL_EMBED_MODEL` (default
     `nomic-embed-text`) and `INTEL_MAX_CLAIM` (1 to 4, default 1).
   - The server address comes from `PRICECHAT_SERVER` (the same one the other workers use; `PRICECHAT_API_BASE` also works), and the
     Ollama address from `INTEL_OLLAMA_URL` or `OLLAMA_URL`.
4. **Models:** `ollama pull qwen3.8:27b` (and `qwen3.6:27b` as the fallback) and `ollama pull nomic-embed-text`.
5. **Start it:** `D:\pc-gpu\venv\Scripts\pythonw.exe worker\start_intel.pyw` (any Python with `httpx` works). To test it once in a
   console, run `python -m pricechat_worker.intel --once` from `worker/`. To keep it running after logon, run the optional
   `worker\install_intel_task.ps1` yourself. Nothing installs that task automatically.
6. **Check it:** Admin's intel status shows the PC online, with its models. Logs are in `worker/intel.log` and `worker/intel.out.log`.

**Rotating the token:** run step 1 again and set the new hash on Railway. The old token stops working immediately. Then put the new
token in `worker.env` and restart the launcher.

## What it does

- **Heartbeat:** every 30 s it sends the installed Ollama model names (from `/api/tags`), its version, a busy flag and the GPU name.
  If Ollama is down it still heartbeats, with `models=[]` and `busy=true`, and claims nothing.
- **Claim:** every 5 s while idle, it claims up to `INTEL_MAX_CLAIM` jobs, but only when the analysis or embedding model is installed.
- **intel.analyze:** calls Ollama `/api/chat` with the job's JSON schema as `format`, `temperature 0`, `num_ctx 4096` and
  `keep_alive 2m`. Thinking is off for qwen3/deepseek models and set to `"low"` for gpt-oss (the same settings as
  `ops/intel/bench.py`). The prompt and schema come with the job from Railway, so both sides always use the same contract.
- **intel.embed:** calls Ollama `/api/embed` with `nomic-embed-text`, adding `search_document: ` or `search_query: ` depending on the
  job's purpose. The vector is cut to the job's `dim` (layer norm, truncate, then L2-normalise, as the model card describes).
- **Errors:** on any error (Ollama failing, a reply that isn't JSON, a missing model, too little lease left) it posts a signed
  `/fail`, so Railway answers the job straight away instead of waiting for the 120 s lease to run out.
- **Railway unreachable:** it backs off from 5 s up to 5 minutes and never exits on a network error. On a 401 it retries every
  5 minutes. The launcher restarts it if it crashes, and stops for good on exit code 2 (no token, or not a `pci_` token).

## Security properties

- **Outbound only.** The PC calls Railway over HTTPS and its own Ollama, and nothing calls the PC: no tunnel, no port forward, no
  listening socket. The launcher binds `127.0.0.1:47564` only as a single-instance lock. It never listens on that port.
- **Scoped token, stored as a hash.** The `pci_` token works only for `/api/intel-worker/*`. It is not a Brand Manager `pcw_` token
  and not the admin cookie. Railway stores only its SHA-256, and the worker never logs the token (`Config` prints `token=set`).
- **HMAC and nonce.** Each result or failure is signed with HMAC-SHA256, keyed by the token, over
  `"<job id>\n<nonce>\n" + canonical JSON`. The worker's `canonical()` and `sign()` are byte-for-byte copies of the server's (tested).
  The nonce works only once and only within the 120 s lease, so a replayed or late answer gets 409. A tampered answer gets 403.
- **Schema validation.** Railway re-validates every analysis against `ItemAnalysis`, and every embedding must be exactly `dim`
  finite numbers. An invalid answer is refused (422) and goes to Railway's fallback. The worker's own checks are only a convenience.
- **The PC can't publish.** It only proposes labels. Nothing on the channel touches News Desk, Trending or X.
- **Production is unaffected when the PC is offline.** Jobs go to the PC only while its heartbeat is under 60 s old. Otherwise
  Railway answers them itself (Claude, capped), or falls back to today's rules.

## Known limits

- **GPU sharing:** the News Desk picture worker (`newsdesk_images.py`) uses the same GPU, and its post-copy step uses the same Ollama
  (gpt-oss:20b). It shares no busy signal with other processes, so the two aren't coordinated. The intel worker keeps `keep_alive`
  short so the GPU is freed quickly. If they overlap, both run slower; neither fails.
- **Ollama down still counts as online:** while the PC heartbeats with `models=[]`, Railway still treats it as online. Slot jobs then
  wait out their 180 s claim window before Railway answers them (see the contract notes in the branch's report).
