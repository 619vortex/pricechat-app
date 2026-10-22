"""USDT payments: network registry, independent verification, providers.

Rules: the buyer picks a network explicitly; each network's USDT is a different token (never interchangeable); a
transaction is only accepted after the SERVER has fetched it from the provider (never because the buyer says so), and
it must match token contract, recipient (the order's own deposit address), exact amount in base units, confirmations
and time; one transaction can settle at most one order (unique network+tx). No seed phrases or private keys, ever.

Only the SimulatedProvider exists today. A live provider must be a regulated payment/escrow partner (see compliance).
"""

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# Tether's published USD₮ contracts (tether.to/supported-protocols). Bridged USDT on other chains is not accepted.
NETWORKS: dict[str, dict[str, object]] = {
    "tron": {"label": "Tron (TRC-20)", "token": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "decimals": 6, "confirmations": 20,
             "fee_estimate": "≈ 1–3 USDT network fee (energy)", "explorer": "https://tronscan.org/#/transaction/{tx}", "wallets": ["TronLink"]},
    "solana": {"label": "Solana (SPL)", "token": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "decimals": 6, "confirmations": 32,
               "fee_estimate": "< 0.01 USD network fee", "explorer": "https://solscan.io/tx/{tx}", "wallets": ["Phantom", "Solflare"]},
    "ethereum": {"label": "Ethereum (ERC-20)", "token": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6, "confirmations": 12,
                 "fee_estimate": "≈ 1–10+ USD network fee (gas)", "explorer": "https://etherscan.io/tx/{tx}", "wallets": ["MetaMask", "WalletConnect"]},
    # TESTNET (no value): Ethereum Sepolia with Circle's test USDC (free at faucet.circle.com). Only for sandbox orders, and
    # only when MARKET_TESTNET_RECEIVE is set. Proves the whole wallet -> chain -> verification path before real money.
    "sepolia": {"label": "Sepolia testnet (test USDC · no value)", "token": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", "decimals": 6,
                "confirmations": 3, "fee_estimate": "free test ETH for gas (Sepolia faucet)", "explorer": "https://sepolia.etherscan.io/tx/{tx}",
                "wallets": ["OKX Wallet", "Binance Wallet", "MetaMask"], "testnet": True, "chain_id": 11155111},
}
TESTNET = "sepolia"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"  # keccak256("Transfer(address,address,uint256)")


def enabled(settings: object) -> dict[str, dict[str, object]]:
    """The networks a buyer can choose on this server: the testnet only when a receiving test wallet is configured."""
    return {k: v for k, v in NETWORKS.items() if not v.get("testnet") or getattr(settings, "market_testnet_receive", None)}


def units(cents: int, network: str) -> str:
    """USDT cents -> exact base units for the network's token (as a string: no float, no overflow)."""
    decimals = int(NETWORKS[network]["decimals"])  # type: ignore[call-overload]
    return str(cents * 10 ** (decimals - 2))


@dataclass
class Transfer:
    network: str
    tx_ref: str
    token: str
    from_address: str | None
    to_address: str
    amount_units: str
    confirmations: int
    at: datetime


class PaymentProvider(Protocol):
    simulated: bool

    def deposit_address(self, network: str, order_ref: str) -> str: ...

    def fetch(self, network: str, tx_ref: str) -> Transfer | None: ...


class SimulatedProvider:
    """Testing only: addresses and transfers are synthetic and clearly marked. Nothing touches a blockchain."""

    simulated = True

    def __init__(self) -> None:
        self._transfers: dict[tuple[str, str], Transfer] = {}

    def deposit_address(self, network: str, order_ref: str) -> str:
        return f"SIM-{network.upper()}-{order_ref}"

    def broadcast(self, t: Transfer) -> None:  # the simulated "chain"
        self._transfers[(t.network, t.tx_ref)] = t

    def fetch(self, network: str, tx_ref: str) -> Transfer | None:
        return self._transfers.get((network, tx_ref))


def new_tx_ref() -> str:
    return "SIM" + secrets.token_hex(16)


def verify(t: Transfer | None, *, network: str, pay_to: str, amount_units: str, created_at: datetime) -> str | None:
    """None when the transfer pays this order exactly; otherwise the reason it doesn't."""
    if t is None:
        return "transaction not found by the provider"
    if t.network != network:
        return "paid on a different network"
    if t.token != NETWORKS[network]["token"]:
        return "not the verified USDT token for this network"
    if t.to_address != pay_to:
        return "sent to a different address"
    if int(t.amount_units) < int(amount_units):
        return "underpaid"
    if t.confirmations < int(NETWORKS[network]["confirmations"]):  # type: ignore[call-overload]
        return "not enough confirmations yet"
    if t.at < created_at:
        return "transaction predates the order"
    return None


class EvmRpcProvider:
    """TESTNET: reads ERC-20 transfers straight from an Ethereum JSON-RPC node (Sepolia). Read-only: no keys, no signing.

    Every order pays the same receiving test wallet, so an order is bound by its exact amount (each order carries a
    unique sub-cent tail), the sender (the buyer's verified wallet, checked by the caller) and the time window."""

    simulated = False

    def __init__(self, rpc_url: str, receive: str, transport: object = None) -> None:
        self.rpc_url, self.receive, self.transport = rpc_url, receive, transport

    def deposit_address(self, network: str, order_ref: str) -> str:
        from app.market import evm

        return evm.checksum(self.receive)

    def _call(self, method: str, params: list[object]) -> object:
        import httpx

        with httpx.Client(timeout=15.0, transport=self.transport) as c:  # type: ignore[arg-type]
            r = c.post(self.rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            r.raise_for_status()
            body = r.json()
        if body.get("error"):
            raise RuntimeError(str(body["error"])[:200])
        return body.get("result")

    def fetch(self, network: str, tx_ref: str) -> Transfer | None:
        """The token transfer in this transaction to the receiving wallet, or None (not mined, failed, or no such transfer)."""
        from datetime import UTC

        from app.market import evm

        if network != TESTNET or not re_tx(tx_ref):
            return None
        rcpt = self._call("eth_getTransactionReceipt", [tx_ref])
        if not isinstance(rcpt, dict) or rcpt.get("status") != "0x1":
            return None
        token = str(NETWORKS[network]["token"]).lower()
        to_topic = "0x" + "0" * 24 + self.receive.lower().removeprefix("0x")
        hits = [lg for lg in rcpt.get("logs", []) if str(lg.get("address", "")).lower() == token and len(lg.get("topics", [])) == 3
                and str(lg["topics"][0]).lower() == TRANSFER_TOPIC and str(lg["topics"][2]).lower() == to_topic]
        if len(hits) != 1:
            return None
        lg = hits[0]
        head = int(str(self._call("eth_blockNumber", [])), 16)
        number = int(str(rcpt["blockNumber"]), 16)
        block = self._call("eth_getBlockByNumber", [rcpt["blockNumber"], False])
        at = datetime.fromtimestamp(int(str(block["timestamp"]), 16), tz=UTC) if isinstance(block, dict) else datetime.now(UTC)  # type: ignore[index]
        return Transfer(network=network, tx_ref=tx_ref.lower(), token=str(NETWORKS[network]["token"]),
                        from_address=evm.checksum("0x" + str(lg["topics"][1])[-40:]), to_address=evm.checksum(self.receive),
                        amount_units=str(int(str(lg["data"]), 16)), confirmations=max(0, head - number + 1), at=at)


def re_tx(tx: str) -> bool:
    import re

    return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", tx or ""))


def order_units(cents: int, network: str, ref: str) -> str:
    """The exact amount to pay. On the testnet each order adds a unique tail under one cent (1..9999 base units), so a
    transfer to the shared receiving wallet matches exactly one order."""
    base = int(units(cents, network))
    if network == TESTNET:
        base += int(ref, 16) % 9999 + 1
    return str(base)
