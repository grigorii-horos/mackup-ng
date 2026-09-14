"""Check PyPI for a newer mackup-ng and tell the user once a day.

The network lives in :func:`fetch_latest` alone; everything else — version
parsing, the cache file, the install-method guess — is offline and testable
without a socket. Nothing here may raise into a sync: a failed check is a
silent no-op.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from typing import TYPE_CHECKING

from . import dirs, hooks, utils

if TYPE_CHECKING:
    from collections.abc import Callable

CACHE_TTL_SECONDS: int = 24 * 60 * 60

PYPI_URL: str = "https://pypi.org/pypi/mackup-ng/json"
NO_UPDATE_CHECK_MARKER: str = "no-update-check"

_NUMERIC_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return a comparable tuple, or None for a pre-release or junk.

    Only a dot-separated run of integers counts. ``2.2.0rc1`` and
    ``2.2.0.dev3`` are pre-releases and deliberately unparseable, which is how
    they stay unannounced.
    """
    stripped = str(text).strip()
    if not _NUMERIC_VERSION_RE.match(stripped):
        return None
    return tuple(int(part) for part in stripped.split("."))


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a strictly newer release than ``current``."""
    new = parse_version(latest)
    now = parse_version(current)
    if new is None or now is None:
        return False
    return new > now


def upgrade_command(executable: str) -> str | None:
    """Guess the upgrade command from the path of the running executable."""
    if not executable:
        return None
    path = os.path.realpath(executable)
    parts = path.split(os.sep)
    if "snap" in parts:
        return "sudo snap refresh mackup-ng"
    if f"{os.sep}uv{os.sep}tools{os.sep}" in path:
        return "uv tool upgrade mackup-ng"
    if "pipx" in parts:
        return "pipx upgrade mackup-ng"
    return "pip install --upgrade mackup-ng"


def cache_path() -> str:
    """Path of the update-check cache file under ``$XDG_CACHE_HOME``."""
    return os.path.join(dirs.cache_dir(), "update-check.json")


def read_cache(now: float) -> str | None:
    """Return the cached version while it is fresh, else None.

    An absent, unreadable or malformed cache file is simply no answer.
    """
    try:
        with open(cache_path()) as handle:
            data = json.load(handle)
        checked_at = float(data["checked_at"])
        latest = str(data["latest"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if now - checked_at > CACHE_TTL_SECONDS:
        return None
    return latest


def write_cache(latest: str, now: float) -> None:
    """Store the fetched version. A cache we cannot write is not an error."""
    try:
        path = cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"checked_at": now, "latest": latest}, handle)
    except (OSError, KeyError):
        return


def fetch_latest(timeout: float = 2.0) -> str | None:
    """Ask PyPI for the latest published version, or None if we cannot.

    The only network call in mackup. Every failure mode — offline, timeout,
    an error status, malformed JSON, a missing field — collapses to None.
    """
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as response:
            payload = json.load(response)
        return str(payload["info"]["version"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def check(
    current: str,
    *,
    fetch: Callable[[], str | None] | None = None,
    now: float | None = None,
    verbose: bool = False,
) -> str | None:
    """Return the line to print about a newer release, or None.

    Consults the ``no-update-check`` marker first, then the cache, and only
    then the network. Never raises: a check that cannot answer says nothing.
    """
    if hooks.has_marker(NO_UPDATE_CHECK_MARKER):
        return None

    moment = time.time() if now is None else now
    latest = read_cache(moment)
    if latest is None:
        fetcher = fetch if fetch is not None else fetch_latest
        try:
            latest = fetcher()
        except Exception:  # a broken fetch must not break sync
            latest = None
        if latest is None:
            if verbose:
                print(utils.colorize_message("Update check skipped: PyPI unreachable"))
            return None
        write_cache(latest, moment)

    if not is_newer(latest, current):
        return None

    command = upgrade_command(sys.argv[0] if sys.argv else "")
    line = f"mackup-ng {current} -> {latest} available."
    if command is None:
        return line
    return f"{line} Upgrade: {command}"
