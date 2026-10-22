"""Who signed this message? Ethereum `personal_sign` (EIP-191) signature recovery, in pure Python (no native deps).

Keccak-256 (Ethereum's hash, not NIST SHA3-256) and secp256k1 public-key recovery. Only *verification* lives here:
Price Chat never holds or asks for a private key. Checked against known vectors in tests/test_trading_bot.py.
Smart-contract wallets (EIP-1271) are not supported: they have no key to recover; such members go to manual review.
"""

import re

# ---------------------------------------------------------------- Keccak-256
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
       0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
       0x000000000000800A, 0x800000008000000A, 0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _f1600(a: list[list[int]]) -> None:
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
                a[x][y] = b[x][y] ^ (~b[(x + 1) % 5][y] & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data) + b"\x01"  # original Keccak padding (SHA3 uses 0x06)
    msg += b"\x00" * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _f1600(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(rate // 8))
    return out[:32]


# ---------------------------------------------------------------- secp256k1
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798, 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)
Point = tuple[int, int] | None


def _add(p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    if p1[0] == p2[0] and (p1[1] + p2[1]) % P == 0:
        return None
    if p1 == p2:
        lam = 3 * p1[0] * p1[0] * pow(2 * p1[1], -1, P) % P
    else:
        lam = (p2[1] - p1[1]) * pow(p2[0] - p1[0], -1, P) % P
    x = (lam * lam - p1[0] - p2[0]) % P
    return x, (lam * (p1[0] - x) - p1[1]) % P


def mul(k: int, pt: Point = G) -> Point:
    out: Point = None
    while k:
        if k & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        k >>= 1
    return out


def address_of(pub: tuple[int, int]) -> str:
    return "0x" + keccak256(pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big"))[-20:].hex()


def eip191_hash(message: str) -> bytes:
    m = message.encode("utf-8")
    return keccak256(b"\x19Ethereum Signed Message:\n" + str(len(m)).encode() + m)


def recover(message: str, signature: str) -> str:
    """The lower-case address that signed `message` with personal_sign. ValueError if the signature is malformed."""
    sig = signature.lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{130}", sig):
        raise ValueError("A wallet signature is 65 bytes of hex.")
    r, s, v = int(sig[:64], 16), int(sig[64:128], 16), int(sig[128:], 16)
    v = v - 27 if v >= 27 else v
    if v not in (0, 1) or not (0 < r < N) or not (0 < s < N):
        raise ValueError("That signature isn't valid.")
    if s > N // 2:
        raise ValueError("That signature isn't in canonical form.")  # no malleable twins
    z = int.from_bytes(eip191_hash(message), "big")
    y2 = (pow(r, 3, P) + 7) % P
    y = pow(y2, (P + 1) // 4, P)
    if y * y % P != y2:
        raise ValueError("That signature isn't valid.")
    if y & 1 != v:
        y = P - y
    rinv = pow(r, -1, N)
    q = _add(mul(s * rinv % N, (r, y)), mul((-z * rinv) % N))
    if q is None:
        raise ValueError("That signature isn't valid.")
    return address_of(q)


def checksum(address: str) -> str:
    """EIP-55 mixed-case form, for display."""
    a = address.lower().removeprefix("0x")
    h = keccak256(a.encode()).hex()
    return "0x" + "".join(c.upper() if c.isalpha() and int(h[i], 16) >= 8 else c for i, c in enumerate(a))


def normalize(address: str) -> str:
    a = (address or "").strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", a):
        raise ValueError("That isn't an Ethereum address.")
    return a
