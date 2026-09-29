"""bubblewrap sandboxes for agent processes and for executing submissions.

Everything an agent can touch is decided here, by construction:
  * the whole filesystem is mounted read-only (repository, LightStim, gate code);
  * credential locations and the shared store are replaced by empty tmpfs mounts;
  * only explicitly listed directories are writable;
  * environment is cleared and rebuilt from an explicit dict;
  * submissions additionally run without network (--unshare-net).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional

HOME = os.path.expanduser("~")
SECRET_DIRS = ["/nvme2n1/yuehan_zhang/.secrets", f"{HOME}/.ssh", f"{HOME}/.claude", f"{HOME}/.config/gh",
               f"{HOME}/.aws", f"{HOME}/.gnupg"]
SECRET_FILES = [f"{HOME}/.claude.json", f"{HOME}/.git-credentials", f"{HOME}/.netrc", f"{HOME}/.pypirc"]
RUNTIME_ROOT_DEFAULT = "/nvme2n1/yuehan_zhang/agent_for_qec_runtime"


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


def wrap(cmd: List[str], *, writable: Iterable[str] = (), readonly_extra: Iterable[str] = (),
         hide: Iterable[str] = (), env: Dict[str, str], cwd: str, network: bool = True,
         runtime_root: Optional[str] = None) -> List[str]:
    """Return a bwrap command line that runs `cmd` in the sandbox."""
    b = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
         "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--new-session"]
    if not network:
        b.append("--unshare-net")
    hidden = list(SECRET_DIRS) + [runtime_root or os.environ.get("QEC_RUNTIME_ROOT", RUNTIME_ROOT_DEFAULT)] + list(hide)
    for d in hidden:
        if os.path.isdir(d):
            b += ["--tmpfs", d]
    for f in SECRET_FILES:
        if os.path.exists(f):
            b += ["--ro-bind", "/dev/null", f]
    for d in readonly_extra:
        b += ["--ro-bind", d, d]
    for d in writable:
        Path(d).mkdir(parents=True, exist_ok=True)
        b += ["--bind", d, d]
    b += ["--clearenv"]
    for k, v in env.items():
        b += ["--setenv", k, str(v)]
    b += ["--chdir", cwd, "--"]
    return b + list(cmd)


def wrap_minimal(cmd: List[str], *, readonly: Iterable[str], writable: Iterable[str], env: Dict[str, str],
                 cwd: str, network: bool = False) -> List[str]:
    """Sandbox that sees ONLY system directories, the listed read-only paths and the
    listed writable paths (used to execute submissions: nothing else of the host,
    in particular no worker directory, no store, no credentials, no network)."""
    b = ["bwrap", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--die-with-parent", "--unshare-pid",
         "--unshare-ipc", "--new-session"]
    if not network:
        b.append("--unshare-net")
    for d in ["/usr", "/bin", "/lib", "/lib64", "/lib32", "/libx32", "/etc", "/sbin"]:
        if os.path.exists(d):
            b += ["--ro-bind", d, d] if not os.path.islink(d) else ["--symlink", os.readlink(d), d]
    for d in readonly:
        b += ["--ro-bind", d, d]
    for d in writable:
        Path(d).mkdir(parents=True, exist_ok=True)
        b += ["--bind", d, d]
    b += ["--clearenv"]
    for k, v in env.items():
        b += ["--setenv", k, str(v)]
    b += ["--chdir", cwd, "--"]
    return b + list(cmd)
