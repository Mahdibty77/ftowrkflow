"""Cross-worker invalidation for the in-process reference-data caches.

The coding/pricing engine keeps reference data (JSON configs, code tables, size
tables, calculation lookups) in per-process caches for speed. With several
gunicorn workers, an admin editing data in one worker used to leave the *other*
workers serving stale data until the next restart, because ``_clear_caches()``
only emptied the caches of the worker that handled the edit.

All workers of a container share its filesystem, so a tiny sentinel file is
enough to broadcast "reference data changed" to every worker:

* the editing worker calls :func:`bump_epoch` right after clearing its caches;
* every read path calls :func:`maybe_refresh`, which (at most once every couple
  of seconds) checks the sentinel and, if it changed, drops this worker's caches
  too so the next read reloads the fresh data.

This changes no business logic or output — only *when* stale caches are dropped.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time

# Shared by every worker in the same container (same /tmp).
_SENTINEL = os.path.join(tempfile.gettempdir(), "ftworkflow_itemcoder_cache_epoch")

_LOCK = threading.Lock()
_LAST_SEEN = None       # epoch token this worker has already synced to
_LAST_CHECK = 0.0       # monotonic time of the last sentinel read (throttle)
_CHECK_INTERVAL = 2.0   # seconds between sentinel reads per worker


def _read_epoch() -> str:
    try:
        with open(_SENTINEL, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def bump_epoch() -> None:
    """Announce that reference data changed (call after clearing local caches)."""
    global _LAST_SEEN
    try:
        token = f"{time.time_ns()}:{os.getpid()}"
        with open(_SENTINEL, "w", encoding="utf-8") as fh:
            fh.write(token)
        # This worker already cleared its own caches, so mark it as up to date.
        _LAST_SEEN = token
    except Exception:
        pass


def maybe_refresh() -> None:
    """Drop this worker's caches if another worker bumped the epoch."""
    global _LAST_SEEN, _LAST_CHECK
    now = time.monotonic()
    if now - _LAST_CHECK < _CHECK_INTERVAL:
        return
    _LAST_CHECK = now
    epoch = _read_epoch()
    if not epoch or epoch == _LAST_SEEN:
        return
    with _LOCK:
        if epoch == _LAST_SEEN:
            return
        _drop_local_caches()
        _LAST_SEEN = epoch


def _drop_local_caches() -> None:
    try:
        from . import constants
        constants.clear_data_caches()
    except Exception:
        pass
    try:
        from . import item_builder
        item_builder.clear_builder_caches()
    except Exception:
        pass
    try:
        from . import code_db
        for con in list(getattr(code_db, "_CONN_CACHE", {}).values()):
            try:
                con.close()
            except Exception:
                pass
        code_db._CONN_CACHE.clear()
        if hasattr(code_db, "_CONN_META"):
            code_db._CONN_META.clear()
        if hasattr(code_db, "_COLUMNS_CACHE"):
            code_db._COLUMNS_CACHE.clear()
    except Exception:
        pass
    # functools.lru_cache lookups in the calculation engine.
    try:
        from . import calculation_engine as ce
        for name in dir(ce):
            fn = getattr(ce, name, None)
            if callable(fn) and hasattr(fn, "cache_clear"):
                try:
                    fn.cache_clear()
                except Exception:
                    pass
    except Exception:
        pass
