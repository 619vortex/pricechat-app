"""Kiwi VPN: WireGuard servers we run (one node agent per VPS) and members' devices on them.

No traffic logs. A device row holds only which account owns which PUBLIC key on which server, its tunnel IP, the last handshake time
and this month's bytes. The client's private key is generated, put in the config returned once, and never stored.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class VpnSettings(Base):
    """One row. Admin › Kiwi VPN."""

    __tablename__ = "vpn_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # closed until launch
    min_rank: Mapped[str] = mapped_column(String(16), default="warrior", server_default="warrior", nullable=False)  # a Rank.key
    max_devices: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    monthly_cap_gb: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # per member per UTC month, 0 = unlimited
    payments_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # off: free for anyone it's open to


class VpnServer(IdMixin, TimestampMixin, Base):
    """A VPS running the node agent. public_key / endpoint / address / dns are read from the agent's /v1/status."""

    __tablename__ = "vpn_servers"

    name: Mapped[str] = mapped_column(String(48), nullable=False)
    region: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), default="", nullable=False)  # ISO-2
    agent_url: Mapped[str] = mapped_column(String(255), nullable=False)  # https://ip:8443
    agent_token_enc: Mapped[str] = mapped_column(Text, nullable=False)  # base64 of secrets.protect(token)
    cert_sha256: Mapped[str] = mapped_column(String(64), nullable=False)  # pinned agent certificate (hex, lower-case)
    capacity: Mapped[int] = mapped_column(Integer, default=250, server_default="250", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    endpoint_host: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    listen_port: Mapped[int] = mapped_column(Integer, default=51820, server_default="51820", nullable=False)
    address: Mapped[str] = mapped_column(String(64), default="", nullable=False)  # the server's tunnel address + pool, "10.66.0.1/16"
    address6: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)  # its IPv6 pool, "fd66:66::1/64"; "" = none
    dns: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    adblock: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # its resolver blocks ads & trackers
    fail_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # syncs in a row that couldn't reach it


class VpnAccount(IdMixin, TimestampMixin, Base):
    """Who holds VPN time: a Passport member, or an anonymous key holder (no member, no email, no name; only the key's hash)."""

    __tablename__ = "vpn_accounts"

    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True)
    key_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active", nullable=False)  # active | blocked
    paid_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lapsed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # when its devices were switched off for lack of time
    stripe_customer: Mapped[str | None] = mapped_column(String(64))
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # the devices made in Admin: never charged, no key, no member


class VpnPayment(IdMixin, TimestampMixin, Base):
    """One top-up or card renewal. For anonymous accounts billing.scrub removes the account link and the blockchain details."""

    __tablename__ = "vpn_payments"

    account_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_accounts.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # nowpayments | stripe
    order_ref: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)  # our random reference, never the account
    provider_ref: Mapped[str | None] = mapped_column(String(80))
    network: Mapped[str | None] = mapped_column(String(16))
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False)  # asked for
    paid_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)  # credited so far
    seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending", nullable=False)  # pending | credited | failed | expired | refunded
    pay_address: Mapped[str | None] = mapped_column(String(128))
    pay_amount: Mapped[float | None] = mapped_column(Float)
    credited_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class VpnDevice(IdMixin, TimestampMixin, Base):
    """One WireGuard peer an account owns. Public key only."""

    __tablename__ = "vpn_devices"
    __table_args__ = (UniqueConstraint("server_id", "ip", name="uq_vpn_devices_server_ip"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("vpn_accounts.id", ondelete="CASCADE"), index=True, nullable=False)
    server_id: Mapped[int] = mapped_column(ForeignKey("vpn_servers.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)  # without the /32
    last_handshake: Mapped[datetime | None] = mapped_column(UTCDateTime())
    usage_month: Mapped[str] = mapped_column(String(7), default="", server_default="", nullable=False)  # "2026-10"
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)  # this month
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    rx_counter: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)  # the agent's last raw counters
    tx_counter: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    paused_until: Mapped[datetime | None] = mapped_column(UTCDateTime())  # over the monthly cap: peer off the node until then
