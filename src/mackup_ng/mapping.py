"""Destination-keyed mapping model.

A managed item is a :class:`Pair` — a backup ``source`` feeding a local
``dest``. Destinations are unique: a later pair for the same destination
evicts the earlier one. Sources may repeat, and a repeated source is what
expresses fanout (one backup file feeding several local files).

Everything here is pure: no filesystem access, no configuration parsing.
The caller resolves paths first and executes the resulting plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True)
class Pair:
    """One managed mapping: backup ``source`` feeds local ``dest``."""

    source: str
    dest: str
    owner_app: str


@dataclass(frozen=True)
class Eviction:
    """A pair displaced because a later pair claimed the same destination."""

    evicted: Pair
    winner: Pair


def build_pairs(entries: Iterable[Pair]) -> tuple[list[Pair], list[Eviction]]:
    """Resolve entries in read order; later pairs win their destination.

    Returns the surviving pairs (in final order, winners last) and the list
    of evictions, in the order they happened.
    """
    by_dest: dict[str, Pair] = {}
    evictions: list[Eviction] = []
    for pair in entries:
        previous = by_dest.pop(pair.dest, None)
        if previous is not None:
            evictions.append(Eviction(previous, pair))
        by_dest[pair.dest] = pair
    return list(by_dest.values()), evictions


def group_by_source(
    pairs: Sequence[Pair],
    evictions: Sequence[Eviction] = (),
) -> tuple[dict[str, list[str]], list[str]]:
    """Group destinations under their source; report orphaned sources.

    A source is orphaned when every pair that used it was evicted, so it
    feeds nothing on this machine.
    """
    groups: dict[str, list[str]] = {}
    for pair in pairs:
        groups.setdefault(pair.source, []).append(pair.dest)
    orphans = [
        eviction.evicted.source
        for eviction in evictions
        if eviction.evicted.source not in groups
    ]
    return groups, list(dict.fromkeys(orphans))


def group_owners(pairs: Sequence[Pair]) -> dict[str, str]:
    """Map each source to the app that first claimed it (for statistics)."""
    owners: dict[str, str] = {}
    for pair in pairs:
        owners.setdefault(pair.source, pair.owner_app)
    return owners
