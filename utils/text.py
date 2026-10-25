from __future__ import annotations

import re
import unicodedata


def slugify(text: str) -> str:
    """Normalize NFKD → ascii → lowercase → underscores."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s\-]+", "_", ascii_text.lower()).strip("_")
