# Milestone 3: authentication changes. Approved by the owner; not deployed yet.

The code is on `feat/kiwi-windows`, merged with `main`. Deploy with `deploy-m3.ps1`: the auto-mode safety check stops Claude running a production deploy, so the owner runs it.

## What changes
**Backend**
- `POST /api/members/desktop/code`: the signed-in website page asks for a one-time code.
- `POST /api/members/desktop/token`: the app swaps code + secret verifier (PKCE, S256) for a session. Codes last 5 minutes, work once, are stored only as hashes, and a wrong attempt still spends them.
- **New:** the app's session starts `kd.` and reaches only `/api/members/vpn*`, `/api/members/me` and `/api/members/logout`. Everything else gets 403, so a token stolen from a PC can't spend, trade or change the account.
- Admin: `POST /api/vpn/admin/testers` names up to 10 testers for up to 90 days while the VPN is closed.
- Sessions last 90 days. `POST /api/members/desktop/refresh` swaps one for a new token about weekly, and the old one stops working. `member_sessions.kind` marks them "desktop", and `/members/desktop/sessions/revoke` signs out every PC, leaving browsers alone.
- `POST /api/members/vpn/devices` also accepts `public_key`: the private half stays on the PC. `GET /api/members/vpn/devices/{id}/peer` gives current connection details. `GET /api/members/vpn/ip` returns the caller's address, unstored.

**Website**
- `/vpn/desktop`: the "Allow" page, reachable only with the app's link, and `noindex`. It sends the code only to `127.0.0.1`.
- Admin > VPN > People for testers.

**App (written, first compile is in the VM)**
- Opens the system browser, listens on `127.0.0.1` on a random port, checks `state`, swaps the code.
- The token lives only in Rust, encrypted with Windows DPAPI, and never enters the web page. All API calls run from Rust: the backend refuses cross-site POSTs, and a request from the app's web page would count as one.
- DPAPI protects against other users and a stolen disk, **not** against malware running as the same user.

## The exact migration: `0102_desktop_codes` (revises `0101_newsdesk`)
```python
def upgrade():
    op.create_table(
        "member_desktop_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("challenge", sa.String(43), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_member_desktop_codes_member_id", "member_desktop_codes", ["member_id"])
    op.add_column("vpn_accounts", sa.Column("tester_until", sa.DateTime(timezone=True)))
    op.add_column("member_sessions", sa.Column("kind", sa.String(16)))

def downgrade():
    with op.batch_alter_table("member_sessions") as batch:
        batch.drop_column("kind")
    with op.batch_alter_table("vpn_accounts") as batch:
        batch.drop_column("tester_until")
    op.drop_table("member_desktop_codes")
```
One new empty table, and two empty nullable columns. No existing data is changed. The `kd.` limit needs no schema change.

**Tested on a throwaway SQLite database, not a copy of production:** upgrade to 0102 kept an existing row with the new column empty; downgrade removed both and left the row identical; upgrading again worked.

## Deployment impact
- **Who notices:** nobody. VPN access stays closed, nothing links to `/vpn/desktop`, and existing sessions and devices don't change.
- **Downtime:** the single Railway replica restarts, so the site's API, scheduler and Telegram bots blink for as long as a normal deploy. The migration itself should take well under a second, but SQLite holds its write lock while it runs.
- **Scope:** the deploy carries everything on `main` at that moment. Before deploying I'll list the exact commits and confirm nothing else is waiting on `main`.
- **VPN servers, firewall and the Windows service:** untouched.
- **Order:** backend first, then website: the website's new page needs the backend's routes, and the reverse does no harm.

## Backup procedure
**The automatic nightly backup can't be relied on.** `system.backup` failed 12 nights out of 12 up to 2026-11-03 (SignatureDoesNotMatch on the bucket), and I have no record of it being fixed.
1. You take a manual Railway volume backup: Railway > `api` > Volume > Backups > Back up now. That's how the two earlier migrations were protected (`59616b60`, `0e2f0314`).
2. Send me its ID and time. I won't deploy without one taken within the hour, and I record it in the notes.
3. It has never been test-restored. If you want real assurance, restore one into a throwaway volume first; that's your decision, because it costs time.

## Deploy steps (`deploy-m3.ps1` runs 1 to 6, except the tests in step 3)
1. Backup, as above.
2. Merge `feat/kiwi-windows` to `main`, in a clean worktree. The main checkout holds another session's uncommitted files, so never deploy from it.
3. Run the full backend and frontend tests on the merged commit.
4. `npx @railway/cli up <clean-worktree>/backend --path-as-root --service api --ci`, with the path as an argument, never `cd ...; railway up`: that caused the 2026-11-03 outage.
5. Check: `/api/health/live`; `POST /api/members/desktop/token` with a bad code gives 400 rather than 404; the VPN's public status still says closed.
6. Deploy the website to Vercel, and check `/vpn/desktop` and `/vpn`.
7. In the VM: sign in, sign out, bad code, expired code.

## Rollback plan
**Do not press "Redeploy" on the previous Railway deployment.** I tested this: old code on a migrated database stops at startup with `Can't locate revision identified by '0102_desktop_codes'`, and the backend stays down.

| Situation | What to do | Tested? | Data lost |
|---|---|---|---|
| Sign-in misbehaves but the site is fine | Website rollback on Vercel to the previous deployment, leaving the backend. The routes do nothing without the page | Normal Vercel action | None |
| Backend must go back | Deploy the previous code **plus the single file** `migrations/versions/0102_desktop_codes.py`, leaving the database as it is. Old code ignores the new table and column | **Yes:** it started cleanly | None |
| Schema itself must go | Run `alembic downgrade 0101_newsdesk` first, then deploy old code. This needs a shell on Railway, and `railway ssh` needs a key that isn't set up | Downgrade yes; on Railway no | Sign-in codes and tester dates |
| Database damaged | Restore the volume backup from step 1 | **Never tested** | Everything since the backup |

Revoking the app's sessions if needed: delete `member_sessions` rows with `kind = 'desktop'`, or use the member's own button on My Devices.

## Decided by the owner
VPN-only sessions, recording desktop sessions in 0102, locally generated keys and automatic device registration: all approved. Testers stay in this migration.

## Remaining weak points
- A local program can start the sign-in with its own verifier and a loopback listener. If the person presses Allow, it gets a session. The page says to continue only if you just pressed Sign in in the app; the `kd.` limit caps the damage.
