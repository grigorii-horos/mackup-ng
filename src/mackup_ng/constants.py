"""Constants used in Mackup."""

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


def get_version() -> str:
    """Return package version, or a safe fallback when metadata is unavailable."""
    try:
        return version(MACKUP_APP_NAME)
    except PackageNotFoundError:
        return "unknown"


# Current version
VERSION: str = get_version()

# Directories holding ignore definitions (*.toml with an [ignore] table):
# built-ins ship in the package, local ones sit next to the custom app configs.
IGNORES_DIRNAME: str = "ignores"

# "markers" names both the marker DEFINITIONS dir (under $XDG_CONFIG_HOME) and
# the marker STATE dir (under $XDG_STATE_HOME) — see dirs.py for the split.
MARKERS_DIRNAME: str = "markers"
DCONF_DIRNAME: str = "dconf-backup"  # dconf dumps (*.dconf)

# Marker DEFINITIONS (name + order), one *.toml per marker, like apps:
# built-in ones ship in the package, local ones live under
# $XDG_CONFIG_HOME/mackup/markers/.
MARKERS_DEFS_DIRNAME: str = "markers"  # package built-ins

DOCUMENTATION_URL: str = (
    "https://github.com/grigorii-horos/mackup-ng/blob/master/doc/README.md"
)

