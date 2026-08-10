"""Uniform condition evaluation for config-file action blocks.

Conditions live in a block's ``[when]`` sub-table with short keys (the section
name supplies the context). A block runs only if every condition in its
``[when]`` passes. List values are any-of; missing keys are vacuously true.
"""

from __future__ import annotations

import os
import platform
import shutil

from . import hooks


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _one(when: dict, key: str) -> bool:
    value = when.get(key)
    if value is None:
        return True
    if key == "os":
        return hooks.os_kind() in _as_list(value)
    if key == "arch":
        return platform.machine() in _as_list(value)
    if key == "marker":
        return all(hooks.has_marker(n) for n in _as_list(value))
    if key == "not_marker":
        return not any(hooks.has_marker(n) for n in _as_list(value))
    if key == "command":
        return all(shutil.which(c) is not None for c in _as_list(value))
    if key == "gui":
        return (not value) or hooks.has_gui()
    if key == "exists":
        return all(os.path.exists(os.path.expanduser(p)) for p in _as_list(value))
    if key == "not_exists":
        return not any(os.path.exists(os.path.expanduser(p)) for p in _as_list(value))
    if key == "env":
        if isinstance(value, dict):
            return all(os.environ.get(k) == v for k, v in value.items())
        return all(os.environ.get(k) is not None for k in _as_list(value))
    return True


CONDITION_KEYS = (
    "os",
    "arch",
    "marker",
    "not_marker",
    "command",
    "gui",
    "exists",
    "not_exists",
    "env",
)
# Private alias kept for existing internal references.
_CONDITION_KEYS = CONDITION_KEYS


def unrecognized_keys(when: dict) -> list[str]:
    """Return the keys of ``when`` outside the recognized condition vocabulary."""
    return [key for key in when if key not in CONDITION_KEYS]


def block_passes(block: dict) -> bool:
    """True iff every condition in the block's ``[when]`` sub-table passes."""
    when = block.get("when") or {}
    return all(_one(when, key) for key in _CONDITION_KEYS)


def config_passes(data: dict) -> bool:
    """True iff every condition in a config's top-level ``[when]`` passes.

    A config's conditions gate everything it declares — its sync entries as
    well as its blocks — and use the same vocabulary as a block's ``[when]``.
    """
    return block_passes(data)


def failing(data: dict) -> dict:
    """Return only the conditions in ``data``'s ``[when]`` that do not hold."""
    when = data.get("when") or {}
    return {
        key: when[key]
        for key in _CONDITION_KEYS
        if key in when and not _one(when, key)
    }
