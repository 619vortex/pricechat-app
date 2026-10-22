"""Wallet linking for the marketplace (EVM: OKX Wallet, Binance Web3 Wallet, MetaMask, Trust…).

A member proves a wallet is theirs by signing a one-time, human-readable message in the wallet (EIP-191 personal_sign).
The server recovers the signer from the signature and links the address only if it matches. Signing is free, sends no
transaction and grants no access to funds. Nothing secret is ever asked for or stored: only the public address.

What a linked wallet is for (before real money): it's where a seller's proceeds are paid, and the wallet a buyer pays
from in testnet mode, so a payment can be tied to the member who made it.
"""

import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.market import evm
from app.market.service import audit
from app.members.service import MemberError
from app.models.market import MarketWallet, MarketWalletChallenge
from app.models.members import Member

CHALLENGE_MINUTES = 10
MAX_WALLETS = 5
CHALLENGES_PER_HOUR = 20


def wallet_out(w: MarketWallet) -> dict[str, Any]:
    return {"id": w.id, "chain": w.chain, "address": w.address, "short": f"{w.address[:6]}…{w.address[-4:]}", "wallet": w.wallet,
            "verified_at": w.verified_at.isoformat(), "payout": w.payout}


def mine(db: Session, member: Member) -> list[dict[str, Any]]:
    return [wallet_out(w) for w in db.scalars(select(MarketWallet).where(MarketWallet.member_id == member.id).order_by(MarketWallet.id))]


def payout_wallet(db: Session, member_id: int) -> MarketWallet | None:
    return db.scalar(select(MarketWallet).where(MarketWallet.member_id == member_id, MarketWallet.payout.is_(True)))


def challenge(db: Session, member: Member, address: str) -> dict[str, Any]:
    """The exact text the wallet will show and sign."""
    if not evm.is_address(address):
        raise MemberError("That isn't a wallet address.")
    since = utcnow() - timedelta(hours=1)
    if (db.scalar(select(func.count()).select_from(MarketWalletChallenge).where(MarketWalletChallenge.member_id == member.id,
                                                                                 MarketWalletChallenge.expires_at >= since)) or 0) >= CHALLENGES_PER_HOUR:
        raise MemberError("Too many wallet checks in the last hour. Try again later.")
    address = evm.checksum(address)
    now = utcnow()
    message = (f"Price Chat: confirm this wallet is yours.\n\nWallet: {address}\nAccount: @{member.username}\nNonce: {secrets.token_hex(12)}\n"
               f"Issued: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC\n\nSigning is free. It doesn't send a transaction or give access to your funds.")
    db.add(MarketWalletChallenge(member_id=member.id, address=address, message=message, expires_at=now + timedelta(minutes=CHALLENGE_MINUTES)))
    db.commit()
    return {"address": address, "message": message, "expires_in": CHALLENGE_MINUTES * 60}


def verify(db: Session, member: Member, address: str, signature: str, wallet: str = "") -> dict[str, Any]:
    """Link the wallet if the signature over the latest open challenge was made by that address."""
    if not evm.is_address(address):
        raise MemberError("That isn't a wallet address.")
    address = evm.checksum(address)
    c = db.scalar(select(MarketWalletChallenge).where(MarketWalletChallenge.member_id == member.id, MarketWalletChallenge.address == address,
                                                       MarketWalletChallenge.used_at.is_(None), MarketWalletChallenge.expires_at >= utcnow())
                  .order_by(MarketWalletChallenge.id.desc()))
    if c is None:
        raise MemberError("That confirmation expired. Connect the wallet again.")
    c.used_at = utcnow()  # single use, whatever happens next
    try:
        signer = evm.recover_personal(c.message, signature)
    except ValueError:
        db.commit()
        raise MemberError("The wallet's signature couldn't be read. Try again.") from None
    if signer != address:
        db.commit()
        raise MemberError("The signature came from a different wallet than the one connected.")
    owner = db.scalar(select(MarketWallet).where(MarketWallet.chain == "evm", MarketWallet.address == address))
    if owner is not None and owner.member_id != member.id:
        audit(db, f"member:{member.id}", "wallet_claimed_by_other", f"wallet:{owner.id}")
        db.commit()
        raise MemberError("This wallet is already linked to another Price Chat account.")
    if owner is None:
        if (db.scalar(select(func.count()).select_from(MarketWallet).where(MarketWallet.member_id == member.id)) or 0) >= MAX_WALLETS:
            db.commit()
            raise MemberError(f"Up to {MAX_WALLETS} wallets per account. Remove one first.")
        first = payout_wallet(db, member.id) is None
        owner = MarketWallet(member_id=member.id, chain="evm", address=address, wallet=wallet.strip()[:40], verified_at=utcnow(), payout=first)
        db.add(owner)
    else:
        owner.verified_at, owner.wallet = utcnow(), wallet.strip()[:40] or owner.wallet
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise MemberError("This wallet is already linked to another Price Chat account.") from None
    audit(db, f"member:{member.id}", "wallet_linked", f"wallet:{owner.id}", address=address, wallet=owner.wallet)
    db.commit()
    return wallet_out(owner)


def set_payout(db: Session, member: Member, wallet_id: int) -> list[dict[str, Any]]:
    w = db.get(MarketWallet, wallet_id)
    if w is None or w.member_id != member.id:
        raise MemberError("Not found.")
    for other in db.scalars(select(MarketWallet).where(MarketWallet.member_id == member.id)):
        other.payout = other.id == w.id
    audit(db, f"member:{member.id}", "payout_wallet_set", f"wallet:{w.id}", address=w.address)
    db.commit()
    return mine(db, member)


def remove(db: Session, member: Member, wallet_id: int) -> list[dict[str, Any]]:
    w = db.get(MarketWallet, wallet_id)
    if w is None or w.member_id != member.id:
        raise MemberError("Not found.")
    was_payout = w.payout
    audit(db, f"member:{member.id}", "wallet_removed", f"wallet:{w.id}", address=w.address)
    db.delete(w)
    db.flush()
    if was_payout:
        nxt = db.scalar(select(MarketWallet).where(MarketWallet.member_id == member.id).order_by(MarketWallet.id))
        if nxt is not None:
            nxt.payout = True
    db.commit()
    return mine(db, member)
