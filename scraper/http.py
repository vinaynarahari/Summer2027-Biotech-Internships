"""Tiny stdlib HTTP helpers (no third-party deps so Actions runs need no install)."""

from __future__ import annotations

import json
import sys
import threading
import time
from urllib.parse import urlparse
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; BiotechInternshipsBot/1.0; +https://github.com)"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


# Per-host concurrency caps: some APIs (Workable) rate-limit hard on parallel requests.
HOST_LIMITS = {"apply.workable.com": 1}
_host_sems: dict[str, threading.Semaphore] = {}
_sem_lock = threading.Lock()


def _host_sem(url: str) -> threading.Semaphore:
    host = urlparse(url).netloc
    with _sem_lock:
        if host not in _host_sems:
            _host_sems[host] = threading.Semaphore(HOST_LIMITS.get(host, 8))
        return _host_sems[host]


def _request(req: urllib.request.Request, quiet: bool, retries: int = 2, timeout: int = 25, raw: bool = False):
    with _host_sem(req.full_url):
        return _request_inner(req, quiet, retries, timeout, raw)


def _request_inner(req: urllib.request.Request, quiet: bool, retries: int, timeout: int, raw: bool):
    for attempt in range(retries + 3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries + 2:
                time.sleep(5 * (attempt + 1))
                continue
            if e.code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            if not quiet:
                print(f"  ! HTTP {e.code} {req.full_url}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001 - network is messy
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            if not quiet:
                print(f"  ! {type(e).__name__} {req.full_url}: {e}", file=sys.stderr)
            return None


def get_json(url: str, quiet: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    return _request(req, quiet)


def post_json(url: str, body: dict, quiet: bool = False):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    return _request(req, quiet, retries=1 if quiet else 2)


def get_text(url: str, quiet: bool = False, headers: dict | None = None) -> str | None:
    """Fetch an HTML page (career sites that render jobs server-side)."""
    h = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/json"}
    h.update(headers or {})
    return _request(urllib.request.Request(url, headers=h), quiet, raw=True)


def get_json_browser(url: str, quiet: bool = False, headers: dict | None = None):
    h = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
    h.update(headers or {})
    return _request(urllib.request.Request(url, headers=h), quiet)
