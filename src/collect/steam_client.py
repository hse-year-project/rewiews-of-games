import logging
import time
from urllib.parse import urlsplit

import httpx

from src.config import CONFIG

log = logging.getLogger(__name__)

USER_AGENT = "steam-reviews-research"


class SteamClient:
    def __init__(self):
        self.http = httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self.delay = {host: 1 / rps for host, rps in CONFIG.http.host_rps.items()}
        self.last_request: dict[str, float] = {}
        self.on_request = None

    def get_json(self, url: str, params: dict | None = None):
        host = urlsplit(url).netloc
        for attempt in range(CONFIG.http.max_retries):
            self._wait_turn(host)
            try:
                resp = self.http.get(url, params=params)
            except httpx.TransportError as e:
                problem, pause = type(e).__name__, 2**attempt
            else:
                if resp.status_code == 429:
                    self.delay[host] = min(self.delay.get(host, 1) * 1.5, 10)
                    retry_after = _retry_after(resp)
                    problem = "HTTP 429"
                    pause = retry_after if retry_after is not None else 30 * 2**attempt
                elif resp.status_code >= 500:
                    problem, pause = f"HTTP {resp.status_code}", 2**attempt
                else:
                    resp.raise_for_status()
                    if self.on_request:
                        self.on_request()
                    return resp.json()
            pause = min(pause, 300)
            log.warning("%s from %s, retry in %ds", problem, host, pause)
            time.sleep(pause)
        raise RuntimeError(f"{host}: still failing after {CONFIG.http.max_retries} attempts")

    def _wait_turn(self, host: str):
        next_allowed = self.last_request.get(host, 0) + self.delay.get(host, 1)
        time.sleep(max(0, next_allowed - time.monotonic()))
        self.last_request[host] = time.monotonic()


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None
