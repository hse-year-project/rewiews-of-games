import json
import logging
import time
from urllib.parse import urlsplit

import httpx

from src.config import CONFIG

log = logging.getLogger(__name__)

USER_AGENT = "steam-reviews-research"

SPEEDUP = 1.1
SLOWDOWN = 0.7
CALM_SECONDS = 60
CALM_SECONDS_NEAR_CEILING = 600
SAFE_SHARE = 0.9
SLOWDOWN_GAP_SECONDS = 120
MIN_RPS = 0.1


class SteamClient:
    def __init__(self):
        self.http = httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=False
        )
        self.rate = dict(CONFIG.http.host_rps)
        self.ceiling: dict[str, float] = {}
        self.calm_since: dict[str, float] = {}
        self.last_slowdown: dict[str, float] = {}
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
                if resp.status_code == 429 or resp.is_redirect:
                    self._slow_down(host)
                    retry_after = _retry_after(resp)
                    problem = f"HTTP {resp.status_code}"
                    pause = retry_after if retry_after is not None else 30 * 2**attempt
                elif resp.status_code >= 500:
                    problem, pause = f"HTTP {resp.status_code}", 2**attempt
                else:
                    resp.raise_for_status()
                    self._speed_up(host)
                    if self.on_request:
                        self.on_request(host)
                    # Steam may cut a review mid-letter (invalid UTF-8): that letter becomes �
                    return json.loads(resp.content.decode("utf-8", errors="replace"))
            pause = min(pause, 300)
            log.warning("%s from %s, retry in %ds", problem, host, pause)
            time.sleep(pause)
        raise RuntimeError(f"{host}: still failing after {CONFIG.http.max_retries} attempts")

    def _speed_up(self, host: str):
        now = time.monotonic()
        calm_since = self.calm_since.setdefault(host, now)
        rate, ceiling = self.rate.get(host, 1.0), self.ceiling.get(host)
        near_ceiling = ceiling is not None and rate * SPEEDUP > ceiling * SAFE_SHARE
        if now - calm_since < (CALM_SECONDS_NEAR_CEILING if near_ceiling else CALM_SECONDS):
            return
        self.calm_since[host] = now
        new_rate = min(rate * SPEEDUP, CONFIG.http.max_rps)
        if new_rate > rate:
            self.rate[host] = new_rate
            log.info("%s: speeding up to %.2f req/s", host, new_rate)

    def _slow_down(self, host: str):
        now = time.monotonic()
        self.calm_since[host] = now
        if now - self.last_slowdown.get(host, float("-inf")) < SLOWDOWN_GAP_SECONDS:
            return
        self.last_slowdown[host] = now
        rate = self.rate.get(host, 1.0)
        self.ceiling[host] = rate
        self.rate[host] = max(rate * SLOWDOWN, MIN_RPS)
        log.warning("429 from %s at %.2f req/s, slowing down to %.2f", host, rate, self.rate[host])

    def _wait_turn(self, host: str):
        next_allowed = self.last_request.get(host, 0) + 1 / self.rate.get(host, 1.0)
        time.sleep(max(0, next_allowed - time.monotonic()))
        self.last_request[host] = time.monotonic()


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None
