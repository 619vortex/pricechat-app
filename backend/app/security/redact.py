"""Keeps private keys and recovery phrases out of the logs.

SecretRedactor sits on every root log handler (app.config.logging): before a line is written, anything shaped like a private
key (64 hex digits, with or without 0x) is replaced, a run of 12 or more BIP-39 words (a recovery phrase) hides the whole
message, and Telegram bot tokens are masked as before (services.activity.redact_text). Exception tracebacks get the same
treatment. Transaction hashes have the same shape as a key and are masked too: a small price for never logging a key.
"""

import functools
import logging
import re

REDACTED_KEY = "[redacted-key]"
REDACTED_PHRASE = "[redacted: looked like a recovery phrase]"
_HEX64 = re.compile(r"(?<![0-9a-fA-F])(?:0x)?[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_WORD = re.compile(r"[A-Za-z]+")


@functools.cache
def _bip39() -> frozenset[str]:
    from importlib import resources

    try:
        text = resources.files("eth_account.hdaccount").joinpath("wordlist/english.txt").read_text(encoding="utf-8")
        return frozenset(w.strip() for w in text.split() if w.strip())
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return frozenset()


def has_phrase(text: str, run: int = 12) -> bool:
    words = _bip39()
    if not words or not text:
        return False
    n = 0
    for t in _WORD.findall(text.lower()):
        n = n + 1 if t in words else 0
        if n >= run:
            return True
    return False


def redact_secrets(text: str) -> str:
    if not text:
        return text
    from app.services.activity import redact_text

    if has_phrase(text):
        return REDACTED_PHRASE
    return redact_text(_HEX64.sub(REDACTED_KEY, text))


class SecretRedactor(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed record is left to the handler's own error path
            return True
        clean = redact_secrets(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_secrets(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


def install(logger: logging.Logger | None = None) -> None:
    """Adds the redactor to every handler of `logger` (the root logger by default), once."""
    for h in (logger or logging.getLogger()).handlers:
        if not any(isinstance(f, SecretRedactor) for f in h.filters):
            h.addFilter(SecretRedactor())
