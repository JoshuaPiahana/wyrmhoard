"""
Memoisation keyed on the ledger's version.

Every analysis function derives from the same table, and the dashboard asks
nine endpoints for overlapping views of it at once. Without this, one page
load rebuilds the same DataFrame nine times and recomputes the same medians
inside both /summary and /coach.

Invalidation is by file version rather than by an explicit "clear the cache"
call, because explicit invalidation is the kind of thing that works until
somebody adds a code path that forgets to do it - and a stale financial
dashboard that looks fresh is precisely the failure this project cannot have.

The version includes the -wal file on purpose. SQLite runs in WAL mode here,
so a write lands in ledger.db-wal and may not touch ledger.db's mtime at all;
keying on the main file alone would serve stale numbers immediately after an
import.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from .config import DATA_DIR

DB_FILES = ("ledger.db", "ledger.db-wal")

F = TypeVar("F", bound=Callable[..., Any])


def ledger_version() -> tuple:
    """A cheap fingerprint of the ledger's current state."""
    parts: list[Any] = []
    for name in DB_FILES:
        path = DATA_DIR / name
        try:
            st = path.stat()
            parts.append((name, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            parts.append((name, None, None))
    return tuple(parts)


def by_ledger(fn: F) -> F:
    """
    Cache a function's result until the ledger changes.

    Only safe for functions whose output depends solely on the ledger and the
    config files - which is every analysis function here. Config edits are
    picked up through the /reload endpoint, which clears these caches.
    """
    store: dict[Any, Any] = {}

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        key = (ledger_version(), args, tuple(sorted(kwargs.items())))
        if key not in store:
            store.clear()          # only ever hold the current version
            store[key] = fn(*args, **kwargs)
        return store[key]

    wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
    _REGISTRY.append(wrapper)
    return wrapper  # type: ignore[return-value]


_REGISTRY: list[Any] = []


def clear_all() -> None:
    """Drop every ledger-keyed cache. Called when config changes."""
    for fn in _REGISTRY:
        fn.cache_clear()
