# AGENTS.md

## Purpose

This fork of `mackup` adds path templating in application config definitions
(`src/mackup_ng/applications/*.cfg`). When editing or adding app configs, use the
fork features below to avoid duplicated platform-specific entries.

## Fork Path Templating (cfg files)

Path templating is supported in `[configuration_files]` entries.

### Built-in variables

Use these Mackup-specific variables (not OS env vars):

- `@CONFIG@` -> `.config` (Linux) / `Library/Application Support` (macOS) / `AppData/Roaming` (Windows)
- `@DATA@` -> `.local/share` (Linux) / `Library/Application Support` (macOS) / `AppData/Local` (Windows)
- `@STATE@` -> `.local/state` (Linux) / `Library/Application Support` (macOS) / `AppData/Local` (Windows)
- `@CACHE@` -> `.cache` (Linux) / `Library/Caches` (macOS) / `AppData/Local` (Windows)

### Platform selector

Use `[...]` to choose a path fragment by platform:

- Syntax: `[linux:...,mac:...,windows:...,fallback]`
- The last unkeyed item is the fallback.
- The fallback is also the canonical backup path used in the Mackup storage.

Selector semantics in this fork:

- local path = platform-specific value (or fallback if no matching key)
- backup path = fallback (or selected path if no fallback is provided)
- built-in variables in backup paths are resolved to Linux canonical values
  (`@CONFIG@ -> .config`, `@DATA@ -> .local/share`, etc.)

Examples:

- `@CONFIG@/[mac:Blender,blender]`
- `@CONFIG@/[mac:Sublime Text 3,sublime-text-3]/Packages/User`
- `[mac:@CONFIG@/MyApp/config.json,linux:@CONFIG@/myapp/config.json,@CONFIG@/shared/myapp-config.json]`

### Brace expansion

Use `{...}` to define multiple entries in one line:

- `@CONFIG@/Code/User/{snippets,keybindings.json,settings.json}`

Brace groups are expanded recursively (cartesian product when multiple groups
are present).

## Processing Order

Paths are resolved in this order:

1. Platform selector `[...]`
   Produces local path and canonical backup path
2. Built-in variables (`@CONFIG@`, `@DATA@`, `@STATE@`, `@CACHE@`)
   Local path uses current OS mapping; backup path uses Linux canonical mapping
3. Brace expansion `{...}`

## Config Style (This Fork)

- Prefer `[configuration_files]` only.
- `xdg_configuration_files` is unsupported in this fork. Use `@CONFIG@/...` in `[configuration_files]`.
- Prefer a single templated line over duplicated macOS/Linux entries when
  semantics are the same.
- If macOS path naming differs, prefer a selector inside `@CONFIG@`, e.g.
  `@CONFIG@/[mac:Foo App,foo-app]`.
- Keep `Library/Preferences/...` entries as-is unless there is a clear
  cross-platform equivalent.

## Safety Notes

- Paths that start with selectors (e.g. `[mac:...,fallback]/...`) are supported
  by this fork, but they rely on fork-specific parsing logic in
  `src/mackup_ng/appsdb.py`.
- Upstream Mackup may not support these templates.
