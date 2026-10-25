from __future__ import annotations

import time
from typing import Optional

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {
    "User-Agent": "TT-Agent-Scraper/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_MAX_RETRIES = 3
_BACKOFF_SECONDS = [1, 2, 4]
_RATE_LIMIT_SLEEP = 1.0


class HttpClient:
    def __init__(self, rate_limit_seconds: float = _RATE_LIMIT_SLEEP) -> None:
        self._client = httpx.Client(
            headers=_HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )
        # Algunas fuentes (fctt.cat) empiezan a servir páginas vacías con HTTP
        # 200 cuando se las castiga; se les da un intervalo mayor.
        self._rate_limit = rate_limit_seconds

    def fetch(self, url: str) -> bytes:
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                if attempt > 0:
                    wait = _BACKOFF_SECONDS[attempt - 1]
                    logger.warning(
                        f"Retry {attempt}/{_MAX_RETRIES - 1} for {url} — waiting {wait}s"
                    )
                    time.sleep(wait)
                time.sleep(self._rate_limit)
                logger.debug(f"GET {url}")
                response = self._client.get(url)
                response.raise_for_status()
                return response.content
            except httpx.HTTPStatusError as exc:
                logger.warning(f"HTTP {exc.response.status_code} for {url}")
                last_exc = exc
            except httpx.RequestError as exc:
                logger.warning(f"Request error for {url}: {exc}")
                last_exc = exc

        raise RuntimeError(
            f"Failed to fetch {url} after {_MAX_RETRIES} attempts"
        ) from last_exc

    def fetch_text(self, url: str) -> str:
        data = self.fetch(url)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()

    def close(self) -> None:
        self._client.close()
