# Shared brief for every audit reviewer

Project: Price Chat (C:\AI\price-chat; audit worktree C:\AI\pc-wt\audit on branch audit/full-2026-11 — read code here; it equals main e978cc3).
Backend: FastAPI + SQLAlchemy, SQLite on a Railway volume (backend/app, ~97k lines; routes in backend/app/api/routes; jobs in backend/app/jobs.py; scheduler app/scheduler; migrations backend/migrations/versions, 105). Frontend: Vite + React (frontend/src: website/ is the public site, pages/ + AdminApp.tsx the admin), deployed on Vercel at https://pricechat.live (API at https://api.pricechat.live). Production runs only the jobs listed in Railway env SCHEDULER_ONLY_JOBS.
Other code: vpn-node/ (VPN server installer), vpn-extension/ (Chrome extension), windows-client/ (Kiwi VPN Windows), worker/, fundarb/, many unmerged feature branches (git -C C:\AI\price-chat branch -a; worktrees under C:\AI\pc-wt).
Owner's memory notes (useful context, may be stale — verify against code): C:\Users\USER\.claude\projects\C--AI-synthetic-ai\memory\*.md

HARD RULES (non-negotiable)
- READ-ONLY toward production. No deploys, no `railway up`, no `railway ssh`, no `railway variables` (prints secrets), no writes to any live API, no database migrations, no billing changes, no live trades (Hyperliquid testnet only, and only read calls unless told otherwise), no changes to VPN servers, no publishing.
- Public, non-destructive GETs against https://pricechat.live and https://api.pricechat.live are fine (no login, no form submits, no load tests, no brute force). `railway logs` (read) is fine from C:\AI\price-chat\backend.
- Never print secrets, tokens, keys, cookies or personal data. Don't open .env files' values; you may check which variable NAMES are referenced in code.
- Don't compile or run Rust on this PC. Python/Node is fine.
- Don't edit code under C:\AI\price-chat or other worktrees. You may add NEW test or diagnostic files only inside C:\AI\pc-wt\audit (e.g. backend/tests/audit_*.py), clearly named.
- Running backend tests: Application Control blocks SQLAlchemy's compiled .pyd here. Use the shim: from C:\AI\pc-wt\audit\backend run
  `PYTHONPATH="C:/Users/USER/AppData/Local/Temp/claude/8bda9b0a0137d3c5/ffd3112d-5e3a-4168-8656-5c5285927885/scratchpad/pyshim" C:/AI/price-chat/backend/.venv/Scripts/python.exe -m pytest <tests> -q -p no:cacheprovider`
  Known pre-existing failures on main (not regressions): test_community::test_agent_receives_labelled_relevant_community_context, test_lunch_bot::test_the_public_page_shows_paper_results_and_the_assumptions, test_trading_execution::test_referral_states_come_from_hyperliquids_record_and_no_longer_gate_bot_trading, test_trends::test_trends_api_lists_and_makes_a_video_once. Full suite already ran today: 1951 passed, these 4 failed — don't re-run the whole suite; run targeted tests only.
- Don't trust docs or comments: confirm in code. Don't claim something works without evidence. Mark each finding CONFIRMED (you reproduced or read the exact code path), SUSPECTED (strong indication, not proven) or UNVERIFIED (can't check from here, say why).

OUTPUT
Write your findings to C:\AI\pc-wt\audit\docs\audits\price-chat-full-audit\notes\<your-area>.md in this format:
  ## Summary (5-10 lines: health of the area, biggest risks, biggest opportunities)
  ## Findings — one per item:
    ### <AREA>-<n>: <title>
    Severity: Critical/High/Medium/Low · Status: CONFIRMED/SUSPECTED/UNVERIFIED · Effort: S/M/L
    Evidence: file:line and/or exact command + output (short)
    Impact / Repro / Recommended fix
  ## Untested / couldn't verify (and why)
  ## Improvement & feature ideas for this area (ranked, with rationale)
  ## Commands run (for the evidence appendix)
Be concrete and critical. Prefer 15 well-evidenced findings over 60 vague ones, but don't skip real problems. Your final reply to the coordinator: a 10-line summary and the path of your notes file.
