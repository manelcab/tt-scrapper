from __future__ import annotations

import hashlib


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def md5_str(text: str) -> str:
    return md5_bytes(text.encode("utf-8"))
