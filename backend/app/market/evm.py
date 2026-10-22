"""Ethereum-compatible (EVM) primitives for wallet sign-in and payment checks, in plain Python (no native modules).

  - keccak256: the original Keccak (Ethereum's hash; NOT hashlib's sha3_256, which pads differently)
  - personal_sign recovery (EIP-191): which address signed a text message (OKX / Binance / MetaMask "sign message")
  - EIP-55 checksummed addresses

Only VERIFICATION lives here: the server never holds a private key and never signs anything. Checked against published
test vectors in tests/test_market_wallets.py.
"""

import re

# --- keccak-256 ---------------------------------------------------------------------------------------------------------

_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
       0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
       0x000000000000800A, 0x800000008000000A, 0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(v: int, n: int) -> int:
    return ((v << n) | (v >> (64 - n))) & _M if n else v


def _f(a: list[list[int]]) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(rate // 8))
    return out[:32]


# --- secp256k1 recovery -------------------------------------------------------------------------------------------------

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798, 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)

Point = tuple[int, int] | None


def _add(p: Point, q: Point) -> Point:
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % P == 0:
        return None
    if p == q:
        lam = 3 * p[0] * p[0] * pow(2 * p[1], -1, P) % P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, P) % P
    x = (lam * lam - p[0] - q[0]) % P
    return x, (lam * (p[0] - x) - p[1]) % P


def _mul(k: int, p: Point) -> Point:
    r: Point = None
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def address_of(pub: tuple[int, int]) -> str:
    return checksum("0x" + keccak256(pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big"))[-20:].hex())


def recover(msg_hash: bytes, signature: bytes) -> str:
    """The address whose key made this 65-byte (r, s, v) signature of a 32-byte hash. Raises ValueError if invalid."""
    if len(signature) != 65 or len(msg_hash) != 32:
        raise ValueError("bad signature length")
    r, s, v = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:64], "big"), signature[64]
    if v >= 27:
        v -= 27
    if v not in (0, 1) or not (1 <= r < N and 1 <= s < N) or s > N // 2:  # low-s only (EIP-2): no malleable twins
        raise ValueError("bad signature values")
    y2 = (pow(r, 3, P) + 7) % P
    y = pow(y2, (P + 1) // 4, P)
    if y * y % P != y2:
        raise ValueError("r is not on the curve")
    if y % 2 != v:
        y = P - y
    e = int.from_bytes(msg_hash, "big")
    r_inv = pow(r, -1, N)
    q = _add(_mul(s * r_inv % N, (r, y)), _mul((-e * r_inv) % N, G))
    if q is None:
        raise ValueError("no public key")
    return address_of(q)


def personal_hash(message: str) -> bytes:
    """EIP-191 personal_sign: what a wallet actually signs for a text message."""
    data = message.encode("utf-8")
    return keccak256(b"\x19Ethereum Signed Message:\n" + str(len(data)).encode() + data)


def recover_personal(message: str, signature_hex: str) -> str:
    sig = bytes.fromhex(signature_hex.removeprefix("0x"))
    return recover(personal_hash(message), sig)


# --- addresses ----------------------------------------------------------------------------------------------------------

_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_address(a: str) -> bool:
    return bool(_ADDR.match(a or ""))


def checksum(a: str) -> str:
    """EIP-55 mixed-case address."""
    h = a.lower().removeprefix("0x")
    k = keccak256(h.encode()).hex()
    return "0x" + "".join(c.upper() if c.isalpha() and int(k[i], 16) >= 8 else c for i, c in enumerate(h))
