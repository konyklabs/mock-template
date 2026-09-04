"""Unpadded base64url for opaque wire tokens: encoding never emits ``=`` padding, and decoding tolerates its absence."""

from __future__ import annotations

import base64

__all__ = ["b64url_decode", "b64url_encode"]


def b64url_encode(data: bytes) -> str:
    """base64url of ``data``, with the ``=`` padding stripped."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode unpadded (or padded) base64url; raises ``binascii.Error`` on bad input."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
