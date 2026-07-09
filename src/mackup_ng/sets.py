"""Declarative config sets for mackup-ng (``~/.mackup/sets.d/*.toml``).

Applied during the restore phase of ``mackup sync``. Each TOML file is one
"config set" and may carry, gated by markers / OS:

  files            = [...]        candidate files (see files_mode)
  files_mode       = "first"      "first" existing, or "all" existing
  require_marker   = "name"       apply only if this marker exists
  skip_if_marker   = "name"       skip if this marker exists (opt-out)
  require_os       = "linux"      linux|macos|android gate
  restart_service  = "syncthing"  user service stopped before / started after the
                                  write (systemctl --user on linux, brew services
                                  on macOS)
  before / after   = "cmd"        shell run before / after the write (e.g. custom
                                  stop / start); after is guaranteed to run
  source_env       = ["~/.profile"]   files sourced to resolve ${VAR} values

  [[systemd_dropin]]    write ~/.config/systemd/user/<svc>.service.d/<name>.conf
  service = "syncthing"
  name    = "low-resource"
  Environment = ["GOMAXPROCS=1"]
  require_os = "linux"  optional; block applies only on this OS (default linux)

  [[mutate_xml]]                  XML edits (repeatable)
  select   = ["folder", "defaults/folder"]  ElementTree paths; omit/"" = root
  set_attr = { fsWatcherEnabled = "true" }
  set_child = { copiers = "1" }
  create_missing = true           create child element if absent
  create_parents = true           create selected element chain if absent

  [[run]]                         inline shell for imperative bits (repeatable)
  shell  = "bash"                 default "bash"
  require_command = "loginctl"    str or list; skip run if any not on PATH
  script = "..."                  runs with the MACKUP_* env; must be idempotent

Values "${VAR}" / "$VAR" resolve from the environment, else from source_env
files. XML/drop-in writes are idempotent (nothing written and the service is
NOT restarted when already identical); ``[[run]]`` scripts always execute and
own their own idempotency.

Files in ``sets.d/`` are applied sorted by name — use numeric prefixes
(``10-``, ``20-``, ...) to control ordering.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from . import hooks, utils
from .constants import MACKUP_HOME_DIR, SETS_DIRNAME


def default_sets_dir() -> str:
    return os.path.join(os.environ["HOME"], MACKUP_HOME_DIR, SETS_DIRNAME)


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


@dataclass(frozen=True)
class ServiceCtl:
    """How to bracket a config-set write with service / shell control."""

    name: str | None = None
    before: str | None = None
    after: str | None = None


def _run_shell(label: str, cmd: str, when: str) -> None:
    """Run a set-level before/after shell command with the MACKUP_* env."""
    _msg(f"Synchronizing {label}: {when}")
    result = subprocess.run(
        ["bash", "-c", cmd], env=hooks.hook_env("restore"), check=False,
    )
    if result.returncode != 0:
        _msg(f"Warning: {label} {when} command failed (code {result.returncode})")


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
    return os.path.join(
        xdg, "systemd", "user", f"{svc}.service.d", f"{name}.conf",
    )


# ---------------------------------------------------------------- apply one set
def resolve_files(cfg: dict) -> list[str]:
    mode = cfg.get("files_mode", "first")
    existing = [os.path.expanduser(f) for f in cfg.get("files", [])]
    existing = [f for f in existing if os.path.isfile(f)]
    if mode == "all":
        return existing
    return existing[:1]


def _block_os_ok(block: dict) -> bool:
    """A [[systemd_dropin]] block applies only on its OS (default: linux)."""
    return bool(block.get("require_os", "linux") == hooks.os_kind())


def gate(cfg: dict, label: str) -> bool:
    require = cfg.get("require_marker")
    if require and not hooks.has_marker(require):
        return False
    skip = cfg.get("skip_if_marker")
    if skip and hooks.has_marker(skip):
        _msg(f"Skipping {label}: marker {skip} (opt-out)")
        return False
    want_os = cfg.get("require_os")
    if want_os:
        current_os = hooks.os_kind()
        if want_os != current_os:
            _msg(f"Skipping {label}: not {want_os} (this is {current_os})")
            return False
    return True


def _run_scripts(label: str, runs: list[dict], dry_run: bool) -> None:
    if not runs:
        return
    env = hooks.hook_env("restore")
    for entry in runs:
        script = entry.get("script")
        if not script:
            continue
        # require_command: skip this run unless every listed binary is on PATH
        required = entry.get("require_command", [])
        if isinstance(required, str):
            required = [required]
        missing = [cmd for cmd in required if shutil.which(cmd) is None]
        if missing:
            _msg(f"Skipping {label}: missing command(s) {', '.join(missing)}")
            continue
        shell = entry.get("shell", "bash")
        _msg(f"Synchronizing {label}: run")
        if dry_run:
            continue
        result = subprocess.run([shell, "-c", script], env=env, check=False)
        if result.returncode != 0:
            _msg(f"Warning: {label} run failed (code {result.returncode})")


def apply_set(cfg_path: str, dry_run: bool = False) -> None:
    label = os.path.splitext(os.path.basename(cfg_path))[0]
    with open(cfg_path, "rb") as handle:
        cfg = tomllib.load(handle)

    if not gate(cfg, label):
        return

    files = resolve_files(cfg)
    # systemd drop-ins are linux-only; a block may narrow further via require_os
    dropins = [b for b in cfg.get("systemd_dropin", []) if _block_os_ok(b)]
    runs = cfg.get("run", [])
    if not files and not dropins and not runs:
        _msg(f"Skipping {label}: no target found")
        return

    env_files = cfg.get("source_env", [])
    mutates = cfg.get("mutate_xml", [])
    service = ServiceCtl(
        name=cfg.get("restart_service"),
        before=cfg.get("before"),
        after=cfg.get("after"),
    )

    # compute XML changes without writing
    pending_xml: list[tuple[str, ET.ElementTree[ET.Element[str]]]] = []
    for path in files:
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            _msg(f"Warning: {label}: cannot parse {path}: {exc}, skip file")
            continue
        root = tree.getroot()
        before = ET.tostring(root, encoding="utf-8")
        for mutate in mutates:
            apply_mutate(root, mutate, env_files)
        after = ET.tostring(root, encoding="utf-8")
        if before != after:
            pending_xml.append((path, tree))

    # compute drop-in changes
    pending_dropin: list[tuple[str, str]] = []
    for block in dropins:
        target = dropin_path(block)
        content = dropin_content(block)
        old = ""
        if os.path.isfile(target):
            with open(target) as handle:
                old = handle.read()
        if old != content:
            pending_dropin.append((target, content))

    changed = bool(pending_xml or pending_dropin)

    if changed and dry_run:
        for path, _ in pending_xml:
            _msg(f"Synchronizing {label}: would update {path}")
        for target, _ in pending_dropin:
            _msg(f"Synchronizing {label}: would write drop-in {target}")
    elif changed:
        _apply_changes(label, service, pending_xml, pending_dropin)
    elif not runs:
        _msg(f"{label}: already up to date")
        return

    _run_scripts(label, runs, dry_run)


def _apply_changes(
    label: str,
    service: ServiceCtl,
    pending_xml: list[tuple[str, ET.ElementTree[ET.Element[str]]]],
    pending_dropin: list[tuple[str, str]],
) -> None:
    """Run before-cmd, stop service, write XML + drop-ins, restart, run after-cmd.

    Service is stopped only if it is active; restart + after-cmd are guaranteed.
    """
    svc = service.name
    was_active = svc_is_active(svc)
    if pending_xml and svc and service_manager() is None:
        _msg(
            f"Warning: {label}: cannot control service {svc} on this OS "
            "— restart it manually so the new config is picked up",
        )
    try:
        if service.before:
            _run_shell(label, service.before, "before")
        if was_active and svc:
            svc_stop(svc)

        for path, tree in pending_xml:
            atomic_write(path, tree)
            _msg(f"Synchronizing {label}: updated {path}")

        reload_needed = False
        for target, content in pending_dropin:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as handle:
                handle.write(content)
            _msg(f"Synchronizing {label}: drop-in {target}")
            reload_needed = True
        if reload_needed and service_manager() == "systemctl":
            _sysd("daemon-reload")
    finally:
        if was_active and svc:
            svc_start(svc)
        if service.after:
            _run_shell(label, service.after, "after")


# ---------------------------------------------------------------- public api
def apply_dir(directory: str, dry_run: bool = False) -> int:
    """Apply every ``*.toml`` in ``directory`` (sorted). Returns exit code."""
    if not os.path.isdir(directory):
        return 0
    paths = sorted(glob.glob(os.path.join(directory, "*.toml")))
    return apply_files(paths, dry_run)


def _safe_apply(path: str, dry_run: bool) -> int:
    """Apply one set; one bad set must not abort the rest."""
    try:
        apply_set(path, dry_run)
    except Exception as exc:
        _msg(f"Warning: {os.path.basename(path)}: apply failed: {exc}")
        return 1
    return 0


def apply_files(paths: list[str], dry_run: bool = False) -> int:
    rc = 0
    for path in paths:
        rc |= _safe_apply(path, dry_run)
    return rc


def apply_file(path: str, dry_run: bool = False) -> int:
    """Apply a single config set file."""
    return _safe_apply(path, dry_run)
