"""mackup-ng.

Keep your application settings in sync.
A maintained fork of mackup by Laurent Raufaste <http://glop.org/>.
Copyright (C) 2013-2025 Laurent Raufaste, Grigorii Horos.

Usage:
  mackup-ng [options] list
  mackup-ng [options] show <application>
  mackup-ng [options] sync
  mackup-ng [options] rm <path>...
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
 - mackup-ng rm: remove a managed config file locally and from the remote folder.

By default, mackup-ng syncs all application data via
Dropbox, but may be configured to exclude applications or use a different
backend with a .mackup.cfg file.

See https://github.com/grigorii-horos/mackup-ng/tree/master/doc for more information.

"""

import os
import sys
from typing import Any, Optional

from docopt import docopt

from . import utils
from .application import ApplicationProfile
from .appsdb import ApplicationsDatabase
from .constants import VERSION
from .mackup import Mackup


class ColorFormatCodes:
    BLUE = "\033[34m"
    BOLD = "\033[1m"
    NORMAL = "\033[0m"


def header(text: str) -> str:
    return ColorFormatCodes.BLUE + text + ColorFormatCodes.NORMAL


def bold(text: str) -> str:
    return ColorFormatCodes.BOLD + text + ColorFormatCodes.NORMAL


def get_action_label(stats: dict[str, int]) -> Optional[str]:
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


def main() -> None:
    """Main function."""
    # Get the command line arg
    docstring = __doc__
    if not docstring:
        sys.exit(
            "Usage information is not available because __doc__ is None. "
            "This can happen when running Python with optimizations (python -OO). "
            "Please run Mackup without -OO to use the command-line interface.",
        )
    assert docstring is not None  # for type narrowing after sys.exit

    args: dict[str, Any] = docopt(docstring, version=f"mackup-ng {VERSION}")

    if args["--force"] and args["--force-no"]:
        sys.exit("Options --force and --force-no are mutually exclusive.")

    config_file: Optional[str] = args.get("--config-file")
    mckp: Mackup = Mackup(config_file)
    app_db: ApplicationsDatabase = ApplicationsDatabase()

    def print_app_header(app_name: str, pretty_name: str) -> None:
        if verbose:
            header_str = header("---")
            print(f"\n{header_str} {bold(f'{app_name}: {pretty_name}')} {header_str}")

    def print_app_result(stats: dict[str, int], app_name: str, pretty_name: str) -> None:
        action = get_action_label(stats)
        if action is None:
            return
        print(utils.colorize_message(f"{action} {pretty_name}"))

    def get_requested_path_candidates(path: str) -> list[str]:
        candidates = [ApplicationProfile.normalize_relative_path(path)]
        if not os.path.isabs(os.path.expanduser(path)):
            absolute_path = os.path.abspath(path)
            home = os.path.abspath(os.environ["HOME"])
            try:
                candidates.append(
                    ApplicationProfile.normalize_relative_path(
                        os.path.relpath(absolute_path, home),
                    ),
                )
            except ValueError:
                pass
        return list(dict.fromkeys(candidates))

    def is_managed_directory(local_filename: str, backup_filename: str) -> bool:
        return os.path.isdir(
            os.path.join(os.environ["HOME"], local_filename),
        ) or os.path.isdir(
            os.path.join(mckp.mackup_folder, backup_filename),
        )

    def get_managed_descendant_mapping(
        requested_path: str,
        local_filename: str,
        backup_filename: str,
    ) -> Optional[tuple[str, str]]:
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

        return (
            os.path.normpath(os.path.join(local_filename, relative_path)),
            os.path.normpath(os.path.join(backup_filename, relative_path)),
        )

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
        output: str = "Supported applications:\n"
        for app_name in sorted(app_db.get_app_names()):
            output += f" - {app_name}\n"
        output += "\n"
        output += (
            f"{len(app_db.get_app_names())} applications supported in "
            f"mackup-ng v{VERSION}"
        )
        print(output)

    # mackup show <application>
    elif args["show"]:
        mckp.check_for_usable_environment()
        requested_app_name: str = args["<application>"]

        # Make sure the app exists
        if requested_app_name not in app_db.get_app_names():
            sys.exit(f"Unsupported application: {requested_app_name}")
        print(f"Name: {app_db.get_name(requested_app_name)}")
        print("Configuration files:")
        for file in app_db.get_files(requested_app_name):
            print(f" - {file}")

    # mackup sync
    elif args["sync"]:
        mckp.check_for_usable_backup_env()

        # Synchronize in two phases:
        # one pass per file: decide direction by mtime and do one action.
        for app_name in sorted(mckp.get_apps_to_backup()):
            pretty_name = app_db.get_name(app_name)
            app = ApplicationProfile(mckp, app_db.get_file_mappings(app_name), dry_run, verbose)
            print_app_header(app_name, pretty_name)
            stats = app.sync_files()
            print_app_result(stats, app_name, pretty_name)

    # mackup rm <path>...
    elif args["rm"]:
        mckp.check_for_usable_backup_env()

        managed_paths: dict[str, tuple[str, tuple[str, str]]] = {}
        for app_name in sorted(mckp.get_apps_to_backup()):
            for local_filename, backup_filename in sorted(
                app_db.get_file_mappings(app_name),
            ):
                managed_paths.setdefault(
                    ApplicationProfile.normalize_relative_path(local_filename),
                    (app_name, (local_filename, backup_filename)),
                )

        for requested_arg in args["<path>"]:
            requested_paths = get_requested_path_candidates(requested_arg)
            if any(
                path == ".."
                or path.startswith("../")
                or path.startswith("..\\")
                or path.startswith("/")
                for path in requested_paths
            ):
                sys.exit(f"Refusing to remove unmanaged path: {requested_arg}")

            match = next(
                (
                    managed_paths[path]
                    for path in requested_paths
                    if path in managed_paths
                ),
                None,
            )
            if match is None:
                descendant_match = next(
                    (
                        (app_name, descendant_mapping)
                        for path in requested_paths
                        for app_name, (
                            local_filename,
                            backup_filename,
                        ) in managed_paths.values()
                        if (
                            descendant_mapping := get_managed_descendant_mapping(
                                path,
                                local_filename,
                                backup_filename,
                            )
                        )
                        is not None
                    ),
                    None,
                )
                if descendant_match is None:
                    sys.exit(f"Unsupported or unmanaged path: {requested_arg}")
                match = descendant_match

            matching_app_name, matching_mapping = match
            pretty_name = app_db.get_name(matching_app_name)
            app = ApplicationProfile(mckp, {matching_mapping}, dry_run, verbose)
            print_app_header(matching_app_name, pretty_name)
            app_stats = app.remove_file(*matching_mapping)
            action = get_action_label(app_stats)
            if action is not None:
                print(
                    utils.colorize_message(
                        f"{action} {matching_mapping[0]} ({pretty_name})",
                    ),
                )
