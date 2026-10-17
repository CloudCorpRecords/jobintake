"""Polite, resilient HTTP: retries with backoff + jitter, honours Retry-After, per-host throttling,
gzip, size caps, and a clear split between permanent and transient failures."""

import gzip
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .util import debug

USER_AGENT = "Mozilla/5.0 (compatible; jobintake/2.0; personal job research)"
MAX_BYTES = 60 * 1024 * 1024
DEFAULT_TIMEOUT = 30          # max seconds of silence on the socket
MAX_REQUEST_SECONDS = 120     # max total seconds for one request, even if the server trickles data
MIN_HOST_INTERVAL = 0.35  # seconds between requests to the same host
HOST_INTERVAL = {"jobs.workable.com": 1.5, "apply.workable.com": 1.0}  # hosts that rate-limit aggressively
MAX_RETRY_AFTER = 180  # a host asking us to wait longer than this is treated as "blocked for now"


STOP = threading.Event()  # set on Ctrl-C: in-flight waits end immediately and no new requests start


class Interrupted(Exception):
    pass


class RateLimited(Exception):
    """The host told us to come back much later (Retry-After > MAX_RETRY_AFTER)."""


class OutOfTime(Exception):
    """The calling source's time budget ran out (set per thread with set_deadline)."""


_local = threading.local()


def set_deadline(monotonic_deadline):
    _local.deadline = monotonic_deadline


def _remaining():
    d = getattr(_local, "deadline", None)
    return None if d is None else d - time.monotonic()


def _sleep(seconds):
    rem = _remaining()
    if rem is not None and seconds >= rem:
        raise OutOfTime()
    if STOP.wait(seconds):
        raise Interrupted()


class PermanentError(Exception):
    """4xx (except 408/429) — retrying won't help (wrong token, removed endpoint, bad request)."""

    def __init__(self, code, url):
        super().__init__("HTTP %s for %s" % (code, url))
        self.code = code


class TransientError(Exception):
    """Gave up after retries on timeouts, 5xx, 429 or network errors."""


_host_lock = threading.Lock()
_host_next = {}
_stats = {"requests": 0, "retries": 0, "bytes": 0}


def stats():
    return dict(_stats)


def _throttle(url):
    host = urllib.parse.urlsplit(url).netloc
    with _host_lock:
        now = time.monotonic()
        wait = _host_next.get(host, 0) - now
        _host_next[host] = max(now, _host_next.get(host, 0)) + HOST_INTERVAL.get(host, MIN_HOST_INTERVAL)
    if wait > 0:
        _sleep(wait)


def _retry_after(err):
    v = err.headers.get("Retry-After") if getattr(err, "headers", None) else None
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fetch(url, headers=None, retries=4, timeout=DEFAULT_TIMEOUT, data=None):
    """Return the response body as text."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json, application/xml;q=0.9, */*;q=0.8",
         "Accept-Encoding": "gzip, deflate"}
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries + 1):
        if STOP.is_set():
            raise Interrupted()
        rem = _remaining()
        if rem is not None and rem <= 0:
            raise OutOfTime()
        _throttle(url)
        _stats["requests"] += 1
        try:
            req = urllib.request.Request(url, headers=h, data=data)
            rem = _remaining()
            started = time.monotonic()
            with urllib.request.urlopen(req, timeout=max(5, min(timeout, rem)) if rem else timeout) as r:
                chunks, size = [], 0
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise PermanentError("too-large", url)
                    if time.monotonic() - started > MAX_REQUEST_SECONDS:
                        raise TimeoutError("response took over %ss" % MAX_REQUEST_SECONDS)
                    if STOP.is_set():
                        raise Interrupted()
                raw = b"".join(chunks)
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                elif enc == "deflate":
                    raw = zlib.decompress(raw)
                _stats["bytes"] += len(raw)
                charset = r.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except urllib.error.HTTPError as e:
            if e.code not in (408, 425, 429, 500, 502, 503, 504):
                raise PermanentError(e.code, url)
            last = e
            delay = _retry_after(e)
            if delay is not None and delay > MAX_RETRY_AFTER:
                host = urllib.parse.urlsplit(url).netloc
                raise RateLimited("%s is rate-limiting this machine; it asks to wait %s" % (host, _human(delay)))
            if delay is None and e.code == 429:
                delay = min(60.0, 5 * (2 ** attempt)) + random.uniform(0, 2)  # rate-limited: back off harder
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            delay = None
        if attempt < retries:
            _stats["retries"] += 1
            delay = delay if delay is not None else min(30.0, (2 ** attempt) + random.uniform(0, 1))
            debug("retry %d in %.1fs: %s (%s)" % (attempt + 1, delay, url[:90], last))
            _sleep(delay)
    cause = "HTTP %s" % last.code if isinstance(last, urllib.error.HTTPError) else str(last)[:80]
    raise TransientError("%s after %d attempts (%s)" % (cause, retries + 1, urllib.parse.urlsplit(url).netloc))


def _human(sec):
    return "%.0f min" % (sec / 60) if sec < 5400 else "%.1f h" % (sec / 3600)


def get_json(url, headers=None, **kw):
    text = fetch(url, headers=headers, **kw)
    try:
        return json.loads(text)
    except ValueError as e:
        snippet = text[:120].replace("\n", " ")
        raise TransientError("invalid JSON from %s: %s (%s…)" % (url[:120], e, snippet))
