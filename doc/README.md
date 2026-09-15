# Configuration

> 💡 **New to Mackup?** Check out the
> [Architecture Guide](ARCHITECTURE.md) to understand how Mackup works
> under the hood.

All the configuration is done in a file named `config.toml` stored in
`$XDG_CONFIG_HOME/mackup/` (`~/.config/mackup/` unless you set the variable).

```bash
vi ~/.config/mackup/config.toml
```

## Configuration file location

`mackup --config-file=<path>` reads a different file instead; the path may be
absolute or relative to your home directory, and must lie inside it.

```bash
mackup-ng --config-file=~/.config/mackup-custom.toml sync
```

## Setting up a machine

```sh
mackup-ng init ~/Sync/Configs/Mackup
```

writes `config.toml` for you and runs the first sync, with the backup folder
outranking whatever is already in your home folder. Use it once per machine;
on a machine that already has a config it refuses rather than repointing it.

The rest of this page describes that config file, which you can also write by
hand.

## Storage

Mackup backs your configuration files up into one folder, and restores them
from it. That folder is the only storage setting there is:

```toml
[storage]
backup_dir = "Sync/Configs/Mackup"
```

A relative path is resolved against your home directory, so the example above
means `~/Sync/Configs/Mackup`. An absolute path is used exactly as written:

```toml
[storage]
backup_dir = "/mnt/backup/Mackup"
```

`backup_dir` is required. There is no default and nothing is auto-detected —
if it is missing, mackup stops and says so rather than guessing a location.

The folder _containing_ `backup_dir` must already exist. Mackup creates the
backup folder itself, on confirmation, but will not build a whole directory
tree out of what may be a typo.

Point it wherever your own syncing happens — a Syncthing share, a mounted
drive, a cloud provider's local folder, or a plain second directory you copy
elsewhere yourself. Mackup does not talk to any sync service; it only reads
and writes that one folder.

### Switching storage

Move the existing backup folder to its new location, then update
`backup_dir` to match. Mackup does not move it for you, and pointing
`backup_dir` at an empty folder makes the next sync treat your machine as the
only source of truth.

## Applications

### Only sync one or two applications

In `config.toml`, add the application names to allow in the `sync` list under
`[applications]`.

```toml
# Example, to only sync SSH and Adium:
[applications]
sync = ["ssh", "adium"]
```

Use `mackup-ng list` to get a list of valid application names.

A [sample](config.toml) of this file is available in this folder. Just copy it
to `~/.config/mackup/`:

```bash
mkdir -p ~/.config/mackup
cp mackup-ng/doc/config.toml ~/.config/mackup/config.toml
```

### Don't sync an application

In `config.toml`, add the application names to ignore in the `ignore` list
under `[applications]`.

```toml
# Example, to not sync SSH and Adium:
[applications]
ignore = ["ssh", "adium"]
```

Use `mackup-ng list` to get a list of valid application names.

A [sample](config.toml) of this file is available in this folder. Just copy it
to `~/.config/mackup/`:

```bash
mkdir -p ~/.config/mackup
cp mackup-ng/doc/config.toml ~/.config/mackup/config.toml
```

### Get official support for an application

Open a [new issue](https://github.com/grigorii-horos/mackup-ng/issues) and ask for it, or
fork Mackup and open a
[Pull Request](https://help.github.com/articles/using-pull-requests).
The stock application configs are in the `mackup_ng/applications` directory.

Remember to follow the guidelines in [CONTRIBUTING.md](https://github.com/grigorii-horos/mackup-ng/blob/master/.github/CONTRIBUTING.md)
to get your Pull Request merged faster.

### Add support for an application or (almost) any file or directory

You can customize the Mackup engine and add support for unsupported
applications or just custom files and directories you'd like to sync.

NOTE: Files and directories to be synced should be rooted at $HOME.

Let's say that you'd like to add support for Nethack (config file:
`.nethackrc`), for the `bin` and `.hidden` directories and for the
`.gitignore` file you keep in your home.

Create the applications directory and add a config file for the application
you'd like to support:

```bash
mkdir -p ~/.config/mackup/applications
touch ~/.config/mackup/applications/nethack.toml
touch ~/.config/mackup/applications/my-files.toml
```

#### Custom applications directory location

Custom application configs live in `$XDG_CONFIG_HOME/mackup/applications/`
(`~/.config/mackup/applications/` unless you set the variable).

Edit those files:

```toml
# ~/.config/mackup/applications/nethack.toml
name = "Nethack"
files = [
    ".nethackrc",
]
```

```toml
# ~/.config/mackup/applications/my-files.toml
name = "My personal synced files and dirs"
files = [
    "bin",
    ".hidden",
    ".gitignore",
]
```

Note that Mackup assumes the file paths listed here are relative to your home
directory.

You can run mackup to see if they are listed:

```bash
$ mackup-ng list
Supported applications:
[...]
 - my-files
 - nethack
[...]
```

All good, you can now sync your newly configured files:

```bash
mackup-ng sync
```

If you override an application config that is already supported by Mackup, your
new config for this application will replace the one provided by Mackup.

You can find some sample configs in the [config](config) directory.

### Locally test an application before submitting a Pull Request

You can add and test an application by following these steps:

- fork this project
- create a branch _(usually containing the name of the application)_
- add the appropriate application `.toml` config file in the `mackup_ng/applications` folder
- from the top-most folder _(mackup)_ run `make develop` that replaces the
  currently installed mackup with the local modified one
- simply run `mackup-ng sync` to test if everything is ok
- if everything works as expected:
  - run `make undevelop` to revert to the official version
  - commit and push the change to your fork and then create the Pulls Request

### Add support for an application using the XDG directory

For applications storing their configuration under the `~/.config` folder, you
should not hardcode it. The `.config` folder is the default location but it can
be named differently on other users' systems by setting the `XDG_CONFIG_HOME`
environment variable.

See <https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html>

This fork supports this mechanism with the `${MACKUP_XDG_CONFIG}` built-in variable in
the `files` array.

If any path starts with `.config`, replace that prefix with `${MACKUP_XDG_CONFIG}`.

Instead of:

```toml
name = "Git"
files = [
    ".gitconfig",
    "${MACKUP_XDG_CONFIG}/git/config",
    "${MACKUP_XDG_CONFIG}/git/ignore",
    "${MACKUP_XDG_CONFIG}/git/attributes",
]
```
