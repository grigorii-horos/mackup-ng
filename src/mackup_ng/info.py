"""Report how one path relates to the backup.

`mackup info <path>` answers three questions about a single path: is it
synced at all, what does the backup side hold, and do the two sides still
agree. Everything here is read-only — it inspects the plan `sync` would run
rather than running any part of it.
"""

from __future__ import annotations

import filecmp
import os
import time
from typing import TYPE_CHECKING, NamedTuple

from . import ignore, paths, synclog, utils
from .application import ApplicationProfile

if TYPE_CHECKING:
    from .appsdb import ApplicationsDatabase
    from .mackup import Mackup
    from .mapping import Pair

TIME_FORMAT: str = "%Y-%m-%d %H:%M"
BYTES_PER_UNIT: int = 1024


class Context(NamedTuple):
    """Everything the report needs, resolved once for the whole command."""

    mckp: Mackup
    app_db: ApplicationsDatabase
    pairs: list[Pair]
    winners: dict[str, Pair]  # destination -> the pair sync will run
    declared: dict[str, list[tuple[str, str]]]  # destination -> [(app, source)]
    tombstoned: set[str]
    journal: dict[str, dict]


class Match(NamedTuple):
    """The managed mapping a requested path resolves to."""

    dest: str  # home-relative destination path
    source: str  # backup-relative source path
    app: str  # the config that owns it
    group_dest: str  # the destination that is synced as a whole
    inside: str | None  # the managed directory it sits in, when a descendant


def human_size(size: int) -> str:
    """A byte count in the largest unit that keeps it above one."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < BYTES_PER_UNIT or unit == units[-1]:
            rounded = f"{value:.0f}" if unit == "B" else f"{value:.1f}"
            return f"{rounded} {unit}"
        value /= BYTES_PER_UNIT
    return f"{size} B"


def describe_side(path: str, globs: ignore.Globs = ()) -> str:
    """One line about what currently sits at ``path``."""
    if not os.path.lexists(path):
        return f"{path} — missing"
    try:
        stamp = time.strftime(TIME_FORMAT, time.localtime(os.path.getmtime(path)))
        if os.path.isdir(path):
            entries = len(
                ApplicationProfile.collect_relative_entries(path, globs),
            )
            return f"{path} — directory, {entries} entries, {stamp}"
        if not os.path.isfile(path):
            return f"{path} — broken symlink"
        return f"{path} — {human_size(os.path.getsize(path))}, {stamp}"
    except OSError as error:
        return f"{path} — unreadable ({error})"


def files_differ(left: str, right: str) -> bool:
    """Whether two regular files hold different bytes (unreadable counts)."""
    try:
        return not filecmp.cmp(left, right, shallow=False)
    except OSError:
        return True


def count_differing_entries(
    local: str,
    backup: str,
    globs: ignore.Globs = (),
) -> int:
    """How many entries of two directory trees disagree."""
    entries = ApplicationProfile.collect_relative_entries(
        local,
        globs,
    ) | ApplicationProfile.collect_relative_entries(backup, globs)
    differing = 0
    for entry in sorted(entries):
        left = os.path.join(local, entry)
        right = os.path.join(backup, entry)
        if os.path.lexists(left) != os.path.lexists(right):
            differing += 1
            continue
        if os.path.isdir(left) != os.path.isdir(right):
            differing += 1
            continue
        if os.path.isdir(left):
            continue
        if files_differ(left, right):
            differing += 1
    return differing


def describe_state(local: str, backup: str, globs: ignore.Globs = ()) -> str:
    """What the two sides hold relative to each other, and what sync would do."""
    local_here = os.path.isfile(local) or os.path.isdir(local)
    backup_here = os.path.isfile(backup) or os.path.isdir(backup)

    if not local_here and not backup_here:
        return "missing on both sides"
    if local_here and not backup_here:
        return "local only — next sync backs it up"
    if backup_here and not local_here:
        return "backup only — next sync restores it"

    try:
        if os.path.samefile(local, backup):
            return "same file (hard link)"
    except OSError:
        pass

    if os.path.isdir(local) != os.path.isdir(backup):
        return (
            "type clash — one side is a file, the other a directory; "
            "next sync replaces the older one"
        )

    if os.path.isdir(local):
        differing = count_differing_entries(local, backup, globs)
        if not differing:
            return "in sync"
        word = "entry differs" if differing == 1 else "entries differ"
        return f"diverged — {differing} {word}; next sync merges them"

    if not files_differ(local, backup):
        return "in sync"

    local_mtime = ApplicationProfile.get_effective_mtime(local, globs)
    backup_mtime = ApplicationProfile.get_effective_mtime(backup, globs)
    if local_mtime > backup_mtime:
        return "diverged — local is newer; next sync backs it up"
    if backup_mtime > local_mtime:
        return "diverged — backup is newer; next sync restores it"
    return "diverged — same mtime; next sync leaves both as they are"


def declarations(app_db: ApplicationsDatabase) -> dict[str, list[tuple[str, str]]]:
    """Every declared destination, in read order: dest -> [(app, source), ...].

    Unlike the resolved sync plan this keeps the configs that declare a path
    but never reach the plan — excluded from sync, or gated out by ``[when]``
    — which is exactly what makes `info` able to say *why* a path is not
    synced.
    """
    declared: dict[str, list[tuple[str, str]]] = {}
    for app_name in app_db.get_app_order():
        for local, backup in app_db.get_file_mappings(app_name):
            key = ApplicationProfile.normalize_relative_path(local)
            declared.setdefault(key, []).append((app_name, backup))
    return declared


def find_match(requested: str, ctx: Context) -> Match | None:
    """Resolve a user-typed path to the mapping it names, or None."""
    winners, declared = ctx.winners, ctx.declared
    candidates = paths.candidates(requested)
    if any(paths.escapes_home(candidate) for candidate in candidates):
        return None

    for candidate in candidates:
        winner = winners.get(candidate)
        if winner is not None:
            return Match(candidate, winner.source, winner.owner_app, candidate, None)
        owners = declared.get(candidate)
        if owners:
            app_name, source = owners[-1]
            return Match(candidate, source, app_name, candidate, None)

    # Not a destination itself: it may still be a path inside a managed
    # directory, which is synced as part of that directory.
    for candidate in candidates:
        for dest, owners in declared.items():
            app_name, source = owners[-1]
            winner = winners.get(dest)
            root_source = winner.source if winner is not None else source
            root_app = winner.owner_app if winner is not None else app_name
            relative = paths.managed_descendant_relative(
                ctx.mckp.mackup_folder,
                candidate,
                dest,
                root_source,
            )
            if relative is None:
                continue
            return Match(
                os.path.normpath(os.path.join(dest, relative)),
                os.path.normpath(os.path.join(root_source, relative)),
                root_app,
                dest,
                dest,
            )
    return None


def sync_status(match: Match, ctx: Context) -> str:
    """Whether the path takes part in `mackup sync`, and why not when it doesn't."""
    app_db = ctx.app_db
    if match.group_dest in ctx.tombstoned or match.dest in ctx.tombstoned:
        return "no — removed by mackup rm (tombstoned)"
    if not app_db.config_enabled(match.app):
        unmet = ", ".join(
            f"{key}={value}"
            for key, value in sorted(app_db.get_failing_conditions(match.app).items())
        )
        return f"no — conditions not met on this machine ({unmet})"
    if match.app not in ctx.mckp.get_apps_to_backup() or not app_db.app_has_sync(
        match.app,
    ):
        return "no — not selected for sync in config.toml"
    if match.group_dest not in ctx.winners:
        return "no — not selected for sync"
    return "yes"


def build_context(
    mckp: Mackup,
    app_db: ApplicationsDatabase,
    pairs: list[Pair],
    tombstoned: set[str],
    journal: dict[str, dict],
) -> Context:
    """Resolve the plan and the declarations once, for every reported path."""
    winners = {
        ApplicationProfile.normalize_relative_path(pair.dest): pair for pair in pairs
    }
    return Context(
        mckp=mckp,
        app_db=app_db,
        pairs=pairs,
        winners=winners,
        declared=declarations(app_db),
        tombstoned=tombstoned,
        journal=journal,
    )


def report(requested: str, ctx: Context) -> tuple[list[str], bool]:
    """The report lines for one path, and whether the path is managed at all."""
    app_db = ctx.app_db
    gray = utils.AnsiColor.GRAY

    def label(text: str) -> str:
        return utils.style_text(text, bold=True)

    match = find_match(requested, ctx)
    if match is None:
        return (
            [
                f"{label('Path:')} "
                f"{ApplicationProfile.normalize_relative_path(requested)}",
                f"{label('Config:')} not managed by any config",
            ],
            False,
        )

    lines = [f"{label('Path:')} {match.dest}"]
    pretty = app_db.get_name(match.app)
    name = match.app if pretty == match.app else f"{match.app} ({pretty})"
    lines.append(f"{label('Config:')} {name}")
    if match.inside is not None:
        lines.append(
            f"{label('Managed:')} as part of the directory {match.inside}, "
            f"synced inside {match.inside}",
        )

    # Another config declaring the same destination later in read order takes
    # it over, source and all — say so, or the backup path below looks wrong.
    losers = [
        f"{app_name} ({source})"
        for app_name, source in ctx.declared.get(match.group_dest, [])
        if app_name != match.app
    ]
    if losers:
        lines.append(
            f"{label('Overridden:')} also declared by {', '.join(losers)}",
        )

    lines.append(f"{label('Sync:')} {sync_status(match, ctx)}")

    winner = ctx.winners.get(match.group_dest)
    if winner is not None:
        siblings = [
            pair.dest
            for pair in ctx.pairs
            if pair.source == winner.source and pair.dest != winner.dest
        ]
        if siblings:
            lines.append(
                f"{label('Fanout:')} {winner.source} also feeds {', '.join(siblings)}",
            )

    local_path = os.path.join(os.environ["HOME"], match.dest)
    backup_path = os.path.join(ctx.mckp.mackup_folder, match.source)
    globs = ignore.load_globs() + tuple(app_db.get_ignore_patterns(match.app))
    lines.append(f"{label('Local:')} {describe_side(local_path, globs)}")
    lines.append(f"{label('Backup:')} {describe_side(backup_path, globs)}")

    entry = synclog.lookup(ctx.journal, match.group_dest)
    if entry is None:
        lines.append(
            f"{label('Last sync:')} never recorded on this machine "
            f"{utils.style_text('(run mackup sync)', color=gray)}",
        )
    else:
        stamp = time.strftime(TIME_FORMAT, time.localtime(float(entry.get("ts", 0))))
        lines.append(f"{label('Last sync:')} {stamp} ({entry.get('action', '?')})")

    lines.append(
        f"{label('State:')} {describe_state(local_path, backup_path, globs)}",
    )
    return lines, True
