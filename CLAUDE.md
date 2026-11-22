# Price Chat: shared instructions for Claude Code

Two developers work on this repository, the **owner** (GitHub `619vortex`) and **Adam**, each in their own Claude Code session on
their own machine. This file is the contract both sessions follow. If an instruction here conflicts with a request, stop and ask.

## Hard rules (never without the owner's explicit approval in the current conversation)

1. **No production deploys.** Never run `railway up`, `vercel --prod`, `railway redeploy`, a Vercel promote or rollback, or anything
   that changes the live site, API, database, workers or servers. Prepare the change; the owner deploys.
2. **No production settings.** Never change Railway or Vercel variables, the live database, Admin settings, News Desk switches
   (market-data pause, emergency stop, X live/dry run, spending caps), Telegram channels or bots, VPN servers or payments.
3. **Never publish.** No posts, corrections, replies or deletions on X or Telegram, no app-store or Chrome Web Store submissions.
4. **Never commit secrets.** Real values live only in Railway (service `api`), Vercel (project `pricechat`) and git-ignored local
   files (`.env`, `worker/worker.env`, `data/`). Examples use empty values. Install the gitleaks pre-commit hook in every clone
   (`pip install pre-commit && pre-commit install`; config in `.pre-commit-config.yaml` and `.gitleaks.toml`); CI scans every pull
   request too. If you find a secret in a diff, stop and tell the owner.
5. **Never rewrite shared history**: no force-push to `main` or to someone else's branch, no `git filter-repo`, no rebasing
   pushed commits that others use.
6. **Never use `git stash`** in this repo's worktrees: the stash is shared by every worktree of a clone, and parallel sessions
   have swapped each other's work this way. Commit to a branch instead (a `wip/` branch is fine).
7. **Accuracy is a hard gate** for anything the News Desk publishes (incidents 2026-11-13 and 2026-11-21: a wrong ticker and a
   570x liquidation unit error). Never loosen a fact check, number check, second-source check or evidence rule to get more posts.

## Branches and pull requests

- `main` is the production baseline: what Railway and Vercel run (plus documentation). It is protected: changes arrive only by
  pull request with the owner's approval. Never commit to `main` directly.
- Work on a branch named `<who>/<kind>/<topic>`, e.g. `adam/feat/kiwi-onboarding`, `owner/fix/newsdesk-dedup`. Kinds: `feat`, `fix`,
  `chore`, `docs`, `test`. One topic per branch; keep branches short-lived and rebase on `main` before opening the PR (rebasing your
  own unshared branch is fine).
- Before opening a PR: the relevant test suites pass (see below), `npx tsc -b` is clean for frontend changes, gitleaks is clean,
  and the PR description says what changed, how it was tested, and whether it needs a deploy, a migration or a new variable.
- **Merged is not deployed.** After a merge the owner decides when to deploy and records it. Nothing auto-deploys from GitHub:
  Railway and Vercel are deployed from the owner's machine. Do not connect either service to this repository.
- Older branches (`feat/*`, `fix/*`, `release/*`, `rollback/*`, `audit/*`, ...) predate the shared repo. `wip/*-20261122` are
  snapshots of uncommitted work taken on 2026-11-22. Don't delete any of them without the owner.
- Migrations (Alembic, `backend/migrations/versions`) are numbered in one chain. Two branches adding a migration at the same time
  must re-chain on merge: the second to merge renumbers and sets its `down_revision` to the first. Say so in the PR.

## What is in the repository

| Path | What | Runs where |
|---|---|---|
| `backend/` | FastAPI app (`app/`), Alembic migrations, tests, the Vortex Realm build (`realm/*.gz`, tracked on purpose) | Railway service `api` (`api.pricechat.live`); migrations run at start (`railway.json`) |
| `backend/app/newsdesk/` | News Desk: collects feeds, scores, editorial and newsworthy gates, safety limits, Telegram + X delivery | production scheduler |
| `backend/app/` (other) | members/passport, portfolio, pokemon, tradingos/trading bot, trending, terminal, brand_manager, vpn, kalshi, ... | Railway |
| `frontend/` | React + Vite + TypeScript: the website, Admin, Terminal, member pages | Vercel project `pricechat` (`pricechat.live`) |
| `worker/` | PC worker: AI jobs, News Desk GPU images, World Monitor; reads `worker/worker.env` | the owner's PC (scheduled tasks) |
| `fundarb/`, `backend/fundarb/` | funding-arbitrage research tools | local only |
| `android-client/`, `ios-client/`, `windows-client/`, `vpn-extension/`, `vpn-node/` | Kiwi VPN apps, Chrome extension, server agent | stores, users' devices, VPN servers |
| `ops/`, `scripts/`, `docs/` | runbooks, setup scripts, audits, incident records, release notes | |

Production runs only the jobs in `SCHEDULER_ONLY_JOBS`; a local backend has its own SQLite database (`data/price_chat.db`),
separate from production. Never point a local backend at production data.

## Setup and tests

Requirements: Python 3.12+ (tested on 3.14), Node 20+ (tested on 24). Ollama is optional (local AI features).

```powershell
copy .env.example .env                      # then fill in only what you need locally; never real production keys
cd backend; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m pytest tests -q -p no:cacheprovider        # about 12 minutes, ~3600 tests
cd ..\frontend; npm ci; npx vitest run; npx tsc -b
```

- On macOS/Linux use `.venv/bin/...`. CI (`.github/workflows/ci.yml`) runs the same on every pull request.
- On the owner's Windows PC, Application Control blocks SQLAlchemy's compiled `.pyd`; tests there run with a `sitecustomize.py`
  shim on `PYTHONPATH` that loads the pure-Python fallbacks. Not needed elsewhere.
- Time-dependent tests: `test_members.py::test_telegram_participation_counts_meaningful_messages_only` fails between 00:00 and
  01:00 UTC. Other failures are real: fix them or say so in the PR.
- Files mix CRLF and LF line endings: keep each file's own endings when editing (`core.autocrlf=false` in this repo).

## Production facts worth knowing (read-only knowledge)

- Railway project `pricechat`, environment `production`, service `api`. Vercel project `pricechat`. The owner deploys from a checkout
  of `main`; `railway up` must only ever run from a folder linked to that service (an unlinked folder once created a stray project).
- The News Desk publishes to Telegram `@Pricechatupdates` and X `@thepricechat`. Market-data posts are paused until the owner
  resumes them; X spending caps are set by the owner and are never raised by code or by a session.
- Telegram group moderation deletes rule-breaking messages and never mutes members.
- Kiwi VPN: customers share the VPN servers on `wg1`; `wg0` is the owner's personal peer; never run `install.sh` on a live server.
  Payments are off until the owner switches them on.
- Releases and incidents are documented in `docs/incidents/` and `docs/hardening/`; read the latest before touching the News Desk
  or Kiwi infrastructure.

## Working style

- Small, reviewable commits with messages that say why. Keep the existing code style (long lines, comments that cite the owner's
  request or the production incident that motivated a rule).
- When you are unsure whether something touches production, it does: ask first.
