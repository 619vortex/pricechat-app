"""Kiwi VPN hardening: free-trial guards, sync limits, the reviewer's member id, how a device was made.

Security audit M4 / L6 / L7 / L9 / L8 (docs/hardening/TRIAL-AND-LIMITS.md):
- vpn_trial_claims: salted hashes of identities that have had a free trial (app.vpn.trial_guard), backfilled here for every
  trial started before this migration (the account's verified email and linked Google / X / Telegram accounts).
- vpn_trial_signals: monitor mode's would-be refusals (reason, time, member; a hashed /24 at most), kept 30 days.
- vpn_settings: trial_guard_mode (default monitor), the trial guards, sync_enforce_limits and count_pc_once (both off), reviewer_member_id.
- vpn_devices.created_via: app | web | anon | admin | bot (NULL for devices made before this).

Rechained on release/kiwi-hardening: revises the server-checks migration (feat/kiwi-hardening-monitoring), one head, no data change.
Numbered 0116 (first written as 0113) so both Kiwi VPN migrations follow production's 0114_trending_topics.

Revision ID: 0116_vpn_trial_guards
Revises: 0115_vpn_server_checks
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision: str = '0116_vpn_trial_guards'
down_revision: str | None = '0115_vpn_server_checks'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SALT = "kiwi-vpn-trial-v1"  # the same as app.vpn.trial_guard.SALT
GMAIL = {"gmail.com", "googlemail.com"}


def _normalise_email(email: str) -> str:  # a copy of app.vpn.trial_guard.normalise_email, frozen with this migration
    addr = (email or "").strip().lower()
    if "@" not in addr:
        return addr
    local, domain = addr.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if domain in GMAIL:
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def _hash(key: str) -> str:
    return hashlib.sha256(f"{SALT}|{key}".encode()).hexdigest()


def upgrade() -> None:
    op.create_table(
        "vpn_trial_claims",
        sa.Column("claim_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vpn_trial_claims")),
        sa.UniqueConstraint("claim_hash", name=op.f("uq_vpn_trial_claims_claim_hash")),
    )
    op.create_table(
        "vpn_trial_signals",
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("net_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name=op.f("fk_vpn_trial_signals_member_id_members"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vpn_trial_signals")),
    )
    op.create_index(op.f("ix_vpn_trial_signals_member_id"), "vpn_trial_signals", ["member_id"])
    with op.batch_alter_table("vpn_settings") as batch:
        batch.add_column(sa.Column("trial_one_per_identity", sa.Boolean(), server_default=sa.true(), nullable=False))
        batch.add_column(sa.Column("trial_min_account_age_hours", sa.Integer(), server_default="24", nullable=False))
        batch.add_column(sa.Column("trial_min_xp", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("trial_per_ip_day", sa.Integer(), server_default="3", nullable=False))
        batch.add_column(sa.Column("trial_per_network_day", sa.Integer(), server_default="10", nullable=False))
        batch.add_column(sa.Column("sync_enforce_limits", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("count_pc_once", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("trial_guard_mode", sa.String(length=8), server_default="monitor", nullable=False))
        batch.add_column(sa.Column("reviewer_member_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("vpn_devices") as batch:
        batch.add_column(sa.Column("created_via", sa.String(length=8), nullable=True))

    # backfill: every identity of every member who has already had a trial
    conn = op.get_bind()
    keys: set[str] = set()
    for (email,) in conn.execute(sa.text("SELECT m.email FROM vpn_accounts a JOIN members m ON m.id = a.member_id "
                                         "WHERE a.trial_started_at IS NOT NULL AND m.email IS NOT NULL")):
        keys.add("email:" + _normalise_email(email))
    for platform, uid in conn.execute(sa.text("SELECT li.platform, li.platform_user_id FROM vpn_accounts a "
                                              "JOIN linked_identities li ON li.member_id = a.member_id WHERE a.trial_started_at IS NOT NULL")):
        keys.add(f"{platform}:{uid}")
    now = datetime.now(timezone.utc)
    claims = sa.table("vpn_trial_claims", sa.column("claim_hash", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
                      sa.column("updated_at", sa.DateTime(timezone=True)))
    rows = [{"claim_hash": h, "created_at": now, "updated_at": now} for h in sorted({_hash(k) for k in keys})]
    if rows:
        op.bulk_insert(claims, rows)

    # the store-review account (app/vpn/reviewer.py) keeps working: bound to the member its sign-in created, not its email
    reviewer = conn.execute(sa.text("SELECT l.member_id FROM member_audit_logs l JOIN members m ON m.id = l.member_id "
                                    "WHERE l.action = 'vpn_reviewer_created' ORDER BY l.id DESC LIMIT 1")).scalar()
    if reviewer is not None:
        conn.execute(sa.text("UPDATE vpn_settings SET reviewer_member_id = :m WHERE id = 1"), {"m": reviewer})


def downgrade() -> None:
    with op.batch_alter_table("vpn_devices") as batch:
        batch.drop_column("created_via")
    with op.batch_alter_table("vpn_settings") as batch:
        for name in ("reviewer_member_id", "trial_guard_mode", "count_pc_once", "sync_enforce_limits", "trial_per_network_day", "trial_per_ip_day",
                     "trial_min_xp", "trial_min_account_age_hours", "trial_one_per_identity"):
            batch.drop_column(name)
    op.drop_table("vpn_trial_signals")
    op.drop_table("vpn_trial_claims")
