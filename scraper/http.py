"""Tiny stdlib HTTP helpers (no third-party deps so Actions runs need no install)."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; BiotechInternshipsBot/1.0; +https://github.com)"


def _request(req: urllib.request.Request, quiet: bool, retries: int = 2, timeout: int = 25):
    for attempt in range(retries + 3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
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
