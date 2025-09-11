#!/usr/bin/env python3
"""
chk_cg.py - Check for cgroup deviations from defaults inside a sosreport snapshot.

Usage:
  ./chk_cg.py [-v] [-d] [-u UNIT_PATTERN]

Options:
  -v                 Verbose output (more details)
  -d                 Debug output (trace IO)
  -u UNIT_PATTERN    Filter by substring. If pattern looks like a specific
                     unit/slice (*.service|*.scope|*.slice), a detailed report
                     for matching entries is printed.

Notes:
  - Reads ONLY from the current directory (sosreport root).
  - Supports cgroup v2 and v1 (cpu/memory/pids).
  - Detects deviations by comparing each directory to ITS PARENT.
"""

import argparse
import sys
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# ---------------------- Globals ----------------------
VERBOSE = False
DEBUG = False
UNIT_FILTER: Optional[str] = None
ROOT = Path(".").resolve()

def vprint(*a, **k):
    if VERBOSE:
        print(*a, **k)

def dprint(*a, **k):
    if DEBUG:
        print("[DBG]", *a, **k)

def match_unit(path_str: str) -> bool:
    if UNIT_FILTER is None:
        return True
    return UNIT_FILTER in path_str

def looks_like_specific_unit(s: Optional[str]) -> bool:
    if not s:
        return False
    return s.endswith((".service", ".scope", ".slice"))

# ---------------------- FS helpers ----------------------
def exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False

def list_dirs(p: Path) -> List[Path]:
    try:
        return [x for x in p.iterdir() if x.is_dir()]
    except Exception as e:
        dprint(f"failed to list {p}: {e}")
        return []

def read_first_line(p: Path) -> Optional[str]:
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            s = f.readline().strip()
            dprint(f"read {p}: {s!r}")
            return s
    except Exception as e:
        dprint(f"failed to read {p}: {e}")
        return None

# ---------------------- Utilities for pretty output ----------------------
def human_bytes(n_str: Optional[str]) -> str:
    if n_str is None:
        return "unknown"
    if n_str.lower() == "max":
        return "unlimited"
    try:
        n = int(n_str)
    except Exception:
        return n_str
    # v1 "unlimited" often uses near-LLONG_MAX; treat very large as unlimited
    if n >= (1 << 60):  # ~1 EiB
        return "unlimited"
    units = ["B","KiB","MiB","GiB","TiB","PiB","EiB"]
    val = float(n)
    i = 0
    while val >= 1024 and i < len(units)-1:
        val /= 1024.0
        i += 1
    if val.is_integer():
        return f"{int(val)} {units[i]}"
    return f"{val:.2f} {units[i]}"

def cpu_ratio_to_str(r: Optional[float]) -> str:
    if r is None:
        return "unlimited"
    # r == number of CPUs allowed (e.g., 0.5 = half a CPU)
    if abs(r - round(r, 3)) < 1e-9:
        return f"{r:.3f} CPUs"
    return f"{r:.3f} CPUs"

def yesno(b: bool) -> str:
    return "yes" if b else "no"

# ---------------------- Detect cgroup version ----------------------
def detect_cgv(root: Path) -> str:
    for base in (root / "sys" / "fs" / "cgroup", root / "cgroup"):
        if exists(base / "cgroup.controllers"):
            return "v2"
        if any(exists(base / n) for n in ("cpu", "cpuacct", "memory", "pids")):
            return "v1"
    return "unknown"

# ---------------------- systemd manager defaults ----------------------
CONF_KEYS = {
    "DefaultCPUAccounting",
    "DefaultMemoryAccounting",
    "DefaultTasksAccounting",
    "DefaultTasksMax",
}

KEY_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]+)\s*=\s*(.*?)\s*$")

def parse_systemd_conf(path: Path) -> Dict[str, str]:
    found: Dict[str, str] = {}
    if not exists(path):
        return found
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = KEY_RE.match(line)
            if not m:
                continue
            k, v = m.group(1), m.group(2).strip()
            if k in CONF_KEYS:
                found[k] = v
    except Exception as e:
        dprint(f"parse_systemd_conf({path}): {e}")
    return found

def layered_systemd_defaults() -> Dict[str, Dict[str, Any]]:
    vendor = parse_systemd_conf(ROOT / "usr" / "lib" / "systemd" / "system.conf")
    local = parse_systemd_conf(ROOT / "etc" / "systemd" / "system.conf")
    dropins_dir = ROOT / "etc" / "systemd" / "system.conf.d"
    dropins: Dict[str, str] = {}
    if exists(dropins_dir):
        for p in sorted(dropins_dir.glob("*.conf")):
            dropins.update(parse_systemd_conf(p))

    keys = set(vendor) | set(local) | set(dropins) | CONF_KEYS
    out: Dict[str, Dict[str, Any]] = {}
    for k in keys:
        eff, src = vendor.get(k), "vendor"
        if k in local:
            eff, src = local[k], "etc"
        if k in dropins:
            eff, src = dropins[k], f"dropin:{src}"
        out[k] = {"vendor": vendor.get(k), "etc": local.get(k),
                  "dropin": dropins.get(k), "effective": eff, "source": src}
    return out

# ---------------------- cgroup v2 ----------------------
def parse_cpu_max(val: Optional[str]) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    if not val:
        return (None, None, None, "unknown")
    parts = val.split()
    if not parts:
        return (None, None, None, "unknown")
    if parts[0] == "max":
        period = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return (None, None, period, "max")
    try:
        quota = int(parts[0])
        period = int(parts[1]) if len(parts) > 1 else 100000
        if period <= 0:
            return (None, quota, period, "limit")
        return (quota/period, quota, period, "limit")
    except Exception:
        return (None, None, None, "unknown")

def read_v2_limits(dirpath: Path) -> Dict[str, Any]:
    mem = read_first_line(dirpath / "memory.max")
    pids = read_first_line(dirpath / "pids.max")
    cpu = read_first_line(dirpath / "cpu.max")
    ratio, quota, period, mode = parse_cpu_max(cpu)
    return {
        "memory.max": mem,
        "pids.max": pids,
        "cpu.max": cpu,
        "cpu.mode": mode,
        "cpu.ratio": ratio,
        "cpu.quota": quota,
        "cpu.period": period,
    }

def compare_v2(child: Dict[str, Any], parent: Dict[str, Any]) -> Dict[str, bool]:
    diffs: Dict[str, bool] = {}
    diffs["memory.max"] = (child.get("memory.max") != parent.get("memory.max"))
    diffs["pids.max"] = (child.get("pids.max") != parent.get("pids.max"))
    if child.get("cpu.mode") != parent.get("cpu.mode"):
        diffs["cpu.max"] = True
    elif child.get("cpu.mode") == "limit":
        cr, pr = child.get("cpu.ratio"), parent.get("cpu.ratio")
        diffs["cpu.max"] = (cr is None) != (pr is None) or (cr is not None and pr is not None and abs(cr - pr) > 1e-9)
    else:
        diffs["cpu.max"] = False
    return diffs

def walk_v2(root: Path) -> Dict[str, Dict[str, Any]]:
    cgroot = root / "sys" / "fs" / "cgroup"
    if not exists(cgroot):
        cgroot = root / "cgroup"
    results: Dict[str, Dict[str, Any]] = {}
    seeds = [cgroot / "system.slice", cgroot / "user.slice", cgroot / "machine.slice"]
    if not any(exists(s) for s in seeds):
        seeds = [cgroot]
    for ts in seeds:
        if not exists(ts):
            continue
        stack = [ts]
        while stack:
            cur = stack.pop()
            for sub in list_dirs(cur):
                parent_l = read_v2_limits(cur)
                limits = read_v2_limits(sub)
                diffs = compare_v2(limits, parent_l)
                name = sub.name
                etype = "unit" if (name.endswith(".service") or name.endswith(".scope")) else ("slice" if name.endswith(".slice") else "dir")
                rel = str(sub.relative_to(root))
                results[rel] = {"type": etype, "limits": limits, "diff": diffs, "parent": str(cur.relative_to(root))}
                stack.append(sub)
    return results

# ---------------------- cgroup v1 ----------------------
def read_v1_vals(cur: Path, files: List[str]) -> Dict[str, Optional[str]]:
    return {fn: read_first_line(cur / fn) for fn in files}

def v1_cpu_mode_ratio(vals: Dict[str, Optional[str]]) -> Tuple[str, Optional[float]]:
    try:
        q = int(vals.get("cpu.cfs_quota_us") or "-1")
    except Exception:
        q = -1
    try:
        p = int(vals.get("cpu.cfs_period_us") or "100000")
    except Exception:
        p = 100000
    if q == -1:
        return "max", None
    if q > 0 and p > 0:
        return "limit", q / p
    return "unknown", None

def walk_v1(root: Path) -> Dict[str, Dict[str, Any]]:
    """
    Walk v1 controllers (cpu, memory, pids) and compare each dir vs its parent.
    Return entries keyed by "<relative_path>::<controller>".
    """
    cgr = root / "sys" / "fs" / "cgroup"
    if not exists(cgr):
        cgr = root / "cgroup"
    controllers = {
        "cpu": ["cpu.cfs_quota_us", "cpu.cfs_period_us"],
        "memory": ["memory.limit_in_bytes"],
        "pids": ["pids.max"],
    }
    results: Dict[str, Dict[str, Any]] = {}
    for ctrl, files in controllers.items():
        base = cgr / ctrl
        if not exists(base):
            dprint(f"controller {ctrl} missing at {base}")
            continue
        seeds = [base / "system.slice"] if exists(base / "system.slice") else [base]
        for seed in seeds:
            stack = [seed]
            while stack:
                cur = stack.pop()
                vals = read_v1_vals(cur, files)
                parent = cur.parent if cur != base else None
                diffs: Dict[str, bool] = {}
                parent_vals = {}
                if parent and exists(parent):
                    parent_vals = read_v1_vals(parent, files)
                    for fn in files:
                        diffs[fn] = (vals.get(fn) != parent_vals.get(fn))
                    if ctrl == "cpu":
                        cm, cr = v1_cpu_mode_ratio(vals)
                        pm, pr = v1_cpu_mode_ratio(parent_vals)
                        diffs["cpu"] = (cm != pm) or (cm == "limit" and pr is not None and cr is not None and abs(cr - pr) > 1e-9)
                else:
                    for fn in files:
                        diffs[fn] = False
                    if ctrl == "cpu":
                        diffs["cpu"] = False

                key = f"{str(cur.relative_to(root))}::{ctrl}"
                cmode, cratio = v1_cpu_mode_ratio(vals) if ctrl == "cpu" else ("", None)
                results[key] = {
                    "type": f"v1:{ctrl}",
                    "limits": {
                        **vals,
                        **({"cpu.mode": cmode, "cpu.ratio": cratio} if ctrl == "cpu" else {})
                    },
                    "diff": diffs,
                    "parent": str(parent.relative_to(root)) if parent else None,
                }

                for sub in list_dirs(cur):
                    if sub.name in (".", ".."):
                        continue
                    stack.append(sub)
    return results

# ---------------------- Detail printers ----------------------
def print_v1_detailed_for_path(path_only: str, results: Dict[str, Dict[str, Any]]) -> None:
    # We expect keys like "<path>::cpu", "<path>::memory", "<path>::pids"
    print("\n== Detailed limits for:", path_only)
    controllers = ["cpu", "memory", "pids"]
    for ctrl in controllers:
        key = f"{path_only}::{ctrl}"
        if key not in results:
            continue
        info = results[key]
        parent = info.get("parent")
        limits = info["limits"]
        diffs = info["diff"]

        print(f"-- {ctrl} controller --")
        if ctrl == "cpu":
            q = limits.get("cpu.cfs_quota_us")
            p = limits.get("cpu.cfs_period_us")
            mode = limits.get("cpu.mode")
            ratio = limits.get("cpu.ratio")
            print(f"   child: quota={q}, period={p}, mode={mode}, effective={cpu_ratio_to_str(ratio)}")
            if parent:
                pkey = f"{parent}::{ctrl}"
                plim = results.get(pkey, {}).get("limits", {})
                pmode = plim.get("cpu.mode")
                pratio = plim.get("cpu.ratio")
                pq = plim.get("cpu.cfs_quota_us")
                pp = plim.get("cpu.cfs_period_us")
                print(f"   parent: {parent}")
                print(f"           quota={pq}, period={pp}, mode={pmode}, effective={cpu_ratio_to_str(pratio)}")
            print(f"   changed: {yesno(diffs.get('cpu', False) or diffs.get('cpu.cfs_quota_us', False))}")
        elif ctrl == "memory":
            val = limits.get("memory.limit_in_bytes")
            print(f"   child:  limit={val} ({human_bytes(val)})")
            if parent:
                plim = results.get(f"{parent}::{ctrl}", {}).get("limits", {})
                pv = plim.get("memory.limit_in_bytes")
                print(f"   parent: {parent}")
                print(f"           limit={pv} ({human_bytes(pv)})")
            print(f"   changed: {yesno(diffs.get('memory.limit_in_bytes', False))}")
        elif ctrl == "pids":
            val = limits.get("pids.max")
            pretty = "unlimited" if (val is None or val.lower() == "max") else val
            print(f"   child:  pids.max={val} ({pretty})")
            if parent:
                plim = results.get(f"{parent}::{ctrl}", {}).get("limits", {})
                pv = plim.get("pids.max")
                ppretty = "unlimited" if (pv is None or str(pv).lower() == "max") else pv
                print(f"   parent: {parent}")
                print(f"           pids.max={pv} ({ppretty})")
            print(f"   changed: {yesno(diffs.get('pids.max', False))}")

def print_v2_detailed_for_path(path: str, results: Dict[str, Dict[str, Any]]) -> None:
    info = results.get(path)
    if not info:
        return
    parent = info.get("parent")
    l = info["limits"]
    print("\n== Detailed limits for:", path)
    # memory
    cm = l.get("memory.max"); print(f"-- memory --")
    print(f"   child:  memory.max={cm} ({human_bytes(cm)})")
    if parent:
        pl = results.get(parent, {}).get("limits", {})
        pm = pl.get("memory.max"); print(f"   parent: {parent}")
        print(f"           memory.max={pm} ({human_bytes(pm)})")
    print(f"   changed: {yesno(info['diff'].get('memory.max', False))}")
    # pids
    cp = l.get("pids.max"); print(f"-- pids --")
    ppretty = "unlimited" if (cp is None or str(cp).lower() == "max") else cp
    print(f"   child:  pids.max={cp} ({ppretty})")
    if parent:
        pl = results.get(parent, {}).get("limits", {})
        pp = pl.get("pids.max")
        pppretty = "unlimited" if (pp is None or str(pp).lower() == "max") else pp
        print(f"   parent: {parent}")
        print(f"           pids.max={pp} ({ppprety if 'ppprety' in locals() else pppretty})")
    print(f"   changed: {yesno(info['diff'].get('pids.max', False))}")
    # cpu
    print(f"-- cpu --")
    print(f"   child:  cpu.max={l.get('cpu.max')} (effective {cpu_ratio_to_str(l.get('cpu.ratio'))})")
    if parent:
        pl = results.get(parent, {}).get("limits", {})
        print(f"   parent: {parent}")
        print(f"           cpu.max={pl.get('cpu.max')} (effective {cpu_ratio_to_str(pl.get('cpu.ratio'))})")
    print(f"   changed: {yesno(info['diff'].get('cpu.max', False))}")

# ---------------------- main ----------------------
def main():
    global VERBOSE, DEBUG, UNIT_FILTER
    ap = argparse.ArgumentParser(description="Check cgroup changes from defaults in sosreport")
    ap.add_argument("-v", action="store_true", help="Verbose")
    ap.add_argument("-d", action="store_true", help="Debug")
    ap.add_argument("-u", metavar="UNIT", help="Filter by substring in unit/slice path")
    args = ap.parse_args()
    VERBOSE, DEBUG, UNIT_FILTER = args.v, args.d, args.u

    print("== chk_cg: cgroup change detector (sosreport mode) ==")
    cgv = detect_cgv(ROOT)
    print(f"Detected cgroup version: {cgv}\n")

    # Systemd manager defaults
    print("-- systemd manager defaults (effective vs vendor) --")
    mgr = layered_systemd_defaults()
    overrides = []
    for k in sorted(CONF_KEYS):
        v = mgr.get(k, {})
        if v.get("effective") is not None and v.get("effective") != v.get("vendor"):
            overrides.append((k, v))
    if overrides:
        for k, v in overrides:
            src = v.get("source", "?")
            print(f"* {k}: effective={v['effective']}  [vendor={v['vendor']}]  via {src}")
    else:
        print("No manager default overrides detected (or not present in snapshot).")
    print()

    specific = looks_like_specific_unit(UNIT_FILTER)

    # Cgroup analysis
    if cgv == "v2":
        results = walk_v2(ROOT)
        if not results:
            print("No cgroup v2 data found.")
            return 0
        deviants = []
        for path, info in results.items():
            if info["type"] in ("unit", "slice", "dir") and any(info["diff"].values()):
                if match_unit(path):
                    deviants.append((path, info))
        print("-- cgroup v2 deviations from parent --")
        if not deviants:
            msg = "None found"
            if UNIT_FILTER:
                msg += f" (after filter: {UNIT_FILTER})"
            print(msg + ".")
        else:
            for path, info in sorted(deviants):
                tags = [k for k, v in info["diff"].items() if v]
                print(f"* {path} [{info['type']}] changed: {', '.join(tags)}")
                if VERBOSE and not specific:
                    parent_limits = {}
                    if info.get("parent"):
                        parent_limits = results.get(info["parent"], {}).get("limits", {})
                    l = info["limits"]
                    if "memory.max" in tags:
                        print(f"    memory.max: {l.get('memory.max')} (parent {parent_limits.get('memory.max')})")
                    if "pids.max" in tags:
                        print(f"    pids.max:   {l.get('pids.max')} (parent {parent_limits.get('pids.max')})")
                    if "cpu.max" in tags:
                        print(f"    cpu.max:    {l.get('cpu.max')} (parent {parent_limits.get('cpu.max')}); ratios child={l.get('cpu.ratio')} parent={parent_limits.get('cpu.ratio')}")
        # Detailed section if user targeted a specific unit/slice
        if specific:
            # choose first exact matching path(s)
            targets = [p for p in results.keys() if UNIT_FILTER in p]
            for t in sorted(targets):
                print_v2_detailed_for_path(t, results)
        return 0

    if cgv == "v1":
        results = walk_v1(ROOT)
        if not results:
            print("No cgroup v1 controller data found.")
            return 0
        print("-- cgroup v1 deviations (per controller vs parent) --")
        deviants = []
        for key, info in results.items():
            path_only = key.split("::", 1)[0]
            if any(info.get("diff", {}).values()) and match_unit(path_only):
                deviants.append((key, info))
        if not deviants:
            msg = "None found"
            if UNIT_FILTER:
                msg += f" (after filter: {UNIT_FILTER})"
            print(msg + ".")
        else:
            for key, info in sorted(deviants):
                ctrl = info["type"].split(":", 1)[-1]
                tags = [k for k, v in info.get("diff", {}).items() if v]
                print(f"* {key} changed: {', '.join(tags)}")
                if VERBOSE and not looks_like_specific_unit(UNIT_FILTER):
                    print(f"    limits: {info['limits']} (parent: {info.get('parent')})")
        # Detailed section if user targeted a specific unit/slice
        if specific:
            # group keys by path
            paths = sorted({k.split("::",1)[0] for k in results if UNIT_FILTER in k})
            for path_only in paths:
                print_v1_detailed_for_path(path_only, results)
        return 0

    print("-- skipping cgroup scan: unknown cgroup version --")
    return 0

if __name__ == "__main__":
    sys.exit(main())
