#!/usr/bin/env python3
"""
chk_cg.py - Check for cgroup deviations from defaults in a sosreport snapshot.

Usage:
  ./chk_cg.py [-v] [-d]

- Works offline against the CURRENT DIRECTORY (sosreport root).
- Detects cgroup v2 (unified) and v1 hierarchies.
- Compares unit/slice limits to their parent slice to flag overrides.
- Also reports systemd manager default overrides from /etc/systemd/*.conf vs /usr/lib/systemd/*.conf

Flags:
  -v  Verbose (show more per-unit details)
  -d  Debug (show paths searched and fallbacks)
"""
import argparse
import os
import sys
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# ---------------------- Utility logging ----------------------
VERBOSE = False
DEBUG = False

def vprint(*a, **k):
    if VERBOSE:
        print(*a, **k)

def dprint(*a, **k):
    if DEBUG:
        print("[DBG]", *a, **k)

# ---------------------- Filesystem helpers ----------------------
ROOT = Path(".").resolve()

def read_first_line(p: Path) -> Optional[str]:
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            line = f.readline().strip()
            dprint(f"read {p}: {line!r}")
            return line
    except Exception as e:
        dprint(f"failed to read {p}: {e}")
        return None

def list_dirs(p: Path) -> List[Path]:
    try:
        return [x for x in p.iterdir() if x.is_dir()]
    except Exception as e:
        dprint(f"failed to list {p}: {e}")
        return []

def exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False

# ---------------------- Detect cgroup version ----------------------
def detect_cgv(root: Path) -> str:
    # Try unified mount snapshot path from sosreport
    cgr = root / "sys" / "fs" / "cgroup"
    if exists(cgr / "cgroup.controllers"):
        return "v2"
    # Detect v1 by controller subdirs typical of sosreport
    likely_v1 = ["cpu", "cpuacct", "memory", "pids"]
    if any(exists(cgr / n) for n in likely_v1):
        return "v1"
    # sosreport variants: sometimes under 'cgroup' not /sys/fs/cgroup (older)
    alt = root / "cgroup"
    if exists(alt / "cgroup.controllers"):
        return "v2"
    if any(exists(alt / n) for n in likely_v1):
        return "v1"
    return "unknown"

# ---------------------- Systemd defaults parsing ----------------------
CONF_KEYS = {
    "DefaultCPUAccounting",
    "DefaultMemoryAccounting",
    "DefaultTasksAccounting",
    "DefaultTasksMax",
    "DefaultMemoryPressure",
    "DefaultLimitNOFILE",
    "DefaultLimitNPROC",
}

INI_SECTION_RE = re.compile(r"^\s*\[(.+?)\]\s*$")
KEY_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9]+)\s*=\s*(.*?)\s*$")

def parse_systemd_conf(path: Path) -> Dict[str, str]:
    """
    Parse a systemd-style INI and return keys of interest (CONF_KEYS).
    Only top-level keys matter for system.conf.
    """
    found: Dict[str, str] = {}
    if not exists(path):
        return found
    current_section = None
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            msec = INI_SECTION_RE.match(line)
            if msec:
                current_section = msec.group(1).strip()
                continue
            mkey = KEY_RE.match(line)
            if mkey:
                key, val = mkey.group(1), mkey.group(2).strip()
                if key in CONF_KEYS:
                    found[key] = val
    except Exception as e:
        dprint(f"parse_systemd_conf error for {path}: {e}")
    return found

def layered_systemd_defaults() -> Dict[str, Dict[str, Any]]:
    """
    Compare manager defaults: /usr/lib/systemd/system.conf (vendor)
    overlaid by /etc/systemd/system.conf and drop-ins.
    Returns dict with 'vendor', 'etc', 'effective' values.
    """
    out: Dict[str, Dict[str, Any]] = {}
    vendor = parse_systemd_conf(ROOT / "usr" / "lib" / "systemd" / "system.conf")
    local = parse_systemd_conf(ROOT / "etc" / "systemd" / "system.conf")
    # drop-ins
    dropins_dir = ROOT / "etc" / "systemd" / "system.conf.d"
    dropins: Dict[str, str] = {}
    if exists(dropins_dir):
        for p in sorted(dropins_dir.glob("*.conf")):
            for k, v in parse_systemd_conf(p).items():
                dropins[k] = v  # later files override earlier (lexicographic order)

    # build effective by overlay (vendor -> local -> dropins)
    keys = set(vendor.keys()) | set(local.keys()) | set(dropins.keys()) | CONF_KEYS
    for k in keys:
        eff = vendor.get(k)
        src = "vendor"
        if k in local:
            eff = local[k]
            src = "etc"
        if k in dropins:
            eff = dropins[k]
            src = f"dropin:{src}"
        out[k] = {"vendor": vendor.get(k), "etc": local.get(k), "dropin": dropins.get(k), "effective": eff, "source": src}
    return out

# ---------------------- Cgroup v2 analysis ----------------------
def parse_cpu_max(val: Optional[str]) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """
    Return (ratio, quota, period, mode) where mode is 'max' or 'limit' or 'unknown'.
    ratio = quota/period if limited; None if unlimited/unknown.
    """
    if not val:
        return (None, None, None, "unknown")
    parts = val.strip().split()
    if not parts:
        return (None, None, None, "unknown")
    if parts[0] == "max":
        # Some kernels show 'max' or 'max <period>'
        period = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return (None, None, period, "max")
    try:
        quota = int(parts[0])
        period = int(parts[1]) if len(parts) > 1 else 100000
        if period == 0:
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
    """
    Compare child to parent settings to decide if child deviates.
    For memory.max and pids.max: any numeric vs parent's different string means deviation.
    For cpu.max: compare modes/ratios.
    """
    diffs = {}
    # memory
    cm, pm = (child.get("memory.max") or ""), (parent.get("memory.max") or "")
    if cm != pm:
        diffs["memory.max"] = True
    else:
        diffs["memory.max"] = False
    # pids
    cp, pp = (child.get("pids.max") or ""), (parent.get("pids.max") or "")
    if cp != pp:
        diffs["pids.max"] = True
    else:
        diffs["pids.max"] = False
    # cpu
    cmode, pmode = child.get("cpu.mode"), parent.get("cpu.mode")
    cr, pr = child.get("cpu.ratio"), parent.get("cpu.ratio")
    if cmode != pmode:
        diffs["cpu.max"] = True
    else:
        if cmode == "limit":
            if cr is None or pr is None:
                diffs["cpu.max"] = True
            else:
                diffs["cpu.max"] = abs(cr - pr) > 1e-9
        else:
            diffs["cpu.max"] = False
    return diffs

def walk_v2(root: Path) -> Dict[str, Dict[str, Any]]:
    """
    Walk common slices and collect units. Returns mapping path->info
    """
    cgroot = None
    for p in [root / "sys" / "fs" / "cgroup", root / "cgroup"]:
        if exists(p):
            cgroot = p
            break
    if not cgroot:
        return {}
    results: Dict[str, Dict[str, Any]] = {}
    # Consider top-level slices
    top_slices = [cgroot / "system.slice", cgroot / "user.slice", cgroot / "machine.slice"]
    for ts in top_slices:
        if not exists(ts):
            continue
        parent_limits = read_v2_limits(ts)  # compare children against their slice
        results[str(ts.relative_to(root))] = {"type": "slice", "limits": parent_limits, "diff": {"memory.max": False, "pids.max": False, "cpu.max": False}, "parent": str(ts.parent.relative_to(root)) if ts.parent != ts else None}
        # BFS within slice
        stack = [ts]
        while stack:
            cur = stack.pop()
            for sub in list_dirs(cur):
                name = sub.name
                if name in (".", ".."):
                    continue
                limits = read_v2_limits(sub)
                parent_l = read_v2_limits(cur)
                diffs = compare_v2(limits, parent_l)
                entry_type = "unit" if (name.endswith(".service") or name.endswith(".scope")) else "slice" if name.endswith(".slice") else "dir"
                results[str(sub.relative_to(root))] = {"type": entry_type, "limits": limits, "diff": diffs, "parent": str(cur.relative_to(root))}
                if entry_type in ("slice", "dir"):
                    stack.append(sub)
    return results

# ---------------------- Cgroup v1 analysis ----------------------
def read_v1_value(base: Path, subpath: Path) -> Optional[str]:
    p = base / subpath
    return read_first_line(p)

def walk_v1(root: Path) -> Dict[str, Dict[str, Any]]:
    """
    Minimal v1: check cpu, memory, pids controller values. Compare child to parent.
    """
    cgr = None
    for p in [root / "sys" / "fs" / "cgroup", root / "cgroup"]:
        if exists(p):
            cgr = p
            break
    if not cgr:
        return {}

    controllers = {
        "cpu": ("cpu.cfs_quota_us", "cpu.cfs_period_us"),
        "memory": ("memory.limit_in_bytes",),
        "pids": ("pids.max",),
    }
    results: Dict[str, Dict[str, Any]] = {}
    for ctrl, files in controllers.items():
        base = cgr / ctrl
        if not exists(base):
            continue
        rootdir = base / "system.slice" if exists(base / "system.slice") else base
        stack = [rootdir]
        while stack:
            cur = stack.pop()
            vals = {fn: read_first_line(cur / fn) for fn in files}
            extra: Dict[str, Any] = {}
            if ctrl == "cpu":
                try:
                    q = int(vals.get("cpu.cfs_quota_us") or "-1")
                except Exception:
                    q = -1
                try:
                    pper = int(vals.get("cpu.cfs_period_us") or "100000")
                except Exception:
                    pper = 100000
                if q > 0 and pper > 0:
                    extra["cpu.ratio"] = q / pper
                    extra["cpu.mode"] = "limit"
                elif q == -1:
                    extra["cpu.ratio"] = None
                    extra["cpu.mode"] = "max"
                else:
                    extra["cpu.ratio"] = None
                    extra["cpu.mode"] = "unknown"
            entry = {"type": f"v1:{ctrl}", "limits": {**vals, **extra}}
            parent = cur.parent if cur != base else None
            entry["parent"] = str(parent.relative_to(root)) if parent else None
            diffs: Dict[str, bool] = {}
            if parent and parent != base and parent.exists():
                pvals = {fn: read_first_line(parent / fn) for fn in files}
                for fn in files:
                    diffs[fn] = (vals.get(fn) != pvals.get(fn))
                if ctrl == "cpu":
                    try:
                        pq = int(pvals.get("cpu.cfs_quota_us") or "-1"); pp = int(pvals.get("cpu.cfs_period_us") or "100000")
                    except Exception:
                        pq, pp = -1, 100000
                    pratio = (pq/pp) if (pq > 0 and pp > 0) else None
                    pmode = "limit" if (pq > 0 and pp > 0) else ("max" if pq == -1 else "unknown")
                    diffs["cpu"] = (extra.get("cpu.mode") != pmode) or (extra.get("cpu.mode") == "limit" and pratio is not None and abs(extra.get("cpu.ratio",0) - pratio) > 1e-9)
            else:
                for fn in files:
                    diffs[fn] = False
                if ctrl == "cpu":
                    diffs["cpu"] = False
            entry["diff"] = diffs
            results[str(cur.relative_to(root)) + f"::{ctrl}"] = entry

            for sub in list_dirs(cur):
                if sub.name in (".", ".."):
                    continue
                stack.append(sub)
    return results

# ---------------------- Presentation ----------------------
def format_limit_pair(child: Any, parent: Any) -> str:
    return f"{child} (parent {parent})"

def main():
    global VERBOSE, DEBUG
    ap = argparse.ArgumentParser(description="Check cgroup changes from defaults (sosreport).")
    ap.add_argument("-v", action="store_true", help="Verbose output")
    ap.add_argument("-d", action="store_true", help="Debug output")
    args = ap.parse_args()
    VERBOSE = args.v
    DEBUG = args.d

    print("== chk_cg: cgroup change detector (sosreport mode) ==")
    cgv = detect_cgv(ROOT)
    print(f"Detected cgroup version: {cgv}")
    if cgv == "unknown":
        print("Could not find cgroup data under ./sys/fs/cgroup or ./cgroup")
    print()

    # Systemd manager defaults
    print("-- systemd manager defaults (effective vs vendor) --")
    mgr = layered_systemd_defaults()
    any_overrides = False
    for k in sorted(CONF_KEYS):
        info = mgr.get(k, {"vendor": None, "effective": None, "etc": None, "dropin": None})
        if info["vendor"] != info["effective"] and (info["effective"] is not None):
            any_overrides = True
            print(f"{k}: effective={info['effective']}  [vendor={info['vendor']}]  (overridden via {('drop-in' if info.get('dropin') else 'etc')})")
        elif VERBOSE:
            print(f"{k}: effective={info['effective']}  [vendor={info['vendor']}]")
        elif info["effective"] is None and DEBUG:
            print(f"{k}: not found in snapshot")
    if not any_overrides:
        print("No manager default overrides detected (or unavailable in snapshot).")
    print()

    # Cgroup analysis
    if cgv == "v2":
        results = walk_v2(ROOT)
        if not results:
            print("No cgroup v2 directories found to analyze.")
            return 0
        deviants = []
        for path, info in results.items():
            if info["type"] in ("unit", "slice"):
                diffs = info["diff"]
                if diffs.get("memory.max") or diffs.get("pids.max") or diffs.get("cpu.max"):
                    deviants.append((path, info))
        print("-- cgroup v2 deviations from parent slice --")
        if not deviants:
            print("No per-unit or slice deviations detected (vs parent slice).")
        else:
            for path, info in sorted(deviants):
                parent_path = info["parent"]
                pl = results.get(parent_path, {}).get("limits", {}) if parent_path else {}
                l = info["limits"]
                tags = [k for k, v in info["diff"].items() if v]
                print(f"* {path} [{info['type']}] changed: {', '.join(tags)}")
                if VERBOSE:
                    if "memory.max" in tags:
                        print(f"    memory.max: {format_limit_pair(l.get('memory.max'), pl.get('memory.max'))}")
                    if "pids.max" in tags:
                        print(f"    pids.max:   {format_limit_pair(l.get('pids.max'), pl.get('pids.max'))}")
                    if "cpu.max" in tags:
                        cchild = l.get('cpu.max'); cparent = pl.get('cpu.max')
                        print(f"    cpu.max:    {format_limit_pair(cchild, cparent)}  (ratio child={l.get('cpu.ratio')} parent={pl.get('cpu.ratio')})")
        if VERBOSE:
            print()
            print("-- sample of units scanned --")
            cnt = 0
            for path, info in sorted(results.items()):
                if info["type"] == "unit":
                    print(f"  {path}: mem={info['limits'].get('memory.max')} pids={info['limits'].get('pids.max')} cpu={info['limits'].get('cpu.max')}")
                    cnt += 1
                    if cnt >= 20:
                        break
    elif cgv == "v1":
        results = walk_v1(ROOT)
        if not results:
            print("No cgroup v1 controller data found to analyze.")
            return 0
        print("-- cgroup v1 deviations (per controller) --")
        for path, info in sorted(results.items()):
            diffs = info.get("diff", {})
            if any(diffs.values()):
                print(f"* {path} changed: {', '.join([k for k, v in diffs.items() if v])}")
                if VERBOSE:
                    print(f"    limits: {info['limits']} (parent: {info['parent']})")
        if VERBOSE:
            print()
            shown = 0
            for path, info in sorted(results.items()):
                print(f"  {path}: {info['limits']}")
                shown += 1
                if shown >= 20:
                    break
    else:
        print("-- skipping cgroup scan due to unknown version --")

    return 0

if __name__ == "__main__":
    sys.exit(main())
