"""Shipped configs must not offer ~/Library paths to machines that have none.

`~/Library` exists only on macOS. A config that syncs nothing else has no
business being considered on Linux or Windows, so it declares
`[when] os = "macos"` and is skipped whole. A config that syncs both kinds of
path cannot say that at the config level — gating the whole file would take
its non-macOS paths down with it — so it carries two blocks instead, one
gated `not_os = "macos"` and one gated `os = "macos"`.

Both rules are enforced here so that a config added later cannot quietly
reintroduce either shape.
"""

import pathlib
import tomllib

APPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "mackup_ng" / "applications"

LIBRARY_PREFIX = "Library/"


def _config_paths(data: dict) -> list[str]:
    """Every local path a config claims: its `files` plus both sides of any
    `mapped_files` entry."""
    found = [p for p in data.get("files", []) or [] if isinstance(p, str)]
    mapped = data.get("mapped_files") or {}
    if isinstance(mapped, dict):
        for source, dests in mapped.items():
            if isinstance(source, str):
                found.append(source)
            found.extend(
                dest
                for dest in (dests if isinstance(dests, list) else [dests])
                if isinstance(dest, str)
            )
    return found


def _all_configs():
    for path in sorted(APPS_DIR.glob("*.toml")):
        with path.open("rb") as handle:
            yield path, tomllib.load(handle)


def _library_split(data: dict) -> tuple[list[str], list[str]]:
    paths = _config_paths(data)
    library = [p for p in paths if p.startswith(LIBRARY_PREFIX)]
    other = [p for p in paths if not p.startswith(LIBRARY_PREFIX)]
    return library, other


def test_at_least_one_library_config_exists():
    """Guards the two tests below against silently passing on an empty set."""
    with_library = [
        path.name for path, data in _all_configs() if _library_split(data)[0]
    ]

    assert len(with_library) > 100, f"only {len(with_library)} found"


def test_library_only_configs_are_gated_to_macos():
    offenders = []
    for path, data in _all_configs():
        library, other = _library_split(data)
        if library and not other and data.get("when", {}).get("os") != "macos":
            offenders.append(path.name)

    assert offenders == [], (
        f"{len(offenders)} config(s) sync only ~/Library but are still"
        f" considered on every platform: {offenders[:10]}"
    )


def _unit_tables(data: dict) -> list[dict]:
    """The config's file-bearing tables: the top level, then each block."""
    return [data, *[b for b in data.get("block", []) if isinstance(b, dict)]]


def _table_paths(table: dict) -> list[str]:
    return [p for p in table.get("files", []) or [] if isinstance(p, str)]


def test_no_library_path_is_offered_to_other_platforms():
    """Every ~/Library path sits in a config or a block gated to macOS."""
    offenders = []
    for path, data in _all_configs():
        config_gated = data.get("when", {}).get("os") == "macos"
        for table in _unit_tables(data):
            if not any(p.startswith(LIBRARY_PREFIX) for p in _table_paths(table)):
                continue
            block_gated = table.get("when", {}).get("os") == "macos"
            if not (config_gated or block_gated):
                offenders.append(path.name)
                break

    assert offenders == [], (
        f"{len(offenders)} config(s) offer a ~/Library path on every platform:"
        f" {offenders[:10]}"
    )


def test_no_split_macos_files_remain():
    """The <app>-macos.toml convention is gone; blocks replaced it."""
    leftovers = [path.name for path, _ in _all_configs() if path.stem.endswith("-macos")]

    assert leftovers == [], f"still split: {leftovers[:10]}"
