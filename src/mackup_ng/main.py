"""mackup-ng.

Keep your application settings in sync.
A maintained fork of mackup by Laurent Raufaste <http://glop.org/>.
Copyright (C) 2013-2025 Laurent Raufaste, Grigorii Horos.

Usage:
  mackup-ng [options] list
  mackup-ng [options] show <application>
  mackup-ng [options] sync
  mackup-ng [options] rm <path>...
  mackup-ng [options] mark <marker>
  mackup-ng [options] unmark <marker>
  mackup-ng [options] markers
  mackup-ng [options] dconf-add <path>...
  mackup-ng [options] apply
  mackup-ng (-h | --help)

Options:
  -h --help                 Show this screen.
  -f --force                Force every question asked to be answered with "Yes".
  --force-no                Force every question asked to be answered with "No".
  -r --root                 Allow mackup-ng to be run as superuser.
  -n --dry-run              Show steps without executing.
  -v --verbose              Show additional details.
  -c --config-file=<path>   Specify custom config file path.
  --version                 Show version.

Modes of action:
 - mackup-ng list: display a list of all supported applications.
 - mackup-ng show: display the details for a supported application.
 - mackup-ng sync: synchronize local and remote config files in both directions.
       Runs each config's action blocks (pre before / post after its file sync).
 - mackup-ng rm: remove a managed config file locally and from the remote folder.
 - mackup-ng mark: set a machine-local marker (e.g. backup, low-resource).
 - mackup-ng unmark: remove a machine-local marker.
 - mackup-ng markers: list known and active markers.
 - mackup-ng dconf-add: track and dump dconf path(s), e.g. /org/gnome/terminal/.
 - mackup-ng apply: run every config's action blocks without syncing files.

dconf paths are backed up (dumped) on the backup-role machine and restored
(loaded) on other machines during `mackup sync`, unless the `no-dconf` marker
is set. Dumps live in ~/.mackup/dconf-backup/.

By default, mackup-ng syncs all application data via
Dropbox, but may be configured to exclude applications or use a different
backend with a .mackup.cfg file.

See https://github.com/grigorii-horos/mackup-ng/tree/master/doc for more information.

"""

import os
import sys
from collections import Counter
from typing import Any, NoReturn

from docopt import docopt

from . import blocks, dconf, hooks, mapping, utils
from .application import ApplicationProfile
from .appsdb import ApplicationsDatabase
from .constants import VERSION
from .mackup import Mackup


def header(text: str) -> str:
    return utils.style_text(text, color=utils.AnsiColor.BLUE)


def bold(text: str) -> str:
    return utils.style_text(text, bold=True)


def die(message: str) -> NoReturn:
    """Exit with a red error message (plain when color is disabled)."""
    sys.exit(utils.style_text(message, color=utils.AnsiColor.RED, bold=True))


_HELP_HEADERS = ("Usage:", "Options:", "Modes of action:")


def colorize_help(doc: str) -> str:
    """Bold the section headers and the title line of the --help text."""
    lines: list[str] = []
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped in _HELP_HEADERS or stripped == "mackup-ng.":
            lines.append(bold(line))
        else:
            lines.append(line)
    return "\n".join(lines)


def get_action_label(stats: dict[str, int]) -> str | None:
    """Return a past-tense action label describing what happened."""
    if not any(stats.values()):
        return None

    backed_up = stats.get("backed_up", 0)
    restored = stats.get("restored", 0)
    synchronized = stats.get("synchronized", 0)
    deleted = stats.get("deleted", 0)
    errors = stats.get("errors", 0)
    if errors > 0:
        return "Failed"
    if deleted > 0:
        return "Deleted"
    if backed_up > 0 and restored > 0:
        return "Synchronized"
    if backed_up > 0:
        return "Backed up"
    if restored > 0:
        return "Restored"
    if synchronized > 0:
        return "Synchronized"
    return "Skipped"


def build_sync_plan(
    app_db: ApplicationsDatabase,
    apps_to_sync: set[str],
) -> tuple[list[mapping.Pair], list[mapping.Eviction]]:
    """Collect every selected app's pairs in read order and resolve them.

    A config whose top-level ``[when]`` does not hold on this machine declares
    nothing, so it never claims — or evicts — a destination.
    """
    entries = [
        mapping.Pair(source=backup, dest=local, owner_app=app_name)
        for app_name in app_db.get_app_order()
        if app_name in apps_to_sync
        and app_db.app_has_sync(app_name)
        and app_db.config_enabled(app_name)
        for local, backup in app_db.get_file_mappings(app_name)
    ]
    return mapping.build_pairs(entries)


def main() -> None:
    """Main function."""
    # Get the command line arg
    docstring = __doc__
    if not docstring:
        die(
            "Usage information is not available because __doc__ is None. "
            "This can happen when running Python with optimizations (python -OO). "
            "Please run Mackup without -OO to use the command-line interface.",
        )
    assert docstring is not None  # for type narrowing after sys.exit

    # Handle -h/--help ourselves (before docopt) so it can be colorized —
    # docopt would otherwise print the raw docstring and exit.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(colorize_help(docstring))
        return

    args: dict[str, Any] = docopt(docstring, version=f"mackup-ng {VERSION}")

    if args["--force"] and args["--force-no"]:
        die("Options --force and --force-no are mutually exclusive.")

    config_file: str | None = args.get("--config-file")
    mckp: Mackup = Mackup(config_file)
    app_db: ApplicationsDatabase = ApplicationsDatabase()

    def print_app_header(app_name: str, pretty_name: str) -> None:
        if verbose:
            header_str = header("---")
            print(f"\n{header_str} {bold(f'{app_name}: {pretty_name}')} {header_str}")

    def report_config(
        pretty_name: str,
        stats: dict[str, int] | None,
        tally: "blocks.Counter",
    ) -> None:
        """Print ONE summary line for a config: sync outcome + block changes."""
        sync_label = get_action_label(stats) if stats else None
        phrase = blocks.summarize(tally)
        detail = f" ({phrase})" if phrase else ""
        if sync_label and sync_label != "Skipped":
            print(utils.colorize_message(f"{sync_label} {pretty_name}{detail}"))
        elif phrase:
            print(utils.colorize_message(f"Applied {pretty_name}{detail}"))
        elif stats is not None:
            print(utils.colorize_message(f"Skipped {pretty_name}"))

    def escapes_home(rel_path: str) -> bool:
        """Whether a relative path points outside the home folder."""
        return rel_path == ".." or rel_path.startswith(("../", "..\\", "/"))

    def get_requested_path_candidates(path: str) -> list[str]:
        candidates = [ApplicationProfile.normalize_relative_path(path)]
        if not os.path.isabs(os.path.expanduser(path)):
            absolute_path = os.path.abspath(path)
            home = os.path.abspath(os.environ["HOME"])
            try:
                cwd_relative = ApplicationProfile.normalize_relative_path(
                    os.path.relpath(absolute_path, home),
                )
            except ValueError:
                cwd_relative = None
            # The current-directory interpretation is only a convenience for
            # running rm from inside a managed folder. When the current
            # directory is outside home it escapes and must be dropped, or it
            # would wrongly trip the unmanaged-path guard even though the
            # literal candidate is a valid managed home-relative path.
            if cwd_relative is not None and not escapes_home(cwd_relative):
                candidates.append(cwd_relative)
        return list(dict.fromkeys(candidates))

    def is_managed_directory(local_filename: str, backup_filename: str) -> bool:
        return os.path.isdir(
            os.path.join(os.environ["HOME"], local_filename),
        ) or os.path.isdir(
            os.path.join(mckp.mackup_folder, backup_filename),
        )

    def get_managed_descendant_relative(
        requested_path: str,
        local_filename: str,
        backup_filename: str,
    ) -> str | None:
        """The path of ``requested_path`` below a managed directory, or None."""
        local_root = ApplicationProfile.normalize_relative_path(local_filename)
        try:
            relative_path = os.path.relpath(requested_path, local_root)
        except ValueError:
            return None

        if relative_path == os.curdir or relative_path.startswith(os.pardir + os.sep):
            return None
        if os.path.isabs(relative_path):
            return None
        if not is_managed_directory(local_filename, backup_filename):
            return None

        return relative_path

    # If we want to answer mackup with "yes" for each question
    if args["--force"]:
        utils.FORCE_YES = True

    # If we want to answer mackup with "no" for each question
    if args["--force-no"]:
        utils.FORCE_NO = True

    # Allow mackup to be run as root
    if args["--root"]:
        utils.CAN_RUN_AS_ROOT = True

    dry_run: bool = args["--dry-run"]

    verbose: bool = args["--verbose"]

    # mackup list
    if args["list"]:
        # Display the list of supported applications
        mckp.check_for_usable_environment()
        synced = sorted(n for n in app_db.get_app_names() if app_db.app_has_sync(n))
        dash = utils.style_text(" -", color=utils.AnsiColor.GRAY)
        lines = [bold("Supported applications:")]
        lines.extend(
            f"{dash} {utils.style_text(name, color=utils.AnsiColor.CYAN)}"
            for name in synced
        )
        lines.append("")
        lines.append(
            f"{bold(str(len(synced)))} applications supported in "
            f"mackup-ng v{VERSION}",
        )
        print("\n".join(lines))

    # mackup show <application>
    elif args["show"]:
        mckp.check_for_usable_environment()
        requested_app_name: str = args["<application>"]

        # Make sure the app exists
        if requested_app_name not in app_db.get_app_names():
            die(f"Unsupported application: {requested_app_name}")
        dash = utils.style_text(" -", color=utils.AnsiColor.GRAY)
        pretty = utils.style_text(
            app_db.get_name(requested_app_name), color=utils.AnsiColor.CYAN, bold=True,
        )
        print(f"{bold('Name:')} {pretty}")
        if not app_db.config_enabled(requested_app_name):
            failing_conds = app_db.get_failing_conditions(requested_app_name)
            unmet = ", ".join(
                f"{key}={value}"
                for key, value in sorted(failing_conds.items())
            )
            print(
                utils.style_text(
                    f"conditions not met on this machine ({unmet})",
                    color=utils.AnsiColor.GRAY,
                ),
            )
        mappings = app_db.get_file_mappings(requested_app_name)
        if mappings:
            # Resolve exactly the plan `sync` would resolve, so the overrides
            # reported here are the overrides that actually happen.
            pairs, _ = build_sync_plan(app_db, mckp.get_apps_to_backup())
            winners = {pair.dest: pair for pair in pairs}
            fanout = Counter(pair.source for pair in pairs)
            print(bold("Configuration files:"))
            for local, backup in mappings:
                winner = winners.get(local)
                if winner is None:
                    # This config is excluded from sync, so the plan holds no
                    # entry for it — there is nothing to override it either.
                    skipped = utils.style_text(
                        "(not selected for sync)", color=utils.AnsiColor.GRAY,
                    )
                    print(f"{dash} {local} <- {backup} {skipped}")
                    continue
                if winner.source != backup:
                    lost = utils.style_text(
                        f"(overridden by {winner.owner_app})",
                        color=utils.AnsiColor.GRAY,
                    )
                    print(f"{dash} {local} <- {backup} {lost}")
                    continue
                extra = ""
                if fanout[backup] > 1:
                    extra = " " + utils.style_text(
                        f"(fanout: {fanout[backup]} destinations)",
                        color=utils.AnsiColor.GRAY,
                    )
                print(f"{dash} {local} <- {backup}{extra}")
        cfg_blocks = app_db.get_blocks(requested_app_name)
        if cfg_blocks:
            print(bold("Action blocks:"))
            for b in cfg_blocks:
                phase = utils.style_text(
                    b.get("phase", "post"), color=utils.AnsiColor.GRAY,
                )
                action = utils.style_text(
                    str(blocks.block_action(b)), color=utils.AnsiColor.CYAN,
                )
                print(f"{dash} {phase}: {action}")

    # mackup sync
    elif args["sync"]:
        mckp.check_for_usable_backup_env()

        role = hooks.machine_role()
        dconf_enabled = not hooks.has_marker("no-dconf")

        # On the source machine, dump tracked dconf paths so they get synced out.
        if role == "backup" and dconf_enabled:
            dconf.dump_all(dry_run)

        # Per config (sorted by id): pre-blocks -> file sync -> post-blocks,
        # then ONE summary line per config. Iterate ALL configs so block-only
        # files (hooks) run too; file sync is limited to selected configs.
        # The sync plan itself is resolved ONCE, globally, across every
        # selected app (in config read order), so an override in one config
        # can displace a pair declared by another.
        to_backup = mckp.get_apps_to_backup()
        pairs, evictions = build_sync_plan(app_db, to_backup)
        all_groups, orphans = mapping.group_by_source(pairs, evictions)

        if verbose:
            for eviction in evictions:
                print(
                    utils.colorize_message(
                        f"{eviction.evicted.dest} <- {eviction.evicted.source} "
                        f"({eviction.evicted.owner_app}) evicted by "
                        f"{eviction.winner.owner_app}",
                    ),
                )
            reported_orphans = set(orphans)
            for orphan in orphans:
                print(
                    utils.colorize_message(
                        f"{orphan} has no destination, left untouched",
                    ),
                )
            # A gated-out config's sources never entered the plan above (they
            # were never candidate pairs), so they cannot show up as evicted
            # orphans. They are an ordinary orphan all the same, unless some
            # other (enabled) config still claims the same source.
            for app_name in sorted(app_db.get_app_names()):
                if app_name not in to_backup or not app_db.app_has_sync(app_name):
                    continue
                if app_db.config_enabled(app_name):
                    continue
                for _local, backup in app_db.get_file_mappings(app_name):
                    if backup in all_groups or backup in reported_orphans:
                        continue
                    reported_orphans.add(backup)
                    print(
                        utils.colorize_message(
                            f"{backup} has no destination, left untouched",
                        ),
                    )

        planner = ApplicationProfile(mckp, dry_run, verbose)
        tombstoned = planner.read_tombstones()
        deletion_stats = planner.apply_tombstones(all_groups, tombstoned)

        live_pairs = [
            pair for pair in pairs
            if ApplicationProfile.normalize_relative_path(pair.dest)
            not in tombstoned
        ]
        groups, _ = mapping.group_by_source(live_pairs)
        # A group is synced in the slot of the config that won its last live
        # destination — the config whose declaration actually decided it.
        owners = mapping.group_owners(live_pairs)
        groups_by_owner: dict[str, list[tuple[str, list[str]]]] = {}
        for source, dests in groups.items():
            groups_by_owner.setdefault(owners[source], []).append((source, dests))

        for app_name in sorted(app_db.get_app_names()):
            if not app_db.config_enabled(app_name):
                if verbose:
                    print(
                        utils.colorize_message(
                            f"{app_name}: conditions not met on this machine",
                        ),
                    )
                continue
            env_files = app_db.get_env_files(app_name)
            cfg_blocks = app_db.get_blocks(app_name)
            pretty_name = app_db.get_name(app_name)

            tally = blocks.apply_blocks(cfg_blocks, "pre", env_files, dry_run)

            stats: dict[str, int] | None = None
            owned = groups_by_owner.get(app_name)
            if app_name in to_backup and app_db.app_has_sync(app_name):
                stats = ApplicationProfile.new_stats()
                if owned:
                    app = ApplicationProfile(mckp, dry_run, verbose)
                    print_app_header(app_name, pretty_name)
                    for source, dests in owned:
                        for key, value in app.sync_group(source, dests).items():
                            stats[key] += value

            tally += blocks.apply_blocks(cfg_blocks, "post", env_files, dry_run)
            report_config(pretty_name, stats, tally)

        if deletion_stats["deleted"]:
            print(
                utils.colorize_message(
                    f"Deleted {deletion_stats['deleted']} tombstoned path(s)",
                ),
            )
        if deletion_stats["errors"]:
            # Without this the run would report success while a tombstoned
            # path is still sitting on disk.
            print(
                utils.colorize_message(
                    f"Failed to delete {deletion_stats['errors']} "
                    "tombstoned path(s)",
                ),
            )

        # On consumer machines, load the synced dconf dumps into dconf.
        if role == "restore" and dconf_enabled:
            dconf.load_all(dry_run)

    # mackup mark <marker> / unmark <marker> / markers
    elif args["mark"] or args["unmark"] or args["markers"]:
        if args["markers"]:
            print(hooks.markers_report())
        else:
            marker_name: str = args["<marker>"]
            if not hooks.valid_marker_name(marker_name):
                die(
                    f"Invalid marker name: {marker_name!r} "
                    "(allowed: A-Z a-z 0-9 . _ -)",
                )
            if args["mark"]:
                hooks.set_marker(marker_name)
                known = hooks.load_marker_defs().get(marker_name)
                label = known.get("name") if known else None
                suffix = f" — {label}" if label else " (custom)"
                print(
                    utils.colorize_message(
                        f"Backed up marker '{marker_name}'{suffix}",
                    ),
                )
            else:
                hooks.unset_marker(marker_name)
                print(utils.colorize_message(f"Deleted marker '{marker_name}'"))

    # mackup dconf-add <path>...
    elif args["dconf-add"]:
        exit_code = dconf.add(args["<path>"], dry_run)
        if exit_code != 0:
            die(
                "No valid dconf path. Example: mackup dconf-add /org/gnome/terminal/",
            )

    # mackup apply — run every config's action blocks, without syncing files
    elif args["apply"]:
        mckp.check_for_usable_environment()
        for app_name in sorted(app_db.get_app_names()):
            if not app_db.config_enabled(app_name):
                continue
            env_files = app_db.get_env_files(app_name)
            cfg_blocks = app_db.get_blocks(app_name)
            tally = blocks.apply_blocks(cfg_blocks, "pre", env_files, dry_run)
            tally += blocks.apply_blocks(cfg_blocks, "post", env_files, dry_run)
            phrase = blocks.summarize(tally)
            if phrase:
                print(utils.colorize_message(
                    f"Applied {app_db.get_name(app_name)} ({phrase})",
                ))

    # mackup rm <path>...
    elif args["rm"]:
        mckp.check_for_usable_backup_env()

        rm_pairs, _ = build_sync_plan(app_db, mckp.get_apps_to_backup())
        managed_paths: dict[str, tuple[str, tuple[str, str]]] = {}
        for pair in rm_pairs:
            managed_paths.setdefault(
                ApplicationProfile.normalize_relative_path(pair.dest),
                (pair.owner_app, (pair.dest, pair.source)),
            )

        # Siblings are recomputed against the *live* (non-tombstoned) pairs on
        # every iteration, since an earlier <path> argument in this same `rm`
        # invocation may have just tombstoned the last other destination
        # feeding a shared source.
        rm_tombstones = ApplicationProfile(mckp, dry_run, verbose)
        tombstoned = rm_tombstones.read_tombstones()

        def find_descendant(
            requested_paths: list[str],
        ) -> tuple[str, str, str, str] | None:
            """(app, local root, backup root, relative) for a managed child."""
            for path in requested_paths:
                for app_name, (
                    local_root,
                    backup_root,
                ) in managed_paths.values():
                    relative = get_managed_descendant_relative(
                        path, local_root, backup_root,
                    )
                    if relative is not None:
                        return app_name, local_root, backup_root, relative
            return None

        for requested_arg in args["<path>"]:
            requested_paths = get_requested_path_candidates(requested_arg)
            if any(escapes_home(path) for path in requested_paths):
                die(f"Refusing to remove unmanaged path: {requested_arg}")

            match = next(
                (
                    managed_paths[path]
                    for path in requested_paths
                    if path in managed_paths
                ),
                None,
            )
            # (local root, backup root, relative) when the requested path is a
            # file *inside* a managed directory rather than a destination.
            descendant: tuple[str, str, str] | None = None
            if match is None:
                found = find_descendant(requested_paths)
                if found is None:
                    die(f"Unsupported or unmanaged path: {requested_arg}")
                app_name, local_root, backup_root, relative = found
                descendant = (local_root, backup_root, relative)
                match = (
                    app_name,
                    (
                        os.path.normpath(os.path.join(local_root, relative)),
                        os.path.normpath(os.path.join(backup_root, relative)),
                    ),
                )

            matching_app_name, matching_mapping = match
            local_filename, backup_filename = matching_mapping
            pretty_name = app_db.get_name(matching_app_name)
            live_pairs = [
                pair for pair in rm_pairs
                if ApplicationProfile.normalize_relative_path(pair.dest)
                not in tombstoned
            ]
            rm_groups, _ = mapping.group_by_source(live_pairs)
            app = ApplicationProfile(mckp, dry_run, verbose)
            print_app_header(matching_app_name, pretty_name)
            if descendant is not None:
                # Members of a fanout group mirror each other, so the file has
                # to go from every destination of the group and from the
                # shared source; leaving a sibling copy behind would let the
                # next directory merge resurrect it.
                local_root, backup_root, relative = descendant
                siblings = []
                app_stats = app.remove_group_descendant(
                    backup_root,
                    rm_groups.get(backup_root) or [local_root],
                    relative,
                    local_filename,
                )
            else:
                normalized_local = ApplicationProfile.normalize_relative_path(
                    local_filename,
                )
                siblings = [
                    dest for dest in rm_groups.get(backup_filename, [])
                    if ApplicationProfile.normalize_relative_path(dest)
                    != normalized_local
                ]
                app_stats = app.remove_destination(
                    backup_filename, local_filename, len(siblings),
                )
            if app_stats["errors"] == 0 and app_stats["deleted"]:
                tombstoned.add(
                    ApplicationProfile.normalize_relative_path(local_filename),
                )
            rm_action = get_action_label(app_stats)
            if rm_action is not None:
                print(
                    utils.colorize_message(
                        f"{rm_action} {local_filename} ({pretty_name})",
                    ),
                )
            if siblings:
                print(
                    utils.colorize_message(
                        f"{backup_filename} still feeds "
                        f"{len(siblings)} destination(s)",
                    ),
                )
