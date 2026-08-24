"""Machine-local record of what the last sync did to each destination.

The mtime of a backup copy says when the file last changed, not when mackup
last touched it — so `mackup info` would have nothing to report about the run
itself. This log fills that gap: `sync` writes one entry per destination it
resolved, `info` reads them back.

The log is per-machine runtime state, so it lives under ``$XDG_STATE_HOME``
next to the marker flags, never in the synced backup folder. Nothing here may
raise into a sync: an unwritable or corrupt log is simply no answer.
"""

from __future__ import annotations

import json
import os

from .application import ApplicationProfile

LOG_FILENAME: str = "sync-log.json"


def log_path() -> str:
    """Path of the sync log under ``$XDG_STATE_HOME``."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.environ["HOME"], ".local", "state",
    )
    return os.path.join(base, "mackup", LOG_FILENAME)


def read() -> dict[str, dict]:
    """Return the recorded entries, keyed by normalized destination path.

    An absent, unreadable or malformed log is simply no answer.
    """
    try:
        with open(log_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, KeyError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(dest): entry
        for dest, entry in data.items()
        if isinstance(entry, dict)
    }


def record(entries: dict[str, dict]) -> None:
    """Merge ``entries`` into the log. A log we cannot write is not an error."""
    if not entries:
        return
    merged = read()
    for dest, entry in entries.items():
        merged[ApplicationProfile.normalize_relative_path(dest)] = entry
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=1, sort_keys=True)
    except (OSError, KeyError):
        return


def lookup(entries: dict[str, dict], dest: str) -> dict | None:
    """The entry recorded for ``dest``, whatever spelling of the path is used."""
    return entries.get(ApplicationProfile.normalize_relative_path(dest))
