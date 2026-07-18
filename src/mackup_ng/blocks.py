"""Unified action-block executor for mackup-ng config files.

A block is a dict with a ``type`` (copy / chmod / run / mutate_xml /
systemd_dropin), an optional ``phase`` (pre/post, default post), a uniform set
of conditions (see :mod:`mackup_ng.conditions`), optional per-block
``restart_service`` bracketing, and type-specific fields.

Blocks are applied per config during ``mackup sync`` (pre before that config's
file sync, post after) and by ``mackup apply``.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter

from . import conditions, hooks, utils


def _msg(text: str) -> None:
    print(utils.colorize_message(text))


# ---------------------------------------------------------------- value resolution
_VAR_RE = re.compile(r"^\$\{?(\w+)\}?$")


def _source_and_get(env_file: str, name: str) -> str | None:
    path = os.path.expanduser(env_file)
    if not os.path.isfile(path):
        return None
    try:
        out = subprocess.run(
            [
                "bash",
                "-c",
                f'set -a; source "$1" >/dev/null 2>&1; printf "%s" "${{{name}}}"',
                "bash",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout or None
    except OSError:
        return None


def resolve_value(val: object, env_files: list[str]) -> str | None:
    """Return literal, or env/${VAR} resolved value, or None if unresolved."""
    match = _VAR_RE.match(str(val))
    if not match:
        return str(val)
    name = match.group(1)
    if os.environ.get(name):
        return os.environ[name]
    for env_file in env_files:
        got = _source_and_get(env_file, name)
        if got:
            return got
    return None


# ---------------------------------------------------------------- xml helpers
def ensure_path(root: ET.Element, path: str) -> ET.Element:
    cur = root
    for seg in path.split("/"):
        nxt = cur.find(seg)
        if nxt is None:
            nxt = ET.SubElement(cur, seg)
        cur = nxt
    return cur


def targets_for(root: ET.Element, mutate: dict) -> list[ET.Element]:
    selects = mutate.get("select") or [""]
    out: list[ET.Element] = []
    for sel in selects:
        if sel == "":
            out.append(root)
            continue
        found = root.findall(sel)
        if not found and mutate.get("create_parents"):
            found = [ensure_path(root, sel)]
        out.extend(found)
    return out


def apply_mutate(root: ET.Element, mutate: dict, env_files: list[str]) -> None:
    for el in targets_for(root, mutate):
        for attr, raw in (mutate.get("set_attr") or {}).items():
            value = resolve_value(raw, env_files)
            if value is None:
                _msg(f"Warning: cannot resolve value for @{attr}, skip")
                continue
            el.set(attr, value)
        for tag, raw in (mutate.get("set_child") or {}).items():
            value = resolve_value(raw, env_files)
            if value is None:
                _msg(f"Warning: cannot resolve value for <{tag}>, skip")
                continue
            child = el.find(tag)
            if child is None:
                if not mutate.get("create_missing"):
                    continue
                child = ET.SubElement(el, tag)
            child.text = value


def atomic_write(path: str, tree: ET.ElementTree[ET.Element[str]]) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".xml.", suffix=".tmp")
    os.close(fd)
    try:
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- service control
def _sysd(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, check=False,
    )


def _brew(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["brew", "services", *args], capture_output=True, text=True, check=False,
    )


def service_manager() -> str | None:
    """The native user-service manager for this OS, or None if unavailable."""
    kind = hooks.os_kind()
    if kind == "linux" and shutil.which("systemctl"):
        return "systemctl"
    if kind == "macos" and shutil.which("brew"):
        return "brew"
    return None


def svc_is_active(svc: str | None) -> bool:
    if not svc:
        return False
    mgr = service_manager()
    if mgr == "systemctl":
        return _sysd("is-active", "--quiet", svc).returncode == 0
    if mgr == "brew":
        for line in _brew("list").stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == svc:
                return len(parts) > 1 and parts[1] == "started"
    return False


def svc_stop(svc: str) -> None:
    mgr = service_manager()
    if mgr == "systemctl":
        _sysd("stop", svc)
    elif mgr == "brew":
        _brew("stop", svc)


def svc_start(svc: str) -> None:
    mgr = service_manager()
    if mgr == "systemctl":
        _sysd("start", svc)
    elif mgr == "brew":
        _brew("start", svc)


def dropin_content(block: dict) -> str:
    lines = ["[Service]"]
    lines.extend(f"Environment={env}" for env in block.get("Environment", []))
    lines.extend(
        f"{key}={block[key]}"
        for key in ("MemoryMax", "CPUQuota", "Nice")
        if key in block
    )
    return "\n".join(lines) + "\n"


def dropin_path(block: dict) -> str:
    svc = block["service"]
    name = block.get("name", "mackup-set")
    xdg = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.environ["HOME"], ".config"),
    )
    return os.path.join(xdg, "systemd", "user", f"{svc}.service.d", f"{name}.conf")


# ---------------------------------------------------------------- copy
def _expand_path(raw: object, env: dict[str, str]) -> str:
    """Expand ``~`` and ``$VAR`` / ``${VAR}`` in a path using ``env``."""
    text = os.path.expanduser(str(raw))
    return re.sub(
        r"\$\{(\w+)\}|\$(\w+)",
        lambda m: env.get(m.group(1) or m.group(2), ""),
        text,
    )


def pending_copies(copies: list[dict], env: dict[str, str]) -> list[tuple[str, str]]:
    """Resolve copy blocks to (src, dst) pairs that need writing (idempotent)."""
    out: list[tuple[str, str]] = []
    for block in copies:
        src = _expand_path(block.get("from", ""), env)
        dst = _expand_path(block.get("to", ""), env)
        if not src or not dst:
            continue
        if os.path.isdir(src):
            out.append((src, dst))  # directory merge — always applied
            continue
        if not os.path.isfile(src):
            continue
        with open(src, "rb") as handle:
            new = handle.read()
        old = None
        if os.path.isfile(dst) and not os.path.islink(dst):
            with open(dst, "rb") as handle:
                old = handle.read()
        if new != old:
            out.append((src, dst))
    return out


def write_copy(src: str, dst: str) -> None:
    """Copy ``src`` -> ``dst`` preserving mode (dir merges; file unlinks first)."""
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=shutil.copy2)
        return
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    if os.path.lexists(dst):
        os.unlink(dst)
    shutil.copy2(src, dst)


# ---------------------------------------------------------------- chmod
def _parse_mode(spec: str, current: int) -> int:
    """Octal (``"700"``) absolute, or symbolic-add (``"+x"``) on top of current."""
    spec = str(spec)
    if spec.startswith("+"):
        add = 0
        if "x" in spec:
            add |= 0o111
        if "r" in spec:
            add |= 0o444
        if "w" in spec:
            add |= 0o222
        return current | add
    return int(spec, 8)


def _chmod_targets(block: dict, env: dict[str, str]):
    """Yield (path, mode_spec) the chmod block wants to set."""
    roots = block.get("paths")
    if roots is None:
        roots = [block["path"]] if block.get("path") else []
    mode = block.get("mode")
    dir_mode = block.get("dir_mode")
    file_mode = block.get("file_mode")
    recursive = block.get("recursive", False)
    for raw in roots:
        for root in glob.glob(_expand_path(raw, env)):
            if mode:
                yield root, mode
            if recursive and os.path.isdir(root):
                for dirpath, _dirnames, filenames in os.walk(root):
                    if dir_mode:
                        yield dirpath, dir_mode
                    if file_mode:
                        for name in filenames:
                            yield os.path.join(dirpath, name), file_mode
            else:
                if dir_mode and os.path.isdir(root):
                    yield root, dir_mode
                if file_mode and os.path.isfile(root):
                    yield root, file_mode


def pending_chmods(chmods: list[dict], env: dict[str, str]) -> list[tuple[str, int]]:
    """Resolve chmod blocks to (path, mode) pairs that need changing (idempotent)."""
    out: list[tuple[str, int]] = []
    for block in chmods:
        for path, spec in _chmod_targets(block, env):
            try:
                current = os.stat(path).st_mode & 0o7777
            except OSError:
                continue
            target = _parse_mode(spec, current)
            if target != current:
                out.append((path, target))
    return out


def _resolve_files(spec: dict) -> list[str]:
    """Expand an xml action's ``paths`` / ``paths_mode`` to existing files."""
    mode = spec.get("paths_mode", "first")
    existing = [os.path.expanduser(p) for p in spec.get("paths", [])]
    existing = [p for p in existing if os.path.isfile(p)]
    if mode == "all":
        return existing
    return existing[:1]


# ---------------------------------------------------------------- action handlers
# Each handler takes the action's own sub-table (spec), applies it quietly, and
# returns the number of changes made (0 = nothing to do). The caller renders one
# summary line per config; handlers do not print per item.
def _apply_copy(spec: dict, env_files: list[str], dry_run: bool) -> int:
    env = hooks.hook_env("restore")
    pending = pending_copies([spec], env)
    if not dry_run:
        for src, dst in pending:
            write_copy(src, dst)
    return len(pending)


def _apply_chmod(spec: dict, env_files: list[str], dry_run: bool) -> int:
    env = hooks.hook_env("restore")
    pending = pending_chmods([spec], env)
    if not dry_run:
        for path, mode in pending:
            os.chmod(path, mode)
    return len(pending)


def _apply_run(spec: dict, env_files: list[str], dry_run: bool) -> int:
    env = hooks.hook_env("restore")
    script = spec.get("script")
    commands = spec.get("commands")
    if isinstance(commands, str):
        commands = [commands]
    if not script and not commands:
        return 0
    if dry_run:
        return 1
    for cmd in commands or [script]:  # stop at first failure (like set -e)
        result = subprocess.run(
            [spec.get("shell", "bash"), "-c", cmd], env=env, check=False,
        )
        if result.returncode != 0:
            _msg(f"Warning: block run failed (code {result.returncode})")
            break
    return 1


def _apply_xml(spec: dict, env_files: list[str], dry_run: bool) -> int:
    changed = 0
    for path in _resolve_files(spec):
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            _msg(f"Warning: cannot parse {path}: {exc}, skip")
            continue
        root = tree.getroot()
        before = ET.tostring(root, encoding="utf-8")
        apply_mutate(root, spec, env_files)
        if ET.tostring(root, encoding="utf-8") != before:
            changed += 1
            if not dry_run:
                atomic_write(path, tree)
    return changed


def _apply_systemd(spec: dict, env_files: list[str], dry_run: bool) -> int:
    target = dropin_path(spec)
    content = dropin_content(spec)
    old = ""
    if os.path.isfile(target):
        with open(target) as handle:
            old = handle.read()
    if old == content:
        return 0
    if not dry_run:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as handle:
            handle.write(content)
        if service_manager() == "systemctl":
            _sysd("daemon-reload")
    return 1


# Action name -> handler. A block carries exactly one of these as a sub-table;
# its presence selects the action (no `type` key needed).
_HANDLERS = {
    "copy": _apply_copy,
    "chmod": _apply_chmod,
    "run": _apply_run,
    "xml": _apply_xml,
    "systemd": _apply_systemd,
}
ACTION_NAMES = frozenset(_HANDLERS)


def block_action(block: dict) -> str | None:
    """Return the single action name present in a block, or None."""
    present = [a for a in ACTION_NAMES if isinstance(block.get(a), dict)]
    if len(present) == 1:
        return present[0]
    return None


def apply_block(block: dict, env_files: list[str], dry_run: bool) -> tuple[str, int]:
    """Apply one block's action; return (action, change_count).

    Base keys (phase, conditions, restart_service) live on ``block``; the action
    parameters live in the ``block[<action>]`` sub-table.
    """
    action = block_action(block)
    if action is None:
        _msg(f"Warning: block has no (single) action sub-table, skip: {block!r}")
        return ("", 0)
    svc = block.get("restart_service")
    was_active = svc_is_active(svc) if svc else False
    if was_active and not dry_run:
        svc_stop(svc)
    try:
        count = _HANDLERS[action](block[action], env_files, dry_run)
    finally:
        if was_active and not dry_run:
            svc_start(svc)
    return (action, count)


_ACTION_VERB = {
    "copy": "copied",
    "chmod": "chmod",
    "xml": "xml",
    "systemd": "dropin",
    "run": "ran",
}


def summarize(tally: Counter) -> str:
    """Render a block tally as a compact phrase, e.g. 'chmod 5, copied 1, ran'."""
    parts = []
    for action in ("copy", "chmod", "xml", "systemd", "run"):
        n = tally.get(action, 0)
        if not n:
            continue
        parts.append("ran" if action == "run" else f"{_ACTION_VERB[action]} {n}")
    return ", ".join(parts)


def apply_blocks(
    blocks: list[dict],
    phase: str,
    env_files: list[str],
    dry_run: bool,
) -> Counter:
    """Apply matching-phase blocks in order; return a Counter action -> changes."""
    tally: Counter = Counter()
    for block in blocks:
        if block.get("phase", "post") != phase:
            continue
        if not conditions.block_passes(block):
            continue
        action, count = apply_block(block, env_files, dry_run)
        if action and count:
            tally[action] += count
    return tally
