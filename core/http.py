"""Polite HTTP client: honest User-Agent, robots.txt, per-host rate limit, retries with
backoff, and a permanent on-disk cache so nothing is downloaded twice."""
from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.config import load_config
from core.log import get_logger

log = get_logger("http")

RETRY_STATUS = {403, 429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


class Blocked(Exception):
    """403/429/5xx that survived all retries. Never cached, never treated as 'no data'."""

    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status


class PoliteClient:
    def __init__(self, cache_root: Path, user_agent: str, min_interval_s: float = 1.0,
                 cache_subdir: str = "html", extra_headers: dict | None = None):
        s = load_config()["http"]
        self.min_interval = min_interval_s
        self.max_retries = s["max_retries"]
        self.backoff = s["backoff_base_s"]
        self.respect_robots = s["respect_robots"]
        self.cache_dir: Path = Path(cache_root) / cache_subdir
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, **(extra_headers or {})},
            timeout=s["timeout_s"],
            follow_redirects=True,
        )
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.n_network = 0
        self.n_cache = 0

    # ---------- cache ----------
    def cache_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        host = urlparse(url).netloc
        return self.cache_dir / host / h[:2] / f"{h}.gz"

    def cached(self, url: str) -> bool:
        return self.cache_path(url).exists()

    def _read_cache(self, url: str) -> tuple[int, bytes]:
        raw = gzip.decompress(self.cache_path(url).read_bytes())
        header, _, body = raw.partition(b"\n")
        return json.loads(header)["status"], body

    def _write_cache(self, url: str, status: int, body: bytes) -> None:
        p = self.cache_path(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(json.dumps({"url": url, "status": status}).encode() + b"\n" + body))
        tmp.replace(p)  # atomic: a crash never leaves a half-written cache entry

    # ---------- politeness ----------
    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        u = urlparse(url)
        base = f"{u.scheme}://{u.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(base + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base].can_fetch(self.client.headers["User-Agent"], url)

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            dt = time.monotonic() - last
            if dt < self.min_interval:
                time.sleep(self.min_interval - dt)
        self._last[host] = time.monotonic()

    # ---------- public ----------
    def get(self, url: str, use_cache: bool = True) -> tuple[int, bytes]:
        """Return (status, body). 404s are cached too (as status 404) so we don't retry them."""
        if use_cache and self.cached(url):
            self.n_cache += 1
            return self._read_cache(url)
        if not self._allowed(url):
            raise RobotsDisallowed(url)
        host = urlparse(url).netloc
        for attempt in range(self.max_retries + 1):
            self._wait(host)
            try:
                r = self.client.get(url)
            except httpx.HTTPError as e:
                if attempt == self.max_retries:
                    raise
                sleep = self.backoff * 2**attempt
                log.warning("network error %s on %s; retry in %.0fs", e, url, sleep)
                time.sleep(sleep)
                continue
            self.n_network += 1
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                ra = r.headers.get("Retry-After")
                sleep = float(ra) if ra and ra.isdigit() else self.backoff * 2**attempt
                log.warning("HTTP %s on %s; retry in %.0fs", r.status_code, url, sleep)
                time.sleep(sleep)
                continue
            if r.status_code in (200, 404, 410):
                self._write_cache(url, r.status_code, r.content)
                return r.status_code, r.content
            if r.status_code in (401, 403, 429) or r.status_code >= 500:
                log.error("BLOCKED? HTTP %s on %s", r.status_code, url)
                raise Blocked(r.status_code, url)
            return r.status_code, r.content
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self.client.close()
