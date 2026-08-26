"""Unpadded base64url, the way every wire format in this project spells it.

FOR: encoding opaque tokens -- pagination cursors today, stored idempotent
response bodies next -- as text that survives a URL, a query string and a JSON
document without escaping.

INVARIANT: **encoding never emits ``=`` padding, and decoding always tolerates
its absence.** The reference produces cursors with Node's
``Buffer.toString('base64url')``, which is unpadded by specification (RFC 4648
section 5 makes padding optional and Node omits it). Python's
``base64.urlsafe_b64encode`` pads. A port that kept the padding would emit
``eyJvIjoyfQ==`` where the oracle emits ``eyJvIjoyfQ``, and every cursor this
project produces would stop being byte-comparable against the oracle's -- for a
value whose whole job is to be handed back verbatim.

Decoding is deliberately lax about characters outside the alphabet, matching
``Buffer.from(s, 'base64url')``, which ignores them rather than raising. The
caller is expected to validate what comes *out*: a cursor is attacker-supplied
text, and the parse of the decoded bytes is where a forgery is caught. Being
strict here would only move the same rejection one layer earlier while
diverging from the oracle on inputs that are rejected either way.
"""

from __future__ import annotations

import base64

__all__ = ["b64url_decode", "b64url_encode"]


def b64url_encode(data: bytes) -> str:
    """base64url of ``data``, with the ``=`` padding stripped."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode unpadded (or padded) base64url.

    Raises ``binascii.Error`` when the input cannot be decoded at all. Callers
    that accept untrusted text catch it and answer with their own error.
    """
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
