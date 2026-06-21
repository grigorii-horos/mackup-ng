# Detailed install instructions for mackup-ng

mackup-ng is distributed on PyPI as `mackup-ng`. Install it with either
[pip](https://pip.pypa.io/en/stable/) or [uv](https://docs.astral.sh/uv/).

> ⚠️ **Do not install alongside the original `mackup`.** mackup-ng provides a
> `mackup` command that shadows the upstream one; installing both in the same
> environment leaves whichever was installed last on `PATH`. If you already
> have the original mackup, uninstall it first (`pip uninstall mackup`).

## Install

### With pip

```bash
pip install mackup-ng

# Now you can run it
mackup-ng -h
```

### With uv (isolated tool)

```bash
uv tool install mackup-ng

mackup-ng -h
```

### Latest development version from GitHub

```bash
pip install "git+https://github.com/grigorii-horos/mackup-ng.git"

mackup-ng -h
```

## Upgrade

### Upgrade with pip

```bash
pip install --upgrade mackup-ng
mackup-ng -h
```

### Upgrade with uv

```bash
uv tool upgrade mackup-ng
mackup-ng -h
```

## Uninstall

### Uninstall with pip

```bash
pip uninstall mackup-ng
```

### Uninstall with uv

```bash
uv tool uninstall mackup-ng
```
