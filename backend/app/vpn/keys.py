"""WireGuard keys (X25519, base64) and the wg-quick config a member imports into the official WireGuard app."""

import base64
import binascii
import ipaddress
import re

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


def generate_keypair() -> tuple[str, str]:
    """(private, public) as WireGuard base64. The private key must only ever go into the config returned once."""
    priv = X25519PrivateKey.generate()
    raw_priv = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    raw_pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw_priv).decode(), base64.b64encode(raw_pub).decode()


def public_from_private(private_b64: str) -> str:
    raw = base64.b64decode(private_b64)
    pub = X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(pub).decode()


def is_key(value: str) -> bool:
    """A WireGuard key: 44 base64 characters decoding to 32 bytes."""
    if not isinstance(value, str) or len(value) != 44:
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def url_key(public_key: str) -> str:
    """The key as it goes in an agent URL: `+` -> `-`, `/` -> `_` (the `=` stays)."""
    return public_key.replace("+", "-").replace("/", "_")


def _slug(text: str, limit: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].strip("-") or "x"


def filename(server_name: str, device_name: str) -> str:
    """kiwi-vpn-<server>-<name>.conf; the stem stays within 32 characters (the WireGuard apps' tunnel-name limit)."""
    server = _slug(server_name, 10)
    name = _slug(device_name, 32 - len("kiwi-vpn-") - len(server) - 1)
    return f"kiwi-vpn-{server}-{name}.conf"


def ipv6_for(pool4: str, ip: str, pool6: str | None) -> str | None:
    """The device's IPv6: the same offset in the server's v6 pool as its IPv4 has in the v4 pool (10.66.0.0/16 -> 10.66.1.2
    is offset 258 -> fd66:66::102 in fd66:66::/64). None without a (valid) v6 pool or when it wouldn't fit."""
    if not pool6:
        return None
    try:
        net4, net6 = ipaddress.ip_interface(pool4).network, ipaddress.ip_interface(pool6).network
        offset = int(ipaddress.ip_address(ip)) - int(net4.network_address)
    except ValueError:
        return None
    if net6.version != 6 or not 0 < offset < net6.num_addresses:
        return None
    return str(net6.network_address + offset)


def render_config(private_key: str, ip: str, dns: list[str], server_public_key: str, endpoint_host: str, listen_port: int,
                  ip6: str | None = None) -> str:
    host = endpoint_host
    try:
        if ipaddress.ip_address(host).version == 6:
            host = f"[{host}]"
    except ValueError:
        pass  # a hostname
    lines = ["[Interface]", f"PrivateKey = {private_key}", f"Address = {ip}/32, {ip6}/128" if ip6 else f"Address = {ip}/32"]
    if dns:
        lines.append(f"DNS = {', '.join(dns)}")
    lines += ["", "[Peer]", f"PublicKey = {server_public_key}", f"Endpoint = {host}:{listen_port}",
              "AllowedIPs = 0.0.0.0/0, ::/0", "PersistentKeepalive = 25", ""]
    return "\n".join(lines)
