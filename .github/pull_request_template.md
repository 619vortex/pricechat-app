## What and why

## How it was tested
- [ ] Backend: `python -m pytest tests -q` (only the known time-of-day test may fail)
- [ ] Frontend: `npx vitest run` and `npx tsc -b`
- [ ] gitleaks is clean (pre-commit hook or CI)

## Production impact (the owner deploys; merged is not deployed)
- [ ] Needs a backend deploy (Railway `api`)
- [ ] Needs a frontend deploy (Vercel `pricechat`)
- [ ] Adds an Alembic migration (number and `down_revision` checked against `main`)
- [ ] Adds or changes an environment variable (name added to `.env.example`, value given to the owner privately)
- [ ] Touches the News Desk, publishing, payments, VPN servers or another safety switch (say how accuracy and safety checks are kept)
