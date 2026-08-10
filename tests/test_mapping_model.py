"""Tests for the pure destination-keyed mapping model."""

import unittest

from mackup_ng.mapping import (
    Pair,
    build_pairs,
    group_by_source,
    group_owners,
)


class TestBuildPairs(unittest.TestCase):
    def test_keeps_order_and_allows_shared_source(self):
        entries = [
            Pair(".config/profile/user.js", ".config/work/user.js", "app-a"),
            Pair(".config/profile/user.js", ".config/home/user.js", "app-a"),
        ]
        pairs, evictions = build_pairs(entries)
        assert pairs == entries
        assert evictions == []

    def test_repeated_destination_evicts_previous_pair(self):
        first = Pair(".config/x", ".config/x", "app-a")
        second = Pair(".config/x-work", ".config/x", "zz-work")
        pairs, evictions = build_pairs([first, second])
        assert pairs == [second]
        assert len(evictions) == 1
        assert evictions[0].evicted == first
        assert evictions[0].winner == second

    def test_redeclaring_the_same_source_is_not_an_eviction(self):
        """An identical re-declaration changes nothing, so it is not reported."""
        first = Pair(".config/x", ".config/x", "app-a")
        again = Pair(".config/x", ".config/x", "zz-work")
        pairs, evictions = build_pairs([first, again])
        assert pairs == [again]
        assert evictions == []

    def test_winner_moves_to_the_end_of_the_list(self):
        first = Pair(".config/a", ".config/a", "app-a")
        second = Pair(".config/b", ".config/b", "app-b")
        override = Pair(".config/a-alt", ".config/a", "zz-work")
        pairs, _ = build_pairs([first, second, override])
        assert pairs == [second, override]


class TestGroupBySource(unittest.TestCase):
    def test_groups_destinations_under_their_source(self):
        pairs = [
            Pair(".config/profile/user.js", ".config/work/user.js", "app-a"),
            Pair(".config/profile/user.js", ".config/home/user.js", "app-a"),
            Pair(".vimrc", ".vimrc", "vim"),
        ]
        groups, orphans = group_by_source(pairs)
        assert groups == {
            ".config/profile/user.js": [
                ".config/work/user.js",
                ".config/home/user.js",
            ],
            ".vimrc": [".vimrc"],
        }
        assert orphans == []

    def test_source_with_no_surviving_destination_is_orphaned(self):
        first = Pair(".config/x", ".config/x", "app-a")
        second = Pair(".config/x-work", ".config/x", "zz-work")
        pairs, evictions = build_pairs([first, second])
        _, orphans = group_by_source(pairs, evictions)
        assert orphans == [".config/x"]

    def test_evicted_source_still_used_elsewhere_is_not_orphaned(self):
        entries = [
            Pair(".config/x", ".config/x", "app-a"),
            Pair(".config/x", ".config/x-copy", "app-a"),
            Pair(".config/x-work", ".config/x", "zz-work"),
        ]
        pairs, evictions = build_pairs(entries)
        _, orphans = group_by_source(pairs, evictions)
        assert orphans == []


class TestGroupOwners(unittest.TestCase):
    def test_owner_is_the_last_pair_of_the_group(self):
        """The config that won the last destination owns the group."""
        pairs = [
            Pair(".config/p", ".config/work", "app-a"),
            Pair(".config/p", ".config/home", "zz-work"),
        ]
        assert group_owners(pairs) == {".config/p": "zz-work"}

    def test_owner_ignores_pairs_the_caller_filtered_out(self):
        """Feeding only the live pairs never attributes a group to a dead one."""
        pairs = [
            Pair(".config/p", ".config/work", "app-a"),
            Pair(".config/p", ".config/home", "zz-work"),
        ]
        live = [pair for pair in pairs if pair.dest != ".config/home"]
        assert group_owners(live) == {".config/p": "app-a"}
