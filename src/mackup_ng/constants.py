"""Constants used in Mackup."""

import os
from importlib.metadata import PackageNotFoundError, version

# Support platforms
PLATFORM_DARWIN: str = "Darwin"
PLATFORM_LINUX: str = "Linux"
PLATFORM_WINDOWS: str = "Windows"

# Directory containing the application configs
APPS_DIR: str = "applications"

# Name of the mackup directory inside each XDG base
MACKUP_DIRNAME: str = "mackup"

# Main config file, inside $XDG_CONFIG_HOME/mackup/
CONFIG_FILENAME: str = "config.toml"

# Pre-XDG locations, kept only to reject them with a helpful message
LEGACY_CONFIG_FILE: str = ".mackup.cfg"
LEGACY_HOME_DIR: str = ".mackup"

# Distribution name (used for version lookup via importlib.metadata)
MACKUP_APP_NAME: str = "mackup-ng"

# Default Mackup backup path where it stores its files in Dropbox
MACKUP_BACKUP_PATH: str = "Mackup"


def get_version() -> str:
    """Return package version, or a safe fallback when metadata is unavailable."""
    try:
        return version(MACKUP_APP_NAME)
    except PackageNotFoundError:
        return "unknown"


# Current version
VERSION: str = get_version()

# Mackup home directory (under $HOME): custom apps + hooks + markers + sets + state
MACKUP_HOME_DIR: str = ".mackup"

# Directories holding ignore definitions (*.toml with an [ignore] table):
# built-ins ship in the package, local ones sit next to the custom app configs.
IGNORES_DIRNAME: str = "ignores"

# Sub-directories under the Mackup home (~/.mackup/)
MARKERS_DIRNAME: str = "markers"  # marker definitions (and legacy state)
DCONF_DIRNAME: str = "dconf-backup"  # dconf dumps (*.dconf)

# Marker DEFINITIONS (name + order), one *.toml per marker, like apps:
# built-in ones ship in the package, local ones live under ~/.mackup/markers/.
MARKERS_DEFS_DIRNAME: str = "markers"  # package built-ins
CUSTOM_MARKERS_DIR: str = os.path.join(MACKUP_HOME_DIR, MARKERS_DIRNAME)  # local defs

# Marker STATE (on/off flags) is machine-local runtime state -> XDG_STATE_HOME.
MARKERS_STATE_XDG: str = "mackup/markers"  # relative to $XDG_STATE_HOME
# Pre-XDG state lived alongside the defs in ~/.mackup/markers/ (flag files, no
# extension); migrated out to the XDG dir, leaving *.toml definitions in place.
LEGACY_MARKERS_STATE_DIR: str = os.path.join(MACKUP_HOME_DIR, MARKERS_DIRNAME)

# Supported engines
ENGINE_DROPBOX: str = "dropbox"
ENGINE_FS: str = "file_system"
ENGINE_GDRIVE: str = "google_drive"
ENGINE_ICLOUD: str = "icloud"

DOCUMENTATION_URL: str = (
    "https://github.com/grigorii-horos/mackup-ng/blob/master/doc/README.md"
)

# Error message displayed when mackup can't find the storage specified
# in the config (or the default one).
ERROR_UNABLE_TO_FIND_STORAGE: str = (
    "Unable to find your {provider} =(\n"
    f"If this is the first time you use {MACKUP_APP_NAME}, you may want "
    "to use another provider.\n"
    "Take a look at the documentation [1] to know more about "
    "how to configure mackup.\n\n"
    f"[1]: {DOCUMENTATION_URL}"
)
