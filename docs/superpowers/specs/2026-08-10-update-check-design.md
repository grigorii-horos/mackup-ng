# Update check: tell the user when a newer mackup-ng is on PyPI

**Date:** 2026-08-10
**Status:** approved design, pending implementation plan

## Problem

mackup-ng ships from PyPI and is installed by pip, uv, pipx or snap. Nothing
tells a user that the copy they run is behind. Machines that sync rarely — a
phone, an e-ink reader — can sit several versions back without any signal, and
this fork changes sync semantics often enough that the gap matters.

## Goals

- Report a newer release during ordinary use, without the user asking.
- Cost nothing when there is nothing to say: no noise, no perceptible delay, no
  network on most runs.
- Stay switchable off, per machine, with the mechanism the project already uses
  for opt-outs.
- Add no dependency.

## Non-goals

- No self-update. The tool prints a command; the user runs it.
- No pre-release announcements.
- No separate `check-update` command — the check rides on `sync`.
- No telemetry of any kind; the request carries nothing but the package name.

## Behavior

The check runs at the end of `mackup sync`, after the summary. No other command
touches the network — not `apply`, `list`, `show` or `rm`.

`--dry-run` skips the check entirely: a dry run stays free of side effects,
including the cache write and the request.

**Source.** The PyPI JSON API, `https://pypi.org/pypi/mackup-ng/json`, field
`info.version` — the same index the package installs from. `urllib` with a
2-second timeout, no new dependency. Any failure — offline, timeout,
non-200, malformed JSON, missing field — is swallowed silently; under `-v` a
single line says why.

**Cache.** `$XDG_CACHE_HOME/mackup/update-check.json`, holding
`{"checked_at": <epoch seconds>, "latest": "2.2.0"}`, with a 24-hour TTL.
Inside the TTL there is no request at all and the line, if any, is printed from
the cached value. This is a cache, not state: deleting the file costs one
request. A malformed or unreadable cache file is treated as absent.

**Message.** When the cached-or-fetched version is newer than the running one:

```text
mackup-ng 2.1.0 -> 2.2.0 available. Upgrade: uv tool upgrade mackup-ng
```

The command is guessed from the path of the running executable:

| Path contains | Command |
| --- | --- |
| `/snap/` | `sudo snap refresh mackup-ng` |
| uv tools directory (`.local/share/uv/tools`) | `uv tool upgrade mackup-ng` |
| pipx venvs directory (`.local/pipx` or `pipx/venvs`) | `pipx upgrade mackup-ng` |
| anything else | `pip install --upgrade mackup-ng` |

A guess that cannot be made prints the fact alone, with no `Upgrade:` clause.
The table's last row means the fallback is `pip`, so "cannot be made" only
happens if the executable path is unavailable.

**Opt-out.** The marker `no-update-check`, defined in
`src/mackup_ng/markers/` so it appears in `mackup-ng markers` beside `no-dconf`
and the other opt-outs. With the marker set, nothing is fetched, nothing is
read from the cache and nothing is printed.

**Version comparison.** Own code, no new dependency: a version is a dot-
separated run of integers, compared as a tuple. Anything else — `2.2.0rc1`,
`2.2.0.dev3`, `2.2` followed by a letter — is a pre-release or unparseable and
is ignored, so pre-releases are never announced. A fetched version that does
not parse is treated as no answer.

## Code

New module `src/mackup_ng/update.py`, with the network confined to one function
so everything else is testable offline:

- `parse_version(text: str) -> tuple[int, ...] | None` — `None` for
  pre-releases and anything unparseable
- `is_newer(latest: str, current: str) -> bool`
- `read_cache() -> str | None` / `write_cache(latest: str) -> None` — the cache
  file and its TTL
- `upgrade_command(executable: str) -> str | None` — the guess above
- `fetch_latest(timeout: float = 2.0) -> str | None` — the only network call
- `check(current: str, *, fetch=fetch_latest) -> str | None` — marker, cache,
  fetch, message assembly; returns the line to print or `None`

`main.py`'s `sync` branch calls `check` at the end, unless `--dry-run`, and
prints the result when it is not `None`.

## Testing

Tests inject a fake `fetch` and point `XDG_CACHE_HOME` at a temp directory; no
test touches the network.

- a pre-release fetched result is ignored
- an equal or older fetched result prints nothing
- a newer result prints the line, with the version numbers in it
- malformed JSON / a fetch that raises / a fetch returning `None` prints nothing
- a fresh cache entry suppresses the fetch entirely (the fake records calls)
- an expired cache entry triggers a fetch and is rewritten
- an unreadable or malformed cache file behaves as if absent
- the `no-update-check` marker suppresses fetch, cache read and output
- each branch of `upgrade_command`, including the unknown-path case
- `--dry-run` performs no fetch and writes no cache file

## Documentation

`README.md` and `AGENTS.md` gain a short section: what the check does, that it
runs at most once a day, that it contacts PyPI — which discloses the machine's
IP address and the fact that the package is in use — and that
`mackup-ng mark no-update-check` turns it off for good on that machine.
