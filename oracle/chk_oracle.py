#!/usr/bin/env python3
"""
chk_oracle.py — Oracle memory and RHEL tuning checks for a sosreport or live /.

    chk_oracle.py --path SOSROOT
    chk_oracle.py --mem --path SOSROOT
    chk_oracle.py --validate --path SOSROOT

--mem
    Memory used by Oracle: process RSS, per-SID totals, SysV shared memory
    (SGA), static HugePages, and /dev/shm. Process RSS counts shared mappings
    once per process; the estimated footprint tries not to.

--validate
    Compare the running system with Red Hat's Oracle guidance:

        https://access.redhat.com/solutions/39188

    The checklist is that article, the RHEL TuneD profile "oracle"
    (package tuned-profiles-oracle), and Oracle Database minimum kernel
    parameters:

        vm.swappiness = 10
        vm.dirty_background ~ 3%, vm.dirty ~ 40%
        vm.dirty_expire_centisecs = 500
        vm.dirty_writeback_centisecs = 100
        kernel.numa_balancing = 0, and do not boot with numa=off
        transparent hugepages = never
        static HugePages for the SGA, not more than about 90% of RAM
        swap: 1.5x RAM if RAM <= 2 GiB, equal to RAM if RAM <= 16 GiB,
              otherwise 16 GiB
        kernel.shmmax at least half of RAM, or the 4 TiB TuneD value
        kernel.shmall large enough for shmmax, kernel.shmmni >= 4096
        kernel.sem >= 250 32000 100 128
        fs.file-max >= 6815744, fs.aio-max-nr >= 1048576
        net.ipv4.ip_local_port_range covering 9000-65499
        net.core rmem/wmem at or above the Oracle minimums
        kernel.panic_on_oops = 1
        RemoveIPC=no
        mq-deadline I/O scheduler (none/noop/kyber on non-rotational disks)
        CPU governor performance
        memlock / nofile / nproc / stack for the oracle and grid users

With neither flag, both reports are printed.

Exit codes:
    0  validation passed, or --mem only and the tree was readable
    1  one or more WARN or FAIL
    2  path is not a directory, or it has no sosreport / proc / sys data
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass, field


# Oracle installer minimums and the RHEL TuneD "oracle" profile.
# https://access.redhat.com/solutions/39188
SEM_MIN = (250, 32000, 100, 128)
SEM_NAMES = ("semmsl", "semmns", "semopm", "semmni")
SHMMNI_MIN = 4096
FILE_MAX_MIN = 6_815_744
AIO_MAX_MIN = 1_048_576
PORT_START_MAX = 9000
PORT_END_MIN = 65499  # TuneD uses 65499; Oracle documents 65500
RMEM_DEFAULT_MIN = 262_144
RMEM_MAX_MIN = 4_194_304
WMEM_DEFAULT_MIN = 262_144
WMEM_MAX_MIN = 1_048_576
SHMMAX_TUNED = 4_398_046_511_104  # 4 TiB, oracle profile and preinstall RPM
DIRTY_RATIO_TARGET = 40
DIRTY_BG_TARGET = 3
DIRTY_EXPIRE = 500
DIRTY_WRITEBACK = 100
HUGEPAGE_RAM_FRACTION = 0.90
HUGEPAGE_UNUSED_FRACTION = 0.50
NOFILE_MIN = 65_536
NPROC_MIN = 16_384
STACK_MIN_KB = 32_768
PRESSURE_FRACTION = 0.80
SHM_ORACLE_MIN_BYTES = 256 * 1024 * 1024
DEVSHM_FULL_FRACTION = 0.90

PROC_SYS_KEYS = (
    "vm.swappiness",
    "vm.dirty_ratio",
    "vm.dirty_bytes",
    "vm.dirty_background_ratio",
    "vm.dirty_background_bytes",
    "vm.dirty_expire_centisecs",
    "vm.dirty_writeback_centisecs",
    "vm.zone_reclaim_mode",
    "vm.nr_hugepages",
    "vm.hugetlb_shm_group",
    "kernel.shmmax",
    "kernel.shmall",
    "kernel.shmmni",
    "kernel.sem",
    "kernel.panic_on_oops",
    "kernel.numa_balancing",
    "fs.file-max",
    "fs.aio-max-nr",
    "fs.file-nr",
    "fs.aio-nr",
    "net.ipv4.ip_local_port_range",
    "net.core.rmem_default",
    "net.core.rmem_max",
    "net.core.wmem_default",
    "net.core.wmem_max",
)

PS_CANDIDATES = (
    "sos_commands/process/ps_auxwww",
    "sos_commands/process/ps_auxcww",
    "sos_commands/process/ps_aux",
    "sos_commands/process/ps_auxww",
    "sos_commands/process/ps_auxwwwm",
)

SYSCTL_CANDIDATES = (
    "sos_commands/kernel/sysctl_-a",
    "sos_commands/kernel/sysctl--a",
    "sos_commands/kernel/sysctl_-a_--ignore",
)

DF_CANDIDATES = (
    "sos_commands/filesys/df_-al",
    "sos_commands/filesys/df_-ali",
    "sos_commands/filesys/df_-hP",
    "sos_commands/filesys/df_-h",
    "sos_commands/filesys/df",
)

IPCS_CANDIDATES = (
    "sos_commands/ipc/ipcs",
    "sos_commands/ipc/ipcs_-m",
    "sos_commands/process/ipcs",
    "sos_commands/process/ipcs_-m",
)

GRID_BASENAMES = frozenset({
    "ohasd", "ohasd.bin",
    "cssdagent", "cssdmonitor",
    "ocssd", "ocssd.bin",
    "crsd", "crsd.bin",
    "evmd", "evmd.bin",
    "evmlogger", "evmlogger.bin",
    "mdnsd", "mdnsd.bin",
    "gpnpd", "gpnpd.bin",
    "gnsd", "gnsd.bin",
    "osysmond", "osysmond.bin",
    "ologgerd",
    "octssd", "octssd.bin",
    "orarootagent", "orarootagent.bin",
    "oraagent", "oraagent.bin",
    "scriptagent", "scriptagent.bin",
    "appagent", "appagent.bin",
    "ons", "ons.bin",
    "diskmon", "diskmon.bin",
})

BG_RE = re.compile(r"^(ora|asm)_([^_]+)_(.+)$")
SERVER_RE = re.compile(r"^oracle([A-Za-z0-9_+$#]+)$")
LIMIT_RE = re.compile(
    r"^(?P<dom>\S+)\s+(?P<type>soft|hard|-)\s+(?P<item>\S+)\s+(?P<val>\S+)\s*$",
    re.IGNORECASE,
)
SKIP_BLOCK_PREFIXES = (
    "loop", "ram", "sr", "fd", "zram", "nbd", "mtd", "zd", "dm-", "md",
)

MISSING = object()


# ---------------------------------------------------------------------------
# Small types
# ---------------------------------------------------------------------------

@dataclass
class Proc:
    user: str
    pid: int
    rss_kb: int
    vsz_kb: int
    command: str
    kind: str
    sid: str | None
    swap_kb: int | None = None
    anon_kb: int | None = None
    shmem_kb: int | None = None


@dataclass
class ShmSeg:
    key: str
    shmid: str
    owner: str
    size_bytes: int
    rss_bytes: int | None
    swap_bytes: int | None
    nattch: int


@dataclass
class UserRec:
    name: str
    uid: int
    gid: int


@dataclass
class GroupRec:
    name: str
    gid: int
    members: set[str] = field(default_factory=set)


@dataclass
class LimitEntry:
    domain: str
    type: str  # soft, hard, or -
    item: str
    value: int | None  # None means unlimited
    source: str


@dataclass
class Check:
    status: str
    title: str
    detail: str = ""


@dataclass
class Footprint:
    rss_sum_bytes: int
    private_bytes: int
    shared_bytes: int
    posix_bytes: int
    posix_included: bool
    total_bytes: int
    method: str
    shared_note: str


@dataclass
class Host:
    root: str
    pagesize: int
    os_name: str | None = None
    meminfo: dict[str, int] = field(default_factory=dict)
    sysctl: dict[str, str] = field(default_factory=dict)
    sysctl_src: dict[str, str] = field(default_factory=dict)
    config: dict[str, str] = field(default_factory=dict)
    config_src: dict[str, str] = field(default_factory=dict)
    procs: list[Proc] = field(default_factory=list)
    ps_source: str | None = None
    shm: list[ShmSeg] = field(default_factory=list)
    shm_source: str | None = None
    users: dict[str, UserRec] = field(default_factory=dict)
    groups: dict[str, GroupRec] = field(default_factory=dict)
    oratab: list[tuple[str, str, str]] = field(default_factory=list)
    tuned: str | None = None
    tuned_src: str | None = None
    cmdline: str | None = None
    thp: str | None = None
    thp_src: str | None = None
    remove_ipc: bool | None = None
    remove_ipc_src: str | None = None
    limits: list[LimitEntry] = field(default_factory=list)
    schedulers: list[tuple[str, str, int | None]] = field(default_factory=list)
    governors: dict[str, int] = field(default_factory=dict)
    devshm_total_bytes: int | None = None
    devshm_used_bytes: int | None = None
    devshm_src: str | None = None
    numa_nodes: int | None = None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def use_color(no_color: bool) -> bool:
    return (
        not no_color
        and sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
    )


def _c(enabled: bool, code: str, text: str) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def c_status(enabled: bool, status: str) -> str:
    codes = {
        "PASS": "32",
        "WARN": "33",
        "FAIL": "31",
        "SKIP": "2",
        "INFO": "36",
    }
    return _c(enabled, codes.get(status, "0"), f"[{status}]")


def c_verdict(enabled: bool, verdict: str) -> str:
    codes = {"PASS": "1;32", "WARN": "1;33", "FAIL": "1;31"}
    return _c(enabled, codes.get(verdict, "1"), verdict)


def fmt_bytes(n: float | int | None) -> str:
    if n is None:
        return "n/a"
    n = float(n)
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    for name, div in (("TiB", 1024 ** 4), ("GiB", 1024 ** 3),
                      ("MiB", 1024 ** 2), ("KiB", 1024)):
        if n >= div:
            return f"{sign}{n / div:.2f} {name}"
    return f"{sign}{n:.0f} B"


def fmt_int(n: int) -> str:
    return f"{n:,}"


def trunc(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def norm_ws(value: str) -> str:
    return " ".join(value.split())


def clean_val(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def read_text(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def read_strip(path: str) -> str | None:
    text = read_text(path)
    if text is None:
        return None
    return text.strip()


def join_root(root: str, *parts: str) -> str:
    return os.path.join(root, *parts)


def rel_to(root: str, path: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path
    return rel if not rel.startswith("..") else path


def first_file(root: str, rels: tuple[str, ...] | list[str]) -> tuple[str | None, str | None]:
    for rel in rels:
        text = read_text(join_root(root, rel))
        if text is not None:
            return text, rel
    return None, None


def looks_like_system(root: str) -> bool:
    for name in ("proc", "sys", "etc", "sos_commands", "var"):
        if os.path.exists(join_root(root, name)):
            return True
    return False


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    token = value.split()[0] if value.split() else ""
    if not token:
        return None
    try:
        return int(token, 10)
    except ValueError:
        return None


def parse_ints(value: str | None) -> list[int] | None:
    if value is None:
        return None
    out: list[int] = []
    for token in value.split():
        try:
            out.append(int(token, 10))
        except ValueError:
            return None
    return out or None


def parse_bool(value: str) -> bool | None:
    token = value.strip().lower()
    if token in {"1", "yes", "true", "on"}:
        return True
    if token in {"0", "no", "false", "off"}:
        return False
    return None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_meminfo(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            out[key.strip()] = int(parts[0])
        except ValueError:
            continue
    return out


def parse_sysctl_text(text: str) -> dict[str, str]:
    """Parse `sysctl -a` (key = value) or sysctl.conf (key = value)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("["):
            continue
        if " = " in line:
            key, _, value = line.partition(" = ")
        elif "=" in line:
            key, _, value = line.partition("=")
        else:
            continue
        key = key.strip()
        if key:
            out[key] = clean_val(value)
    return out


def parse_status(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def status_kb(status: dict[str, str], key: str) -> int | None:
    raw = status.get(key)
    if not raw:
        return None
    token = raw.split()[0]
    try:
        return int(token)
    except ValueError:
        return None


def parse_oratab(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        flag = parts[2].strip() if len(parts) > 2 else ""
        rows.append((parts[0].strip(), parts[1].strip(), flag))
    return rows


def parse_passwd(text: str) -> dict[str, UserRec]:
    users: dict[str, UserRec] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 4:
            continue
        try:
            users[parts[0]] = UserRec(parts[0], int(parts[2]), int(parts[3]))
        except ValueError:
            continue
    return users


def parse_group(text: str) -> dict[str, GroupRec]:
    groups: dict[str, GroupRec] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 3:
            continue
        try:
            gid = int(parts[2])
        except ValueError:
            continue
        members = {m for m in parts[3].split(",") if m} if len(parts) > 3 else set()
        groups[parts[0]] = GroupRec(parts[0], gid, members)
    return groups


def parse_thp(text: str) -> str | None:
    match = re.search(r"\[([^\]]+)\]", text)
    if match:
        return match.group(1).strip().lower()
    token = text.strip().lower()
    if token in {"always", "madvise", "never"}:
        return token
    return None


def active_scheduler(text: str) -> str:
    match = re.search(r"\[([^\]]+)\]", text)
    if match:
        return match.group(1).strip()
    parts = text.split()
    return parts[0] if parts else ""


def cmdline_map(cmdline: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for token in cmdline.split():
        if "=" in token:
            key, _, value = token.partition("=")
            out.setdefault(key, []).append(value)
        else:
            out.setdefault(token, []).append("")
    return out


def _classify_token(token: str) -> tuple[str, str | None] | None:
    base = os.path.basename(token)
    match = BG_RE.match(base)
    if match:
        kind = "asm" if match.group(1) == "asm" else "db"
        return kind, match.group(3)
    if base in {"tnslsnr", "lsnrctl"}:
        return "listener", None
    if base in GRID_BASENAMES:
        return "grid", None
    if base == "oracle":
        return "server", None
    match = SERVER_RE.match(base)
    if match:
        return "server", match.group(1)
    return None


def classify_command(command: str, homes: list[str]) -> tuple[str, str | None] | None:
    """Return (kind, sid) when the command is an Oracle DB or Grid process.

    `ps auxf` puts tree glyphs in the command column (`\\_ ora_pmon_SID`), so
    the executable is not always the first token.
    """
    parts = command.split()
    if not parts:
        return None
    tokens: list[str] = []
    for token in parts[:6]:
        cleaned = token.lstrip("|\\-_`+")
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
    for token in tokens:
        found = _classify_token(token)
        if found:
            return found
    for token in tokens:
        for home in homes:
            home = home.rstrip("/")
            if home and (token == home or token.startswith(home + "/")):
                return "home", None
    return None


def parse_ps_text(text: str, homes: list[str]) -> list[Proc] | None:
    """Parse `ps aux*` output. None means the file has no RSS/PID columns."""
    lines = text.splitlines()
    if not lines:
        return None
    tokens = lines[0].split()
    if "PID" not in tokens or "RSS" not in tokens:
        return None
    if "COMMAND" in tokens:
        cmd_i = tokens.index("COMMAND")
    elif "CMD" in tokens:
        cmd_i = tokens.index("CMD")
    else:
        return None
    if "USER" in tokens:
        user_i = tokens.index("USER")
    elif "UID" in tokens:
        user_i = tokens.index("UID")
    else:
        return None
    pid_i = tokens.index("PID")
    rss_i = tokens.index("RSS")
    vsz_i = tokens.index("VSZ") if "VSZ" in tokens else None
    procs: list[Proc] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(None, cmd_i)
        if len(parts) <= cmd_i:
            continue
        fixed = parts[:cmd_i]
        command = parts[cmd_i].strip()
        need = max(pid_i, rss_i, user_i)
        if len(fixed) <= need:
            continue
        if not fixed[pid_i].isdigit():
            continue
        try:
            rss = int(fixed[rss_i])
        except ValueError:
            continue
        vsz = 0
        if vsz_i is not None and vsz_i < len(fixed):
            try:
                vsz = int(fixed[vsz_i])
            except ValueError:
                vsz = 0
        classified = classify_command(command, homes)
        if classified is None:
            continue
        kind, sid = classified
        procs.append(Proc(
            user=fixed[user_i],
            pid=int(fixed[pid_i]),
            rss_kb=rss,
            vsz_kb=vsz,
            command=command,
            kind=kind,
            sid=sid,
        ))
    return procs


def enrich_from_status(root: str, procs: list[Proc]) -> None:
    for proc in procs:
        text = read_text(join_root(root, "proc", str(proc.pid), "status"))
        if not text:
            continue
        status = parse_status(text)
        proc.swap_kb = status_kb(status, "VmSwap")
        if "RssAnon" in status:
            proc.anon_kb = status_kb(status, "RssAnon")
        if "RssShmem" in status:
            proc.shmem_kb = status_kb(status, "RssShmem")


def scan_proc(root: str, homes: list[str], uid_to_name: dict[int, str]) -> list[Proc]:
    proc_root = join_root(root, "proc")
    if not os.path.isdir(proc_root):
        return []
    procs: list[Proc] = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return []
    for name in names:
        if not name.isdigit():
            continue
        status_text = read_text(join_root(proc_root, name, "status"))
        if not status_text:
            continue
        status = parse_status(status_text)
        cmdline = read_text(join_root(proc_root, name, "cmdline"))
        if cmdline:
            command = cmdline.replace("\x00", " ").strip()
        else:
            command = status.get("Name", "")
        classified = classify_command(command, homes)
        if classified is None and status.get("Name"):
            classified = classify_command(status["Name"], homes)
        if classified is None:
            continue
        kind, sid = classified
        rss = status_kb(status, "VmRSS") or 0
        vsz = status_kb(status, "VmSize") or 0
        uid = parse_int(status.get("Uid"))
        user = uid_to_name.get(uid, str(uid) if uid is not None else "?")
        proc = Proc(
            user=user,
            pid=int(name),
            rss_kb=rss,
            vsz_kb=vsz,
            command=command or status.get("Name", ""),
            kind=kind,
            sid=sid,
            swap_kb=status_kb(status, "VmSwap"),
        )
        if "RssAnon" in status:
            proc.anon_kb = status_kb(status, "RssAnon")
        if "RssShmem" in status:
            proc.shmem_kb = status_kb(status, "RssShmem")
        procs.append(proc)
    procs.sort(key=lambda p: p.pid)
    return procs


def pages_or_bytes(raw: int, size_bytes: int, pagesize: int) -> int:
    """
    /proc/sysvipc/shm rss and swap are page counts (man proc).
    If the captured number is already bytes, raw * pagesize exceeds the segment.
    """
    if raw <= 0:
        return 0
    as_pages = raw * pagesize
    if size_bytes > 0 and as_pages > size_bytes * 2 and raw <= size_bytes:
        return raw
    return as_pages


def parse_sysvipc_shm(
    text: str,
    pagesize: int,
    uid_to_name: dict[int, str],
) -> list[ShmSeg]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split()
    idx = {name: i for i, name in enumerate(header)}
    if "size" not in idx:
        return []

    def cell_int(parts: list[str], col: str) -> int | None:
        if col not in idx or idx[col] >= len(parts):
            return None
        try:
            return int(parts[idx[col]])
        except ValueError:
            return None

    segs: list[ShmSeg] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= idx["size"]:
            continue
        size = cell_int(parts, "size")
        if size is None:
            continue
        uid = cell_int(parts, "uid")
        if uid is None:
            owner = "?"
        else:
            owner = uid_to_name.get(uid, str(uid))
        rss_raw = cell_int(parts, "rss")
        swap_raw = cell_int(parts, "swap")
        nattch = cell_int(parts, "nattch") or 0
        key = parts[idx["key"]] if "key" in idx and idx["key"] < len(parts) else "?"
        shmid = parts[idx["shmid"]] if "shmid" in idx and idx["shmid"] < len(parts) else "?"
        segs.append(ShmSeg(
            key=key,
            shmid=shmid,
            owner=owner,
            size_bytes=size,
            rss_bytes=pages_or_bytes(rss_raw, size, pagesize) if rss_raw is not None else None,
            swap_bytes=pages_or_bytes(swap_raw, size, pagesize) if swap_raw is not None else None,
            nattch=nattch,
        ))
    return segs


def parse_ipcs(text: str) -> list[ShmSeg]:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if "shmid" in lower and "bytes" in lower:
            start = i
            break
    if start is None:
        return []
    segs: list[ShmSeg] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("---") or "semaphore" in stripped.lower() or "message queue" in stripped.lower():
            break
        parts = stripped.split()
        if len(parts) < 6:
            continue
        try:
            size = int(parts[4])
            nattch = int(parts[5])
        except ValueError:
            continue
        segs.append(ShmSeg(parts[0], parts[1], parts[2], size, None, None, nattch))
    return segs


def parse_df_shm(text: str) -> tuple[int, int] | None:
    """Return (/dev/shm total bytes, used bytes) from df output."""
    lines = text.splitlines()
    header_i = None
    for i, line in enumerate(lines):
        if "Mounted" in line and ("Used" in line or "1K-blocks" in line or "Size" in line):
            header_i = i
            break
    if header_i is None:
        return None
    header = lines[header_i]
    human = "Size" in header.split() and "1K-blocks" not in header and "1024-blocks" not in header
    for line in lines[header_i + 1:]:
        parts = line.split()
        if len(parts) < 6 or parts[-1] != "/dev/shm":
            continue
        total_tok, used_tok = parts[-5], parts[-4]
        try:
            if human:
                total = human_to_bytes(total_tok)
                used = human_to_bytes(used_tok)
                if total is None or used is None:
                    return None
                return total, used
            return int(total_tok) * 1024, int(used_tok) * 1024
        except ValueError:
            return None
    return None


def human_to_bytes(token: str) -> int | None:
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)([KMGTP])?", token, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    mult = {"": 1, "B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}
    return int(number * mult[unit])


def parse_mount_size(options: str, mem_bytes: int | None) -> int | None:
    match = re.search(r"(?:^|,)size=([^,]+)", options)
    if not match:
        if mem_bytes is None:
            return None
        return mem_bytes // 2
    token = match.group(1).strip()
    if token.endswith("%"):
        if mem_bytes is None:
            return None
        try:
            return int(mem_bytes * float(token[:-1]) / 100.0)
        except ValueError:
            return None
    match = re.fullmatch(r"(\d+)([kKmMgGtT])?", token)
    if not match:
        return None
    number = int(match.group(1))
    suffix = (match.group(2) or "").lower()
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
    return number * mult[suffix]


def parse_limits_text(text: str, source: str) -> list[LimitEntry]:
    entries: list[LimitEntry] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = LIMIT_RE.match(line)
        if not match:
            continue
        value_tok = match.group("val").lower()
        if value_tok in {"unlimited", "infinity", "-1"}:
            value: int | None = None
        else:
            try:
                value = int(value_tok)
            except ValueError:
                continue
        entries.append(LimitEntry(
            domain=match.group("dom"),
            type=match.group("type").lower(),
            item=match.group("item").lower(),
            value=value,
            source=source,
        ))
    return entries


def recommended_swap_kb(mem_kb: int) -> int:
    """Oracle installation-guide swap size, in KiB."""
    mem_bytes = mem_kb * 1024
    gib = 1024 ** 3
    if mem_bytes <= 2 * gib:
        return int(mem_kb * 3 / 2)
    if mem_bytes <= 16 * gib:
        return mem_kb
    return 16 * gib // 1024


# ---------------------------------------------------------------------------
# Load a sosreport
# ---------------------------------------------------------------------------

def detect_pagesize(root: str, override: int | None) -> int:
    if override:
        return override
    for rel in (
        "sos_commands/kernel/getconf_PAGE_SIZE",
        "sos_commands/host/getconf_PAGE_SIZE",
    ):
        text = read_strip(join_root(root, rel))
        if text and text.isdigit() and int(text) > 0:
            return int(text)
    return 4096


def load_os_name(root: str) -> str | None:
    text = read_text(join_root(root, "etc/os-release"))
    if text:
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                return clean_val(line.split("=", 1)[1])
    for rel in ("etc/redhat-release", "etc/system-release"):
        text = read_strip(join_root(root, rel))
        if text:
            return text.splitlines()[0].strip()
    return None


def load_sysctl(root: str) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for key in PROC_SYS_KEYS:
        rel = os.path.join("proc/sys", *key.split("."))
        text = read_strip(join_root(root, rel))
        if text is None:
            continue
        values[key] = clean_val(text.replace("\t", " "))
        sources[key] = rel
    text, rel = first_file(root, SYSCTL_CANDIDATES)
    if text is None:
        hits = sorted(glob.glob(join_root(root, "sos_commands/kernel/sysctl*")))
        for path in hits:
            text = read_text(path)
            if text and " = " in text:
                rel = rel_to(root, path)
                break
        else:
            text = None
    if text and rel:
        for key, value in parse_sysctl_text(text).items():
            values[key] = value
            sources[key] = rel
    return values, sources


def load_sysctl_config(root: str) -> tuple[dict[str, str], dict[str, str]]:
    files: list[str] = []
    for base in ("usr/lib/sysctl.d", "run/sysctl.d"):
        files.extend(sorted(glob.glob(join_root(root, base, "*.conf"))))
    main = join_root(root, "etc/sysctl.conf")
    if os.path.isfile(main):
        files.append(main)
    files.extend(sorted(glob.glob(join_root(root, "etc/sysctl.d", "*.conf"))))
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for path in files:
        text = read_text(path)
        if not text:
            continue
        rel = rel_to(root, path)
        for key, value in parse_sysctl_text(text).items():
            values[key] = value
            sources[key] = rel
    return values, sources


def load_tuned(root: str) -> tuple[str | None, str | None]:
    text, rel = first_file(root, (
        "sos_commands/tuned/tuned-adm_active",
        "sos_commands/tuned/tuned-adm_profile",
        "etc/tuned/active_profile",
    ))
    if not text or not rel:
        return None, None
    match = re.search(r"Current active profile:\s*(\S+)", text)
    if match:
        return match.group(1), rel
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line.split()[-1], rel
    return None, rel


def load_thp(root: str) -> tuple[str | None, str | None]:
    text, rel = first_file(root, (
        "sys/kernel/mm/transparent_hugepage/enabled",
        "sys/kernel/mm/redhat_transparent_hugepage/enabled",
    ))
    if text and rel:
        mode = parse_thp(text)
        if mode:
            return mode, rel
    return None, None


def load_remove_ipc(root: str) -> tuple[bool | None, str | None]:
    files: list[str] = []
    main = join_root(root, "etc/systemd/logind.conf")
    if os.path.isfile(main):
        files.append(main)
    files.extend(sorted(glob.glob(join_root(root, "etc/systemd/logind.conf.d", "*.conf"))))
    value: bool | None = None
    source: str | None = None
    for path in files:
        text = read_text(path)
        if not text:
            continue
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or "=" not in line or line.startswith("["):
                continue
            key, _, raw_val = line.partition("=")
            if key.strip().lower() != "removeipc":
                continue
            parsed = parse_bool(raw_val.strip())
            if parsed is None:
                continue
            value = parsed
            source = rel_to(root, path)
    return value, source


def load_limits(root: str) -> list[LimitEntry]:
    files: list[str] = []
    main = join_root(root, "etc/security/limits.conf")
    if os.path.isfile(main):
        files.append(main)
    files.extend(sorted(glob.glob(join_root(root, "etc/security/limits.d", "*.conf"))))
    entries: list[LimitEntry] = []
    for path in files:
        text = read_text(path)
        if text:
            entries.extend(parse_limits_text(text, rel_to(root, path)))
    return entries


def load_schedulers(root: str) -> list[tuple[str, str, int | None]]:
    rows: list[tuple[str, str, int | None]] = []
    pattern = join_root(root, "sys/block/*/queue/scheduler")
    for path in sorted(glob.glob(pattern)):
        name = path.split(os.sep)
        try:
            dev = name[name.index("block") + 1]
        except (ValueError, IndexError):
            continue
        if dev.startswith(SKIP_BLOCK_PREFIXES):
            continue
        text = read_strip(path)
        if not text:
            continue
        rot_text = read_strip(os.path.join(os.path.dirname(path), "rotational"))
        rot = int(rot_text) if rot_text in {"0", "1"} else None
        rows.append((dev, active_scheduler(text), rot))
    return rows


def load_governors(root: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    pattern = join_root(root, "sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")
    for path in glob.glob(pattern):
        text = read_strip(path)
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return counts


def load_devshm(root: str, mem_bytes: int | None) -> tuple[int | None, int | None, str | None]:
    text, rel = first_file(root, DF_CANDIDATES)
    if text is None:
        hits = sorted(glob.glob(join_root(root, "sos_commands/filesys/df*")))
        for path in hits:
            text = read_text(path)
            if text and "/dev/shm" in text:
                rel = rel_to(root, path)
                break
        else:
            text = None
    if text and rel:
        parsed = parse_df_shm(text)
        if parsed:
            return parsed[0], parsed[1], rel
    mounts, mrel = first_file(root, ("proc/mounts", "proc/self/mounts", "sos_commands/filesys/mount_-l"))
    if not mounts:
        return None, None, None
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[1] != "/dev/shm":
            continue
        total = parse_mount_size(parts[3], mem_bytes)
        return total, None, mrel
    return None, None, None


def load_numa_nodes(root: str) -> int | None:
    nodes = glob.glob(join_root(root, "sys/devices/system/node/node[0-9]*"))
    nodes = [p for p in nodes if os.path.isdir(p)]
    if nodes:
        return len(nodes)
    text, _ = first_file(root, (
        "sos_commands/numa/numactl_--hardware",
        "sos_commands/numa/numactl_--hardware_",
    ))
    if not text:
        hits = sorted(glob.glob(join_root(root, "sos_commands/numa/numactl*")))
        for path in hits:
            text = read_text(path)
            if text and "node" in text.lower():
                break
        else:
            text = None
    if not text:
        return None
    match = re.search(r"available:\s*(\d+)\s+nodes", text)
    if match:
        return int(match.group(1))
    return None


def oracle_homes(oratab: list[tuple[str, str, str]]) -> list[str]:
    homes: list[str] = []
    for _sid, home, _flag in oratab:
        home = home.rstrip("/")
        if home and home not in homes:
            homes.append(home)
    return homes


def load_procs(root: str, homes: list[str], uid_to_name: dict[int, str]) -> tuple[list[Proc], str | None]:
    candidates = list(PS_CANDIDATES)
    candidates.extend(
        rel_to(root, path)
        for path in sorted(glob.glob(join_root(root, "sos_commands/process/ps_aux*")))
    )
    seen: set[str] = set()
    for rel in candidates:
        if rel in seen:
            continue
        seen.add(rel)
        text = read_text(join_root(root, rel))
        if text is None:
            continue
        procs = parse_ps_text(text, homes)
        if procs is None:
            continue
        enrich_from_status(root, procs)
        return procs, rel
    procs = scan_proc(root, homes, uid_to_name)
    if procs:
        return procs, "proc/<pid>/{cmdline,status}"
    return [], None


def load_shm(root: str, pagesize: int, uid_to_name: dict[int, str]) -> tuple[list[ShmSeg], str | None]:
    rel = "proc/sysvipc/shm"
    text = read_text(join_root(root, rel))
    lines = text.splitlines() if text else []
    header = lines[0].split() if lines else []
    if "size" in header:
        return parse_sysvipc_shm(text or "", pagesize, uid_to_name), rel
    text, rel = first_file(root, IPCS_CANDIDATES)
    if text and rel:
        return parse_ipcs(text), rel
    return [], None


def load_host(root: str, pagesize: int) -> Host:
    users = parse_passwd(read_text(join_root(root, "etc/passwd")) or "")
    groups = parse_group(read_text(join_root(root, "etc/group")) or "")
    oratab_text = read_text(join_root(root, "etc/oratab"))
    oratab = parse_oratab(oratab_text) if oratab_text else []
    uid_to_name = {user.uid: user.name for user in users.values()}
    mem_text = read_text(join_root(root, "proc/meminfo"))
    meminfo = parse_meminfo(mem_text) if mem_text else {}
    sysctl, sysctl_src = load_sysctl(root)
    config, config_src = load_sysctl_config(root)
    procs, ps_source = load_procs(root, oracle_homes(oratab), uid_to_name)
    shm, shm_source = load_shm(root, pagesize, uid_to_name)
    tuned, tuned_src = load_tuned(root)
    thp, thp_src = load_thp(root)
    remove_ipc, remove_src = load_remove_ipc(root)
    mem_bytes = meminfo.get("MemTotal", 0) * 1024 if meminfo.get("MemTotal") else None
    dev_total, dev_used, dev_src = load_devshm(root, mem_bytes)
    cmdline = read_strip(join_root(root, "proc/cmdline"))
    return Host(
        root=root,
        pagesize=pagesize,
        os_name=load_os_name(root),
        meminfo=meminfo,
        sysctl=sysctl,
        sysctl_src=sysctl_src,
        config=config,
        config_src=config_src,
        procs=procs,
        ps_source=ps_source,
        shm=shm,
        shm_source=shm_source,
        users=users,
        groups=groups,
        oratab=oratab,
        tuned=tuned,
        tuned_src=tuned_src,
        cmdline=cmdline,
        thp=thp,
        thp_src=thp_src,
        remove_ipc=remove_ipc,
        remove_ipc_src=remove_src,
        limits=load_limits(root),
        schedulers=load_schedulers(root),
        governors=load_governors(root),
        devshm_total_bytes=dev_total,
        devshm_used_bytes=dev_used,
        devshm_src=dev_src,
        numa_nodes=load_numa_nodes(root),
    )


# ---------------------------------------------------------------------------
# Memory report
# ---------------------------------------------------------------------------

def mem_bytes(host: Host) -> int | None:
    kb = host.meminfo.get("MemTotal")
    if not kb:
        return None
    return kb * 1024


def hugepage_bytes(host: Host, count_key: str) -> int | None:
    count = host.meminfo.get(count_key)
    size_kb = host.meminfo.get("Hugepagesize")
    if count is None or not size_kb:
        return None
    return count * size_kb * 1024


def oracle_user_names(host: Host) -> list[str]:
    names: list[str] = []
    for name in ("oracle", "grid"):
        if name in host.users or any(p.user == name for p in host.procs):
            names.append(name)
    for proc in host.procs:
        if proc.user not in names:
            names.append(proc.user)
    return names


def oracle_evidence(host: Host) -> bool:
    if host.procs or host.oratab:
        return True
    return any(name in host.users for name in ("oracle", "grid"))


def oracle_segments(host: Host) -> list[ShmSeg]:
    names = {name.lower() for name in oracle_user_names(host)}
    names.update({"oracle", "grid"})
    selected: list[ShmSeg] = []
    for seg in host.shm:
        if seg.owner.lower() in names:
            selected.append(seg)
        elif host.procs and seg.size_bytes >= SHM_ORACLE_MIN_BYTES:
            selected.append(seg)
    return selected


def estimate_footprint(host: Host, segs: list[ShmSeg]) -> Footprint:
    rss_sum = sum(proc.rss_kb for proc in host.procs) * 1024
    huge = hugepage_bytes(host, "HugePages_Total")
    free = hugepage_bytes(host, "HugePages_Free")
    huge_inuse = 0
    if huge is not None and free is not None:
        huge_inuse = max(0, huge - free)
    elif huge is not None and free is None:
        huge_inuse = huge
    rss_known = [seg for seg in segs if seg.rss_bytes is not None]
    sysv_res = sum(seg.rss_bytes or 0 for seg in rss_known)
    weighted = sum((seg.rss_bytes or 0) * max(seg.nattch, 1) for seg in rss_known)
    anon_vals = [proc.anon_kb for proc in host.procs if proc.anon_kb is not None]
    include_posix = False
    if host.procs and len(anon_vals) == len(host.procs):
        private = sum(anon_vals) * 1024
        method = "sum of RssAnon (private)"
        include_posix = True
    elif huge_inuse > 0 and sysv_res > 0 and rss_sum < sysv_res:
        private = rss_sum
        method = "sum of RSS; HugePages are not part of RSS"
    elif weighted > 0 and rss_sum >= weighted // 2:
        private = max(0, rss_sum - weighted)
        method = "sum of RSS minus SysV resident x nattch"
    elif sysv_res > 0 and rss_sum > sysv_res:
        private = rss_sum - sysv_res
        method = "sum of RSS minus one copy of SysV resident"
    else:
        private = rss_sum
        method = "sum of RSS"
    if segs and not rss_known:
        if huge_inuse > 0:
            shared = huge_inuse
            shared_note = "HugePages in use (SysV resident was not captured)"
        else:
            shared = sum(seg.size_bytes for seg in segs)
            shared_note = "SysV allocated size (resident pages were not captured)"
    else:
        shared = max(sysv_res, huge_inuse)
        if huge_inuse and sysv_res and abs(huge_inuse - sysv_res) <= 0.05 * max(huge_inuse, sysv_res):
            shared_note = "SysV resident and HugePages in use match; counted once"
        else:
            shared_note = "max of SysV resident and HugePages in use"
    if shared == 0 and not segs and huge_inuse == 0:
        shared_note = "no SysV resident memory or HugePages in use"
    posix = host.devshm_used_bytes or 0
    total = private + shared + (posix if include_posix else 0)
    return Footprint(
        rss_sum_bytes=rss_sum,
        private_bytes=private,
        shared_bytes=shared,
        posix_bytes=posix,
        posix_included=include_posix,
        total_bytes=total,
        method=method,
        shared_note=shared_note,
    )


def sid_rows(procs: list[Proc]) -> list[tuple[str, str, list[Proc]]]:
    buckets: dict[str, list[Proc]] = {}
    order: list[str] = []
    for proc in procs:
        if proc.sid:
            key = proc.sid
        elif proc.kind == "listener":
            key = "(listener)"
        elif proc.kind == "grid":
            key = "(grid)"
        elif proc.kind == "server":
            key = "(server)"
        else:
            key = "(other)"
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(proc)
    rows: list[tuple[str, str, list[Proc]]] = []
    for key in order:
        group = buckets[key]
        kinds = {proc.kind for proc in group}
        if "asm" in kinds:
            kind = "asm"
        elif "db" in kinds:
            kind = "db"
        elif "server" in kinds and "db" not in kinds:
            kind = "server"
        else:
            kind = group[0].kind
        rows.append((key, kind, group))
    rows.sort(key=lambda row: sum(p.rss_kb for p in row[2]), reverse=True)
    return rows


def print_mem(host: Host, top_n: int) -> None:
    print("Memory")
    info = host.meminfo
    if not info:
        print("  proc/meminfo not found")
    else:
        total = info.get("MemTotal")
        avail = info.get("MemAvailable")
        swap_total = info.get("SwapTotal")
        swap_free = info.get("SwapFree")
        print(f"  {'MemTotal':<22} {fmt_bytes((total or 0) * 1024) if total else 'n/a'}")
        if avail is not None:
            print(f"  {'MemAvailable':<22} {fmt_bytes(avail * 1024)}")
        if swap_total is not None:
            used = None if swap_free is None else max(0, swap_total - swap_free)
            swap_txt = fmt_bytes(swap_total * 1024)
            if used is not None:
                swap_txt += f"   used {fmt_bytes(used * 1024)}"
            print(f"  {'SwapTotal':<22} {swap_txt}")
        huge_n = info.get("HugePages_Total")
        huge_sz = info.get("Hugepagesize")
        if huge_n is not None and huge_sz:
            pool = huge_n * huge_sz * 1024
            pct = ""
            if total:
                pct = f"   {pool / (total * 1024) * 100:.1f}% of RAM"
            print(f"  {'HugePages':<22} {huge_n} x {huge_sz} KiB = {fmt_bytes(pool)}{pct}")
            free = info.get("HugePages_Free")
            rsvd = info.get("HugePages_Rsvd")
            surp = info.get("HugePages_Surp")
            extra = []
            if free is not None:
                extra.append(f"free {free}")
            if rsvd is not None:
                extra.append(f"rsvd {rsvd}")
            if surp is not None:
                extra.append(f"surp {surp}")
            if free is not None:
                in_use = max(0, huge_n - free)
                extra.append(f"in use {in_use} ({fmt_bytes(in_use * huge_sz * 1024)})")
            if extra:
                print(f"  {'':<22} {', '.join(extra)}")
        anon = info.get("AnonHugePages")
        if anon is not None:
            print(f"  {'AnonHugePages':<22} {fmt_bytes(anon * 1024)}")
        shmem = info.get("Shmem")
        if shmem is not None:
            print(f"  {'Shmem':<22} {fmt_bytes(shmem * 1024)}")
    if host.devshm_total_bytes is not None:
        used = "used n/a" if host.devshm_used_bytes is None else f"used {fmt_bytes(host.devshm_used_bytes)}"
        src = f"  [{host.devshm_src}]" if host.devshm_src else ""
        print(f"  {'/dev/shm':<22} {fmt_bytes(host.devshm_total_bytes)}   {used}{src}")
    if host.thp:
        src = f"  [{host.thp_src}]" if host.thp_src else ""
        print(f"  {'THP':<22} {host.thp}{src}")

    print()
    if host.ps_source:
        print(f"  Oracle processes       {len(host.procs)}  [{host.ps_source}]")
    elif host.procs:
        print(f"  Oracle processes       {len(host.procs)}")
    else:
        print("  Oracle processes       none found")
        print("    Looked for sos_commands/process/ps_auxwww (and proc/<pid> when no ps capture is present).")

    if host.procs:
        print()
        print(f"  {'SID':<18} {'KIND':<10} {'PROCS':>6} {'RSS':>14} {'ANON':>14}")
        for sid, kind, group in sid_rows(host.procs):
            rss = sum(p.rss_kb for p in group) * 1024
            anons = [p.anon_kb for p in group if p.anon_kb is not None]
            anon_txt = fmt_bytes(sum(anons) * 1024) if len(anons) == len(group) else "-"
            print(f"  {sid:<18} {kind:<10} {len(group):>6} {fmt_bytes(rss):>14} {anon_txt:>14}")
        rss_sum = sum(p.rss_kb for p in host.procs) * 1024
        print(f"  {'RSS sum':<18} {'':<10} {len(host.procs):>6} {fmt_bytes(rss_sum):>14}")
        print("  The RSS sum includes each shared mapping in every process, so it is not the footprint.")

        shown = sorted(host.procs, key=lambda p: p.rss_kb, reverse=True)[:top_n]
        print()
        print(f"  Top {len(shown)} of {len(host.procs)} by RSS")
        print(f"  {'PID':>8}  {'USER':<12} {'RSS':>12} {'VSZ':>12}  COMMAND")
        for proc in shown:
            sid = f" [{proc.sid}]" if proc.sid else ""
            cmd = trunc(proc.command, 72)
            print(
                f"  {proc.pid:>8}  {trunc(proc.user, 12):<12} "
                f"{fmt_bytes(proc.rss_kb * 1024):>12} {fmt_bytes(proc.vsz_kb * 1024):>12}  {cmd}{sid}"
            )

    segs = oracle_segments(host)
    print()
    if host.shm_source:
        print(f"  SysV shared memory     [{host.shm_source}]")
    else:
        print("  SysV shared memory     proc/sysvipc/shm not found")
    if segs:
        print(f"  {'SHMID':<10} {'OWNER':<12} {'SIZE':>12} {'RESIDENT':>12} {'SWAP':>12} {'NATTCH':>7}")
        for seg in sorted(segs, key=lambda s: s.size_bytes, reverse=True)[:top_n]:
            res = fmt_bytes(seg.rss_bytes) if seg.rss_bytes is not None else "-"
            swap = fmt_bytes(seg.swap_bytes) if seg.swap_bytes is not None else "-"
            print(
                f"  {trunc(seg.shmid, 10):<10} {trunc(seg.owner, 12):<12} "
                f"{fmt_bytes(seg.size_bytes):>12} {res:>12} {swap:>12} {seg.nattch:>7}"
            )
        alloc = sum(seg.size_bytes for seg in segs)
        resident_known = [seg.rss_bytes for seg in segs if seg.rss_bytes is not None]
        res_txt = fmt_bytes(sum(resident_known)) if resident_known else "n/a"
        print(f"  segments {len(segs)}   allocated {fmt_bytes(alloc)}   resident {res_txt}")
        if len(segs) > top_n:
            print(f"  showing {top_n} largest; {len(segs) - top_n} more")
    elif host.shm_source:
        print("  no Oracle-owned or large (>= 256 MiB) segments")

    if host.procs or segs or hugepage_bytes(host, "HugePages_Total"):
        foot = estimate_footprint(host, segs)
        total_mem = mem_bytes(host)
        pct = ""
        if total_mem:
            pct = f"   ({foot.total_bytes / total_mem * 100:.1f}% of MemTotal)"
        print()
        print("  Estimated footprint")
        print(f"  {'private':<22} {fmt_bytes(foot.private_bytes)}")
        print(f"    {foot.method}")
        print(f"  {'shared':<22} {fmt_bytes(foot.shared_bytes)}")
        print(f"    {foot.shared_note}")
        if foot.posix_bytes:
            added = "included in the total" if foot.posix_included else "not included in the total"
            print(f"  {'/dev/shm used':<22} {fmt_bytes(foot.posix_bytes)}   ({added})")
        print(f"  {'estimated total':<22} {fmt_bytes(foot.total_bytes)}{pct}")
        if total_mem and foot.total_bytes > total_mem * 1.05:
            print("    estimate is above MemTotal; shared pages are still counted more than once")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def sysctl_value(host: Host, key: str) -> tuple[str | None, str]:
    if key in host.sysctl:
        return host.sysctl[key], host.sysctl_src.get(key, "sysctl")
    if key in host.config:
        src = host.config_src.get(key, "sysctl config")
        return host.config[key], f"{src} (runtime not captured)"
    return None, ""


def persistent_note(host: Host, key: str) -> str:
    if key not in host.sysctl or key not in host.config:
        return ""
    if norm_ws(host.sysctl[key]) == norm_ws(host.config[key]):
        return ""
    src = host.config_src.get(key, "sysctl config")
    return f"; persistent {src}={host.config[key]}"


def user_gids(host: Host, user: str) -> set[int]:
    gids: set[int] = set()
    rec = host.users.get(user)
    if rec:
        gids.add(rec.gid)
    for group in host.groups.values():
        if user in group.members:
            gids.add(group.gid)
    return gids


def group_names_for_gid(host: Host, gid: int) -> list[str]:
    return sorted(group.name for group in host.groups.values() if group.gid == gid)


def domain_matches(domain: str, user: str, gids: set[int], host: Host) -> bool:
    if domain == "*":
        return True
    if domain == user:
        return True
    if domain.startswith("@") or domain.startswith("%"):
        name = domain[1:]
        group = host.groups.get(name)
        return bool(group and group.gid in gids)
    return False


def effective_limit(host: Host, user: str, item: str, typ: str) -> int | None | object:
    gids = user_gids(host, user)
    found = MISSING
    for entry in host.limits:
        if entry.item != item or not domain_matches(entry.domain, user, gids, host):
            continue
        types = ("soft", "hard") if entry.type == "-" else (entry.type,)
        if typ not in types:
            continue
        found = entry.value
    return found


def limit_text(value: int | None | object) -> str:
    if value is MISSING:
        return "unset"
    if value is None:
        return "unlimited"
    return fmt_int(int(value))


def cmdline_thp(host: Host) -> str | None:
    if not host.cmdline:
        return None
    values = cmdline_map(host.cmdline).get("transparent_hugepage")
    if not values:
        return None
    return values[-1].lower()


def dirtyable_bytes(host: Host) -> int | None:
    total = mem_bytes(host)
    if total is None:
        return None
    huge = hugepage_bytes(host, "HugePages_Total") or 0
    base = total - huge
    return base if base > 0 else total


def scheduler_ok(sched: str, rotational: int | None) -> bool:
    name = sched.lower()
    if name in {"mq-deadline", "deadline"}:
        return True
    if name in {"none", "noop", "kyber"}:
        return rotational != 1
    return False


def target_limit_users(host: Host) -> list[str]:
    names: list[str] = []
    for name in ("oracle", "grid"):
        if name in host.users:
            names.append(name)
    for proc in host.procs:
        if proc.user not in names and proc.user not in {"?", "root"}:
            names.append(proc.user)
    return names


def build_checks(host: Host) -> list[tuple[str, Check]]:
    checks: list[tuple[str, Check]] = []

    def add(section: str, status: str, title: str, detail: str = "") -> None:
        checks.append((section, Check(status, title, detail)))

    def require_at_least(section: str, key: str, minimum: int, severity: str) -> None:
        raw, src = sysctl_value(host, key)
        if raw is None:
            add(section, "SKIP", key, "not captured")
            return
        value = parse_int(raw)
        if value is None:
            add(section, "WARN", key, f"unparsed value {raw!r} ({src})")
            return
        ok = value >= minimum
        status = "PASS" if ok else severity
        relation = ">=" if ok else "<"
        add(
            section,
            status,
            key,
            f"{fmt_int(value)} {relation} {fmt_int(minimum)} ({src}){persistent_note(host, key)}",
        )

    # Profile
    section = "Profile"
    if host.tuned is None:
        add(section, "SKIP", "TuneD profile", "active profile not captured")
    elif host.tuned == "oracle" or host.tuned.startswith("oracle-"):
        add(section, "PASS", "TuneD profile", f"{host.tuned} ({host.tuned_src})")
    else:
        add(
            section,
            "WARN",
            "TuneD profile",
            f"{host.tuned} ({host.tuned_src}); Red Hat recommends "
            "tuned-adm profile oracle from tuned-profiles-oracle",
        )

    # THP and NUMA
    section = "Transparent HugePages and NUMA"
    cmd_thp = cmdline_thp(host)
    if host.thp:
        detail = f"{host.thp} ({host.thp_src})"
        if cmd_thp and cmd_thp != host.thp:
            detail += f"; cmdline requests {cmd_thp}"
        if host.thp == "never":
            add(section, "PASS", "transparent_hugepage", detail)
        else:
            anon = host.meminfo.get("AnonHugePages")
            if anon:
                detail += f"; AnonHugePages {fmt_bytes(anon * 1024)}"
            add(
                section,
                "FAIL",
                "transparent_hugepage",
                f"{detail}; Oracle on RHEL requires never "
                "(static HugePages, not THP)",
            )
    elif cmd_thp:
        status = "PASS" if cmd_thp == "never" else "FAIL"
        note = "" if cmd_thp == "never" else "; Oracle on RHEL requires never"
        add(
            section,
            status,
            "transparent_hugepage",
            f"cmdline={cmd_thp}; sysfs state was not captured{note}",
        )
    else:
        add(section, "WARN", "transparent_hugepage", "state not found in sysfs or proc/cmdline")

    raw, src = sysctl_value(host, "kernel.numa_balancing")
    if raw is None:
        add(section, "SKIP", "kernel.numa_balancing", "not captured")
    else:
        value = parse_int(raw)
        if value == 0:
            add(section, "PASS", "kernel.numa_balancing", f"0 ({src}){persistent_note(host, 'kernel.numa_balancing')}")
        elif value is None:
            add(section, "WARN", "kernel.numa_balancing", f"unparsed value {raw!r}")
        else:
            add(
                section,
                "FAIL",
                "kernel.numa_balancing",
                f"{value} ({src}); Red Hat recommends 0 for Oracle. "
                "Leave NUMA itself enabled so the database can place memory.",
            )
    if host.cmdline is None:
        add(section, "SKIP", "numa=off", "proc/cmdline not captured")
    elif "off" in cmdline_map(host.cmdline).get("numa", []):
        add(
            section,
            "WARN",
            "numa=off",
            "kernel cmdline disables NUMA. Red Hat recommends numa_balancing=0 "
            "and leaving NUMA on; numa=off disables Oracle NUMA placement",
        )
    else:
        add(section, "PASS", "numa=off", "NUMA is not disabled on the kernel cmdline")

    # Virtual memory
    section = "Virtual memory"
    raw, src = sysctl_value(host, "vm.swappiness")
    if raw is None:
        add(section, "SKIP", "vm.swappiness", "not captured")
    else:
        value = parse_int(raw)
        note = persistent_note(host, "vm.swappiness")
        if value == 10:
            add(section, "PASS", "vm.swappiness", f"10 ({src}); Red Hat oracle profile and Performance Tuning Guide{note}")
        elif value == 1:
            add(section, "PASS", "vm.swappiness", f"1 ({src}); low swapping. The oracle TuneD profile sets 10{note}")
        elif value == 0:
            add(
                section,
                "WARN",
                "vm.swappiness",
                f"0 ({src}); on RHEL 6.4+ this raises OOM risk. Recommended 10{note}",
            )
        elif value is None:
            add(section, "WARN", "vm.swappiness", f"unparsed value {raw!r}")
        else:
            add(section, "WARN", "vm.swappiness", f"{value} ({src}); recommended 10{note}")

    add_dirty_checks(host, add)
    raw, src = sysctl_value(host, "vm.zone_reclaim_mode")
    if raw is None:
        add(section, "SKIP", "vm.zone_reclaim_mode", "not captured")
    else:
        value = parse_int(raw)
        if value == 0:
            add(section, "PASS", "vm.zone_reclaim_mode", f"0 ({src})")
        elif value is None:
            add(section, "WARN", "vm.zone_reclaim_mode", f"unparsed value {raw!r}")
        else:
            add(
                section,
                "WARN",
                "vm.zone_reclaim_mode",
                f"{value} ({src}); non-zero reclaim prefers the local NUMA node "
                "and is not recommended for Oracle. Use 0",
            )

    # Shared memory
    section = "Shared memory and semaphores"
    check_shmmax(host, add)
    check_shmall(host, add)
    require_at_least(section, "kernel.shmmni", SHMMNI_MIN, "FAIL")
    check_sem(host, add)

    section = "Files and asynchronous I/O"
    require_at_least(section, "fs.file-max", FILE_MAX_MIN, "FAIL")
    require_at_least(section, "fs.aio-max-nr", AIO_MAX_MIN, "FAIL")
    check_file_pressure(host, add)
    check_aio_pressure(host, add)

    section = "Network"
    check_port_range(host, add)
    require_at_least(section, "net.core.rmem_default", RMEM_DEFAULT_MIN, "FAIL")
    require_at_least(section, "net.core.rmem_max", RMEM_MAX_MIN, "FAIL")
    require_at_least(section, "net.core.wmem_default", WMEM_DEFAULT_MIN, "FAIL")
    require_at_least(section, "net.core.wmem_max", WMEM_MAX_MIN, "FAIL")

    section = "Kernel"
    raw, src = sysctl_value(host, "kernel.panic_on_oops")
    if raw is None:
        add(section, "SKIP", "kernel.panic_on_oops", "not captured")
    else:
        value = parse_int(raw)
        if value == 1:
            add(section, "PASS", "kernel.panic_on_oops", f"1 ({src}){persistent_note(host, 'kernel.panic_on_oops')}")
        else:
            add(
                section,
                "FAIL",
                "kernel.panic_on_oops",
                f"{raw} ({src}); Oracle requires 1{persistent_note(host, 'kernel.panic_on_oops')}",
            )

    section = "Swap"
    check_swap(host, add)

    section = "HugePages"
    check_hugepages(host, add)
    check_hugetlb_group(host, add)

    section = "systemd IPC and limits"
    check_remove_ipc(host, add)
    check_user_limits(host, add)

    section = "I/O and CPU"
    check_schedulers(host, add)
    check_governor(host, add)
    check_devshm(host, add)
    return checks


def add_dirty_checks(host: Host, add) -> None:
    section = "Virtual memory"
    base = dirtyable_bytes(host)

    def one(
        title: str,
        ratio_key: str,
        bytes_key: str,
        target: int,
        low: float,
        high: float,
        wide_low: float,
        wide_high: float,
    ) -> None:
        ratio_raw, ratio_src = sysctl_value(host, ratio_key)
        bytes_raw, bytes_src = sysctl_value(host, bytes_key)
        if ratio_raw is None and bytes_raw is None:
            add(section, "SKIP", title, "not captured")
            return
        ratio = parse_int(ratio_raw) if ratio_raw is not None else 0
        nbytes = parse_int(bytes_raw) if bytes_raw is not None else 0
        if ratio is None or nbytes is None:
            add(section, "WARN", title, f"unparsed {ratio_raw!r} / {bytes_raw!r}")
            return
        if nbytes > 0:
            if base is None:
                add(section, "SKIP", title, f"{bytes_key}={fmt_int(nbytes)} but MemTotal is missing")
                return
            pct = nbytes / base * 100.0
            note = persistent_note(host, bytes_key)
            if low <= pct <= high:
                add(section, "PASS", title, f"{bytes_key}={fmt_bytes(nbytes)} ({pct:.1f}% of dirtyable, target {target}%) ({bytes_src}){note}")
            elif wide_low <= pct <= wide_high:
                add(
                    section,
                    "WARN",
                    title,
                    f"{bytes_key}={fmt_bytes(nbytes)} is {pct:.1f}% of dirtyable; "
                    f"the oracle TuneD profile uses {target}%{note}",
                )
            else:
                add(
                    section,
                    "WARN",
                    title,
                    f"{bytes_key}={fmt_bytes(nbytes)} is {pct:.1f}% of dirtyable; "
                    f"recommended about {target}%{note}",
                )
            return
        if ratio > 0:
            note = persistent_note(host, ratio_key)
            if ratio == target:
                add(section, "PASS", title, f"{ratio_key}={ratio} ({ratio_src}){note}")
            elif wide_low <= ratio <= wide_high:
                add(
                    section,
                    "WARN",
                    title,
                    f"{ratio_key}={ratio} ({ratio_src}); the oracle TuneD profile uses {target}{note}",
                )
            else:
                add(section, "WARN", title, f"{ratio_key}={ratio} ({ratio_src}); recommended {target}{note}")
            return
        add(section, "WARN", title, f"{ratio_key} and {bytes_key} are both 0")

    one("vm.dirty_background", "vm.dirty_background_ratio", "vm.dirty_background_bytes",
        DIRTY_BG_TARGET, 2.0, 5.0, 1.0, 10.0)
    one("vm.dirty", "vm.dirty_ratio", "vm.dirty_bytes",
        DIRTY_RATIO_TARGET, 35.0, 45.0, 15.0, 80.0)

    for key, recommended, default in (
        ("vm.dirty_expire_centisecs", DIRTY_EXPIRE, 3000),
        ("vm.dirty_writeback_centisecs", DIRTY_WRITEBACK, 500),
    ):
        raw, src = sysctl_value(host, key)
        if raw is None:
            add(section, "SKIP", key, "not captured")
            continue
        value = parse_int(raw)
        note = persistent_note(host, key)
        if value == recommended:
            add(section, "PASS", key, f"{value} ({src}){note}")
        elif value is None:
            add(section, "WARN", key, f"unparsed value {raw!r}")
        else:
            add(
                section,
                "WARN",
                key,
                f"{value} ({src}); oracle TuneD profile sets {recommended} (kernel default {default}){note}",
            )


def check_shmmax(host: Host, add) -> None:
    section = "Shared memory and semaphores"
    raw, src = sysctl_value(host, "kernel.shmmax")
    if raw is None:
        add(section, "SKIP", "kernel.shmmax", "not captured")
        return
    value = parse_int(raw)
    if value is None:
        add(section, "WARN", "kernel.shmmax", f"unparsed value {raw!r}")
        return
    note = persistent_note(host, "kernel.shmmax")
    total = mem_bytes(host)
    half = total // 2 if total else None
    shown = f"{fmt_int(value)} ({fmt_bytes(value)})"
    if half is not None and (value >= half or value >= SHMMAX_TUNED):
        why = ">= half of RAM" if value >= half else "4 TiB TuneD/preinstall value"
        add(section, "PASS", "kernel.shmmax", f"{shown} {why} ({src}){note}")
        if half is not None and value < half:
            add(
                section,
                "WARN",
                "kernel.shmmax vs RAM",
                f"{fmt_bytes(value)} is below half of RAM ({fmt_bytes(half)}). "
                "Oracle's minimum is half of physical memory; this still matches the 4 TiB profile value",
            )
        return
    if half is None:
        if value >= SHMMAX_TUNED:
            add(section, "PASS", "kernel.shmmax", f"{shown} matches the 4 TiB profile value; MemTotal missing ({src}){note}")
        else:
            add(section, "WARN", "kernel.shmmax", f"{shown} ({src}); MemTotal missing, cannot compare with half of RAM{note}")
        return
    add(
        section,
        "WARN",
        "kernel.shmmax",
        f"{shown} is below half of RAM ({fmt_bytes(half)}) and below the 4 TiB "
        f"oracle profile value ({src}){note}",
    )


def check_shmall(host: Host, add) -> None:
    section = "Shared memory and semaphores"
    raw, src = sysctl_value(host, "kernel.shmall")
    if raw is None:
        add(section, "SKIP", "kernel.shmall", "not captured")
        return
    pages = parse_int(raw)
    if pages is None:
        add(section, "WARN", "kernel.shmall", f"unparsed value {raw!r}")
        return
    cover = pages * host.pagesize
    note = persistent_note(host, "kernel.shmall")
    shmmax_raw, _ = sysctl_value(host, "kernel.shmmax")
    shmmax = parse_int(shmmax_raw) if shmmax_raw else None
    detail = f"{fmt_int(pages)} pages = {fmt_bytes(cover)} ({src}, page size {host.pagesize}){note}"
    if shmmax is not None and cover < shmmax:
        add(
            section,
            "FAIL",
            "kernel.shmall",
            f"{detail}; smaller than kernel.shmmax {fmt_bytes(shmmax)}. "
            "Oracle requires shmall >= shmmax in pages",
        )
        return
    total = mem_bytes(host)
    if total is not None and cover < total:
        add(
            section,
            "WARN",
            "kernel.shmall",
            f"{detail}; does not cover MemTotal {fmt_bytes(total)}",
        )
        return
    add(section, "PASS", "kernel.shmall", detail)


def check_sem(host: Host, add) -> None:
    section = "Shared memory and semaphores"
    raw, src = sysctl_value(host, "kernel.sem")
    if raw is None:
        add(section, "SKIP", "kernel.sem", "not captured")
        return
    values = parse_ints(raw)
    if not values or len(values) != 4:
        add(section, "WARN", "kernel.sem", f"expected 4 integers, got {raw!r} ({src})")
        return
    short = [
        f"{SEM_NAMES[i]} {values[i]} < {SEM_MIN[i]}"
        for i in range(4)
        if values[i] < SEM_MIN[i]
    ]
    shown = " ".join(str(v) for v in values)
    note = persistent_note(host, "kernel.sem")
    if short:
        add(
            section,
            "FAIL",
            "kernel.sem",
            f"{shown} ({src}); {', '.join(short)}; minimum is 250 32000 100 128{note}",
        )
    else:
        add(section, "PASS", "kernel.sem", f"{shown} ({src}); minimum 250 32000 100 128{note}")


def check_port_range(host: Host, add) -> None:
    section = "Network"
    key = "net.ipv4.ip_local_port_range"
    raw, src = sysctl_value(host, key)
    if raw is None:
        add(section, "SKIP", key, "not captured")
        return
    values = parse_ints(raw)
    if not values or len(values) != 2:
        add(section, "WARN", key, f"expected 2 integers, got {raw!r}")
        return
    lo, hi = values
    note = persistent_note(host, key)
    if lo <= PORT_START_MAX and hi >= PORT_END_MIN:
        add(section, "PASS", key, f"{lo} {hi} ({src}); covers 9000-{PORT_END_MIN}{note}")
    else:
        add(
            section,
            "FAIL",
            key,
            f"{lo} {hi} ({src}); Oracle wants a range covering 9000-65500 "
            f"(TuneD oracle profile uses 9000 {PORT_END_MIN}){note}",
        )


def check_file_pressure(host: Host, add) -> None:
    section = "Files and asynchronous I/O"
    raw, src = sysctl_value(host, "fs.file-nr")
    if raw is None:
        add(section, "SKIP", "fs.file-nr", "not captured")
        return
    values = parse_ints(raw)
    if not values or len(values) < 3:
        add(section, "WARN", "fs.file-nr", f"unparsed value {raw!r}")
        return
    allocated, _free, limit = values[0], values[1], values[2]
    if limit <= 0:
        add(section, "WARN", "fs.file-nr", f"{raw} ({src})")
        return
    pct = allocated / limit * 100.0
    if allocated >= limit * PRESSURE_FRACTION:
        add(section, "WARN", "fs.file-nr", f"{fmt_int(allocated)} of {fmt_int(limit)} allocated ({pct:.1f}%) ({src})")
    else:
        add(section, "PASS", "fs.file-nr", f"{fmt_int(allocated)} of {fmt_int(limit)} allocated ({pct:.1f}%) ({src})")


def check_aio_pressure(host: Host, add) -> None:
    section = "Files and asynchronous I/O"
    raw, src = sysctl_value(host, "fs.aio-nr")
    limit_raw, _ = sysctl_value(host, "fs.aio-max-nr")
    if raw is None or limit_raw is None:
        add(section, "SKIP", "fs.aio-nr", "aio-nr or aio-max-nr not captured")
        return
    used = parse_int(raw)
    limit = parse_int(limit_raw)
    if used is None or limit is None or limit <= 0:
        add(section, "WARN", "fs.aio-nr", f"unparsed {raw!r} / {limit_raw!r}")
        return
    pct = used / limit * 100.0
    if used >= limit * PRESSURE_FRACTION:
        add(section, "WARN", "fs.aio-nr", f"{fmt_int(used)} of {fmt_int(limit)} ({pct:.1f}%) ({src})")
    else:
        add(section, "PASS", "fs.aio-nr", f"{fmt_int(used)} of {fmt_int(limit)} ({pct:.1f}%) ({src})")


def check_swap(host: Host, add) -> None:
    section = "Swap"
    mem_kb = host.meminfo.get("MemTotal")
    swap_kb = host.meminfo.get("SwapTotal")
    if mem_kb is None or swap_kb is None:
        add(section, "SKIP", "swap size", "MemTotal or SwapTotal not in proc/meminfo")
        return
    recommended = recommended_swap_kb(mem_kb)
    mem_b = mem_kb * 1024
    gib = 1024 ** 3
    if mem_b <= 2 * gib:
        rule = "RAM <= 2 GiB, swap >= 1.5x RAM"
    elif mem_b <= 16 * gib:
        rule = "RAM <= 16 GiB, swap >= RAM"
    else:
        rule = "RAM > 16 GiB, swap >= 16 GiB"
    detail = (
        f"SwapTotal {fmt_bytes(swap_kb * 1024)}, recommended {fmt_bytes(recommended * 1024)} ({rule})"
    )
    if swap_kb >= int(recommended * 0.95):
        add(section, "PASS", "swap size", detail)
    else:
        add(section, "WARN", "swap size", detail)


def check_hugepages(host: Host, add) -> None:
    section = "HugePages"
    total = host.meminfo.get("HugePages_Total")
    size_kb = host.meminfo.get("Hugepagesize")
    if total is None:
        add(section, "SKIP", "HugePages", "HugePages_Total not in proc/meminfo")
        return
    size_kb = size_kb or 2048
    pool = total * size_kb * 1024
    free = host.meminfo.get("HugePages_Free")
    rsvd = host.meminfo.get("HugePages_Rsvd") or 0
    ram = mem_bytes(host)
    nr_raw, _ = sysctl_value(host, "vm.nr_hugepages")
    nr = parse_int(nr_raw) if nr_raw else None
    if total == 0:
        if oracle_evidence(host):
            extra = ""
            alloc = sum(seg.size_bytes for seg in oracle_segments(host))
            if alloc:
                extra = f" SysV shared memory is {fmt_bytes(alloc)} and is not in a HugePages pool."
            add(
                section,
                "WARN",
                "HugePages",
                "no static HugePages reserved. Red Hat recommends a HugePages pool for the "
                "SGA and transparent_hugepage=never. AMM via /dev/shm cannot use HugePages."
                + extra,
            )
        else:
            add(section, "INFO", "HugePages", "no static HugePages, and no Oracle user, oratab, or processes were found")
        return
    problems: list[str] = []
    if ram and pool > ram * HUGEPAGE_RAM_FRACTION:
        problems.append(
            f"pool is {pool / ram * 100:.1f}% of RAM; leave memory for the OS and PGA "
            f"(about {HUGEPAGE_RAM_FRACTION * 100:.0f}% maximum)"
        )
    if free is not None and total > 0:
        unused = max(0, free - rsvd)
        if pool >= 1024 ** 3 and unused / total > HUGEPAGE_UNUSED_FRACTION:
            problems.append(
                f"{unused / total * 100:.0f}% of the pool is unused "
                f"(free {free}, reserved {rsvd})"
            )
    if nr is not None and nr > total:
        problems.append(f"vm.nr_hugepages={nr} but only {total} pages are allocated")
    detail = f"{total} x {size_kb} KiB = {fmt_bytes(pool)}"
    if free is not None:
        detail += f", free {free}, rsvd {rsvd}"
    if problems:
        add(section, "WARN", "HugePages", detail + "; " + "; ".join(problems))
    else:
        add(section, "PASS", "HugePages", detail)


def check_hugetlb_group(host: Host, add) -> None:
    section = "HugePages"
    total = host.meminfo.get("HugePages_Total")
    if not total:
        add(section, "SKIP", "vm.hugetlb_shm_group", "no HugePages pool")
        return
    raw, src = sysctl_value(host, "vm.hugetlb_shm_group")
    if raw is None:
        add(section, "WARN", "vm.hugetlb_shm_group", "not captured; Oracle needs the dba/oinstall gid to use SHM_HUGETLB")
        return
    gid = parse_int(raw)
    if gid is None:
        add(section, "WARN", "vm.hugetlb_shm_group", f"unparsed value {raw!r}")
        return
    if gid == 0:
        add(
            section,
            "WARN",
            "vm.hugetlb_shm_group",
            f"0 ({src}); set this to the dba or oinstall gid so the Oracle user can allocate HugePages",
        )
        return
    names = group_names_for_gid(host, gid)
    label = ",".join(names) if names else "group name not in etc/group"
    outsiders = []
    for user in target_limit_users(host):
        if gid not in user_gids(host, user):
            outsiders.append(user)
    detail = f"{gid} ({label}) ({src}){persistent_note(host, 'vm.hugetlb_shm_group')}"
    if outsiders:
        add(section, "WARN", "vm.hugetlb_shm_group", f"{detail}; not a group of: {', '.join(outsiders)}")
    else:
        add(section, "PASS", "vm.hugetlb_shm_group", detail)


def check_remove_ipc(host: Host, add) -> None:
    section = "systemd IPC and limits"
    if host.remove_ipc is False:
        add(section, "PASS", "RemoveIPC", f"no ({host.remove_ipc_src})")
    elif host.remove_ipc is True:
        add(
            section,
            "FAIL",
            "RemoveIPC",
            f"yes ({host.remove_ipc_src}); systemd can delete Oracle shared memory "
            "when the last session for that user ends. Set RemoveIPC=no",
        )
    else:
        add(
            section,
            "WARN",
            "RemoveIPC",
            "unset. The systemd default on RHEL 7+ is yes, which can remove Oracle "
            "shared memory at logout. Set RemoveIPC=no in /etc/systemd/logind.conf",
        )


def memlock_required_kb(host: Host) -> int | None:
    total = host.meminfo.get("HugePages_Total")
    size_kb = host.meminfo.get("Hugepagesize") or 0
    if total and size_kb:
        return total * size_kb
    mem_kb = host.meminfo.get("MemTotal")
    if mem_kb:
        return int(mem_kb * 0.9)
    return None


def check_user_limits(host: Host, add) -> None:
    section = "systemd IPC and limits"
    users = target_limit_users(host)
    if not users:
        add(section, "SKIP", "user limits", "no oracle/grid user in passwd and no Oracle process owner")
        return
    if not host.limits:
        add(
            section,
            "WARN",
            "user limits",
            "etc/security/limits.conf and limits.d were not captured",
        )
        return
    need_memlock = memlock_required_kb(host)
    huge = bool(host.meminfo.get("HugePages_Total"))
    failures: list[str] = []
    memlock_fail = False
    for user in users:
        bits = []
        memlock = effective_limit(host, user, "memlock", "hard")
        nofile = effective_limit(host, user, "nofile", "hard")
        nproc = effective_limit(host, user, "nproc", "hard")
        stack = effective_limit(host, user, "stack", "hard")
        memlock_ok = memlock is None or (
            memlock is not MISSING and (need_memlock is None or int(memlock) >= need_memlock)
        )
        if memlock is MISSING:
            memlock_ok = False
        if not memlock_ok:
            if huge and need_memlock is not None:
                need_txt = f"{fmt_bytes(need_memlock * 1024)} (the HugePages pool)"
            elif need_memlock is not None:
                need_txt = f"{fmt_bytes(need_memlock * 1024)} (90% of RAM) or unlimited"
            else:
                need_txt = "unlimited"
            bits.append(f"memlock {limit_text(memlock)} (need >= {need_txt})")
            if huge:
                memlock_fail = True
        nofile_ok = nofile is None or (nofile is not MISSING and int(nofile) >= NOFILE_MIN)
        if not nofile_ok:
            bits.append(f"nofile {limit_text(nofile)} (need >= {fmt_int(NOFILE_MIN)})")
        nproc_ok = nproc is None or (nproc is not MISSING and int(nproc) >= NPROC_MIN)
        if not nproc_ok:
            bits.append(f"nproc {limit_text(nproc)} (need >= {fmt_int(NPROC_MIN)})")
        stack_ok = stack is None or (stack is not MISSING and int(stack) >= STACK_MIN_KB)
        if not stack_ok:
            bits.append(f"stack {limit_text(stack)} (need >= {fmt_bytes(STACK_MIN_KB * 1024)})")
        if bits:
            failures.append(f"{user}: " + ", ".join(bits))
    if not failures:
        summary = []
        for user in users:
            memlock = effective_limit(host, user, "memlock", "hard")
            nofile = effective_limit(host, user, "nofile", "hard")
            nproc = effective_limit(host, user, "nproc", "hard")
            stack = effective_limit(host, user, "stack", "hard")
            summary.append(
                f"{user} memlock {limit_text(memlock)}, nofile {limit_text(nofile)}, "
                f"nproc {limit_text(nproc)}, stack {limit_text(stack)}"
            )
        add(section, "PASS", "user limits", "; ".join(summary))
        return
    status = "FAIL" if memlock_fail else "WARN"
    add(section, status, "user limits", "; ".join(failures))


def check_schedulers(host: Host, add) -> None:
    section = "I/O and CPU"
    if not host.schedulers:
        add(section, "SKIP", "I/O scheduler", "sys/block/*/queue/scheduler not captured")
        return
    bad: list[str] = []
    good: list[str] = []
    for dev, sched, rot in host.schedulers:
        kind = "HDD" if rot == 1 else "non-rotational" if rot == 0 else "disk"
        if scheduler_ok(sched, rot):
            good.append(f"{dev}={sched}")
        else:
            expect = "mq-deadline" if rot == 1 else "mq-deadline or none"
            bad.append(f"{dev} ({kind}) uses {sched}; expected {expect}")
    if not bad:
        shown = ", ".join(good[:6])
        if len(good) > 6:
            shown += f", +{len(good) - 6} more"
        add(section, "PASS", "I/O scheduler", shown)
        return
    detail = "; ".join(bad[:6])
    if len(bad) > 6:
        detail += f"; +{len(bad) - 6} more"
    if good:
        detail += f"; {len(good)} other device(s) OK"
    add(section, "WARN", "I/O scheduler", detail)


def check_governor(host: Host, add) -> None:
    section = "I/O and CPU"
    if not host.governors:
        add(section, "SKIP", "CPU governor", "cpufreq scaling_governor not captured")
        return
    parts = [f"{name} x {count}" for name, count in sorted(host.governors.items())]
    if set(host.governors) == {"performance"}:
        add(section, "PASS", "CPU governor", ", ".join(parts))
    else:
        add(
            section,
            "WARN",
            "CPU governor",
            ", ".join(parts) + "; the oracle profile (via throughput-performance) sets performance",
        )


def check_devshm(host: Host, add) -> None:
    section = "I/O and CPU"
    if host.devshm_total_bytes is None:
        add(section, "SKIP", "/dev/shm", "df and proc/mounts did not show /dev/shm")
        return
    total = host.devshm_total_bytes
    used = host.devshm_used_bytes
    src = f" ({host.devshm_src})" if host.devshm_src else ""
    if used is None:
        add(section, "INFO", "/dev/shm", f"size {fmt_bytes(total)}{src}; used bytes not captured")
        return
    pct = used / total * 100.0 if total else 0.0
    detail = f"used {fmt_bytes(used)} of {fmt_bytes(total)} ({pct:.1f}%){src}"
    if total > 0 and used >= total * DEVSHM_FULL_FRACTION:
        add(
            section,
            "WARN",
            "/dev/shm",
            detail + "; MEMORY_TARGET/AMM stores the SGA here and needs free space. "
            "HugePages and /dev/shm cannot back the same SGA",
        )
    else:
        add(section, "PASS", "/dev/shm", detail)


def print_checks(checks: list[tuple[str, Check]], verbose: bool, color: bool) -> str:
    counts = {name: 0 for name in ("PASS", "WARN", "FAIL", "INFO", "SKIP")}
    current = None
    shown = False
    for section, check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
        if check.status == "SKIP" and not verbose:
            continue
        if section != current:
            if shown:
                print()
            print(section)
            current = section
            shown = True
        line = f"  {c_status(color, check.status)} {check.title}"
        if check.detail:
            line += f" — {check.detail}"
        print(line)
    print()
    if counts["FAIL"]:
        verdict = "FAIL"
    elif counts["WARN"]:
        verdict = "WARN"
    elif counts["PASS"] or counts["INFO"]:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    skip = f" skip={counts['SKIP']}" if verbose else ""
    print(
        f"Verdict: {c_verdict(color, verdict)}  "
        f"(pass={counts['PASS']} warn={counts['WARN']} fail={counts['FAIL']} "
        f"info={counts['INFO']}{skip})"
    )
    substantive = counts["PASS"] + counts["WARN"] + counts["FAIL"] + counts["INFO"]
    if substantive == 0:
        return "EMPTY"
    return verdict


def print_banner(host: Host) -> None:
    print(f"Oracle checks  (path: {host.root})")
    if host.os_name:
        print(f"  {'OS':<22} {host.os_name}")
    total = host.meminfo.get("MemTotal")
    if total:
        print(f"  {'MemTotal':<22} {fmt_bytes(total * 1024)}")
    if host.numa_nodes is not None:
        print(f"  {'NUMA nodes':<22} {host.numa_nodes}")
    tuned = host.tuned or "(not captured)"
    if host.tuned_src:
        tuned += f"  [{host.tuned_src}]"
    print(f"  {'TuneD':<22} {tuned}")
    accounts = [name for name in ("oracle", "grid") if name in host.users]
    if accounts:
        print(f"  {'accounts':<22} {', '.join(accounts)}")
    if host.oratab:
        sids = ", ".join(sid for sid, _home, _flag in host.oratab)
        print(f"  {'oratab':<22} {sids}")
    else:
        print(f"  {'oratab':<22} (none)")
    proc_txt = str(len(host.procs))
    if host.ps_source:
        proc_txt += f"  [{host.ps_source}]"
    print(f"  {'Oracle processes':<22} {proc_txt}")
    print(f"  {'base page size':<22} {host.pagesize}")
    if not oracle_evidence(host):
        print("  note                   no Oracle processes, oratab, or oracle/grid user found")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chk_oracle.py",
        description="Check Oracle memory and RHEL tuning in a sosreport or a live filesystem root.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  chk_oracle.py --path /var/tmp/sosreport-host\n"
            "  chk_oracle.py --mem --path /var/tmp/sosreport-host\n"
            "  chk_oracle.py --validate --path /\n"
        ),
    )
    parser.add_argument("--path", default=".", help="sosreport root or filesystem root (default: .)")
    parser.add_argument("--mem", action="store_true", help="show memory used by Oracle")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="check tuning against Red Hat Oracle recommendations (https://access.redhat.com/solutions/39188)",
    )
    parser.add_argument("--top", type=int, default=15, metavar="N", help="processes and shm segments to list (default: 15)")
    parser.add_argument(
        "--pagesize",
        type=int,
        default=None,
        help="base page size in bytes (default: getconf PAGE_SIZE from the sosreport, else 4096)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show SKIP checks")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        return 2
    if args.top < 1:
        print("Error: --top must be >= 1", file=sys.stderr)
        return 2
    if args.pagesize is not None and args.pagesize <= 0:
        print("Error: --pagesize must be > 0", file=sys.stderr)
        return 2
    if not looks_like_system(root):
        print(
            f"Error: {root} has no proc, sys, etc, or sos_commands directory",
            file=sys.stderr,
        )
        return 2

    do_mem = args.mem or not args.validate
    do_val = args.validate or not args.mem
    host = load_host(root, detect_pagesize(root, args.pagesize))
    color = use_color(args.no_color)
    print_banner(host)
    if do_mem:
        print()
        print_mem(host, args.top)
    if not do_val:
        return 0
    print()
    print("Tuning validation")
    print("Reference: https://access.redhat.com/solutions/39188")
    print("           RHEL TuneD profile oracle and Oracle Database minimum kernel parameters")
    print()
    verdict = print_checks(build_checks(host), verbose=args.verbose, color=color)
    if verdict == "EMPTY":
        print("No tuning data was found to validate.", file=sys.stderr)
        return 2
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
