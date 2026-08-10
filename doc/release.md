# Release

Releasing is now automated. Pushing a version tag triggers the
[`release` workflow](../.github/workflows/release.yaml), which builds the
package, publishes it to PyPI, and creates the GitHub release.

To cut a release, run one command. The version is `X.Y.Z`
(`major.minor.patch`), and the `BUMP` you choose increments one number and
resets the numbers to its right to `0`:

```sh
make release              # patch: 0.10.3 -> 0.10.4 (bug fixes)
make release BUMP=minor   # minor: 0.10.3 -> 0.11.0 (new features)
make release BUMP=major   # major: 0.10.3 -> 1.0.0  (breaking changes)
make release VERSION=1.2.3  # pin an exact version, no arithmetic
```

Preview a bump without changing anything with `uv version --bump <level>
--dry-run`.

This runs the checks, bumps the version, syncs the lockfile, then commits, tags
and pushes. The workflow takes over from the tag push:

- Builds the package with `uv build`
- Publishes it to PyPI with `uv publish`
- Creates the GitHub release with auto-generated notes and the built artifacts

## Publishing by hand

The workflow publishes through PyPI Trusted Publishing, which needs a publisher
registered on pypi.org for `grigorii-horos/mackup-ng`, workflow `release.yaml`,
environment `release`. Until that exists, `uv publish` in CI fails with
`Missing credentials for https://upload.pypi.org/legacy/` and the release has to
be published from a workstation.

**Pass the token through the environment — never on the command line and never
pasted into a chat, an issue or a commit.** A token on the command line lands in
the shell history and in the process list, where any other process on the
machine can read it.

Keep it in an environment variable exported by your shell profile, or in a
file only you can read:

```sh
umask 077; printf '%s' 'pypi-...' > ~/.pypi-token   # once, by hand
```

Then, from a clean checkout of the tag:

```sh
rm -rf dist
uv build
UV_PUBLISH_TOKEN=$(cat ~/.pypi-token) uv publish
rm -rf dist
```

`uv publish` reads `UV_PUBLISH_TOKEN` on its own; there is no `--token` flag to
type. Verify the upload before announcing it — PyPI's JSON API caches for a few
seconds, so check twice if the first call still shows the old version:

```sh
curl -s https://pypi.org/pypi/mackup-ng/json |
  python3 -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
```

A published version can never be reused, even after deleting the release. If a
token is ever exposed, revoke it on pypi.org and issue a new one.
