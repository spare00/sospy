#!/usr/bin/env python3
"""
Summarize /proc/zoneinfo: per-NUMA-node zone memory state, watermarks, and LRU mix.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DEFAULT_PAGE_SIZE_BYTES = 4096
UNIT_LABELS = {"P": "pages", "K": "KiB", "M": "MiB", "G": "GiB"}

NODE_ZONE_RE = re.compile(r"^Node\s+(\d+),\s*zone\s+(\S+)")
NODE_PERNODE_RE = re.compile(r"^Node\s+(\d+),\s*per-node stats")
PAGESET_CPU_RE = re.compile(r"^cpu:\s*(\d+)\s*$", re.IGNORECASE)
PAGESET_KV_RE = re.compile(r"^([A-Za-z0-9_ ]+):\s*(-?\d+)\s*$")


def scale_value(kb: float, unit: str) -> float:
    if unit == "K":
        return kb
    if unit == "M":
        return kb / 1024
    if unit == "G":
        return kb / (1024 * 1024)
    raise ValueError(f"unknown unit {unit!r}; use P, K, M, or G")


def format_scaled_size(kb: float, unit: str) -> str:
    """Format a KiB value in the selected display unit."""
    val = scale_value(kb, unit)
    if unit == "K":
        return f"{val:,.0f}" if abs(val) >= 100 else f"{val:.2f}"
    if val == 0:
        return "0.00"
    if unit == "M":
        return f"{val:.2f}" if abs(val) >= 0.01 else f"{val:.4f}"
    return f"{val:.2f}" if abs(val) >= 0.01 else f"{val:.4f}"


def pages_to_kib(pages: int, pagesize_bytes: int) -> float:
    return pages * pagesize_bytes / 1024


def pages_to_scaled_str(pages: int, pagesize_bytes: int, unit: str) -> str:
    return format_scaled_size(pages_to_kib(pages, pagesize_bytes), unit)


def pages_to_display(pages: Optional[int], pagesize_bytes: int, unit: str) -> str:
    if pages is None:
        return "-"
    if unit == "P":
        return f"{pages:,}"
    return pages_to_scaled_str(pages, pagesize_bytes, unit)


def kib_to_display(kb: float, pagesize_bytes: int, unit: str) -> str:
    if unit == "P":
        pages = kb * 1024 / pagesize_bytes
        return f"{pages:,.0f}" if pages.is_integer() else f"{pages:,.2f}"
    return format_scaled_size(kb, unit)


def parse_kv_line(line: str) -> Optional[Tuple[str, int]]:
    s = line.strip()
    if not s or s.startswith("protection:"):
        return None
    parts = s.split()
    if len(parts) < 2:
        return None
    try:
        val = int(parts[-1])
    except ValueError:
        return None
    key = " ".join(parts[:-1]).rstrip(":")
    if key == "pages free":
        key = "nr_free_pages"
    return key, val


def _parse_pageset_cpu(stripped: str) -> Optional[int]:
    m = PAGESET_CPU_RE.match(stripped)
    if m:
        return int(m.group(1))
    return None


def _parse_pageset_kv(stripped: str) -> Optional[Tuple[str, int]]:
    m = PAGESET_KV_RE.match(stripped)
    if m is None:
        return None
    return m.group(1).strip().replace(" ", "_").lower(), int(m.group(2))


@dataclass
class ZoneRecord:
    node: int
    zone: str
    stats: Dict[str, int] = field(default_factory=dict)
    node_stats: Dict[str, int] = field(default_factory=dict)
    pcp_sets: List[Dict[str, int]] = field(default_factory=list)


def parse_zoneinfo(path: str) -> List[ZoneRecord]:
    zones: List[ZoneRecord] = []
    current: Optional[ZoneRecord] = None
    in_pagesets = False
    in_per_node_stats = False
    current_pcp: Optional[Dict[str, int]] = None

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\n")
                m_zone = NODE_ZONE_RE.match(line)
                if m_zone:
                    if current is not None:
                        zones.append(current)
                    current = ZoneRecord(int(m_zone.group(1)), m_zone.group(2))
                    in_pagesets = False
                    in_per_node_stats = False
                    current_pcp = None
                    continue

                if NODE_PERNODE_RE.match(line):
                    if current is not None:
                        zones.append(current)
                        current = None
                    in_pagesets = False
                    in_per_node_stats = False
                    current_pcp = None
                    continue

                if current is None:
                    continue

                stripped = line.strip()
                if stripped == "per-node stats":
                    in_per_node_stats = True
                    continue
                if in_per_node_stats:
                    if stripped.startswith("pages free"):
                        in_per_node_stats = False
                    else:
                        kv = parse_kv_line(line)
                        if kv is not None:
                            key, val = kv
                            current.node_stats[key] = val
                        continue

                if stripped == "pagesets":
                    in_pagesets = True
                    current_pcp = None
                    continue
                if in_pagesets:
                    if NODE_ZONE_RE.match(line) or NODE_PERNODE_RE.match(line):
                        in_pagesets = False
                        # Re-dispatch node header (handled at top of loop on next iter only).
                        m_zone = NODE_ZONE_RE.match(line)
                        if m_zone:
                            if current is not None:
                                zones.append(current)
                            current = ZoneRecord(int(m_zone.group(1)), m_zone.group(2))
                        elif NODE_PERNODE_RE.match(line):
                            if current is not None:
                                zones.append(current)
                            current = None
                        in_per_node_stats = False
                        current_pcp = None
                        continue

                    cpu_n = _parse_pageset_cpu(stripped)
                    if cpu_n is not None:
                        current_pcp = {"cpu": cpu_n}
                        current.pcp_sets.append(current_pcp)
                        continue

                    pcp_kv = _parse_pageset_kv(stripped)
                    if pcp_kv is not None:
                        key, val = pcp_kv
                        if current_pcp is None:
                            current_pcp = {}
                            current.pcp_sets.append(current_pcp)
                        current_pcp[key] = val
                        if key == "count":
                            current.stats["pcp_pages"] = (
                                current.stats.get("pcp_pages", 0) + val
                            )
                        continue

                    kv_peek = parse_kv_line(line)
                    if kv_peek is None:
                        continue
                    in_pagesets = False
                    current_pcp = None

                kv = parse_kv_line(line)
                if kv is None:
                    continue
                key, val = kv
                current.stats[key] = val
    except FileNotFoundError:
        return []

    if current is not None:
        zones.append(current)
    return zones


def stat(z: ZoneRecord, *keys: str) -> Optional[int]:
    for k in keys:
        if k in z.stats:
            return z.stats[k]
    return None


def node_stat(z: ZoneRecord, *keys: str) -> Optional[int]:
    for k in keys:
        if k in z.node_stats:
            return z.node_stats[k]
    return None


def zone_anon_pages(z: ZoneRecord) -> int:
    if stat(z, "nr_zone_active_anon") is not None or stat(z, "nr_zone_inactive_anon") is not None:
        return (stat(z, "nr_zone_active_anon") or 0) + (stat(z, "nr_zone_inactive_anon") or 0)
    return stat(z, "nr_anon_pages") or 0


def zone_file_pages(z: ZoneRecord) -> int:
    if stat(z, "nr_zone_active_file") is not None or stat(z, "nr_zone_inactive_file") is not None:
        return (stat(z, "nr_zone_active_file") or 0) + (stat(z, "nr_zone_inactive_file") or 0)
    return stat(z, "nr_file_pages") or 0


def zone_unevictable_pages(z: ZoneRecord) -> int:
    return stat(z, "nr_zone_unevictable", "nr_unevictable") or 0


def zone_dirty_pages(z: ZoneRecord) -> int:
    return stat(z, "nr_zone_write_pending", "nr_dirty") or 0


def zone_slab_pages(z: ZoneRecord) -> int:
    r = stat(z, "nr_slab_reclaimable") or 0
    u = stat(z, "nr_slab_unreclaimable") or 0
    return r + u


def node_stats_by_node(zones: List[ZoneRecord]) -> Dict[int, Dict[str, int]]:
    """Per-node stats are printed once inside the first populated zone block."""
    out: Dict[int, Dict[str, int]] = {}
    for z in zones:
        if z.node_stats and z.node not in out:
            out[z.node] = z.node_stats
    return out


def zone_capacity_pages(z: ZoneRecord) -> Optional[int]:
    return stat(z, "managed", "present", "spanned")


def zone_buddy_free_pages(z: ZoneRecord) -> Optional[int]:
    return stat(z, "nr_free_pages")


def zone_pcp_pages(z: ZoneRecord) -> int:
    return stat(z, "pcp_pages") or 0


def zone_total_free_pages(z: ZoneRecord) -> Optional[int]:
    """Buddy free lists plus per-CPU pageset (PCP) caches."""
    buddy = zone_buddy_free_pages(z)
    if buddy is None:
        return None
    return buddy + zone_pcp_pages(z)


def zone_used_pages(z: ZoneRecord) -> Optional[int]:
    cap = zone_capacity_pages(z)
    free = zone_total_free_pages(z)
    if cap is None or free is None:
        return None
    return max(cap - free, 0)


def watermark_severity(free_pages: Optional[int], z: ZoneRecord) -> str:
    cap = zone_capacity_pages(z)
    if cap is not None and cap == 0:
        return "none"
    mn = stat(z, "min")
    low = stat(z, "low")
    if free_pages is None or mn is None:
        return "none"
    if free_pages <= mn:
        return "min"
    if low is not None and free_pages <= low:
        return "low"
    high = stat(z, "high")
    if high is not None and free_pages <= high:
        return "below-high"
    return "ok"


def watermark_note(
    free_pages: Optional[int],
    z: ZoneRecord,
    pagesize_bytes: int,
    unit: str,
    unit_label: str,
) -> str:
    sev = watermark_severity(free_pages, z)
    mn = stat(z, "min")
    low = stat(z, "low")
    high = stat(z, "high")
    if sev == "min" and free_pages is not None and mn is not None:
        return (
            f"free <= min ({pages_to_display(free_pages, pagesize_bytes, unit)} "
            f"<= {pages_to_display(mn, pagesize_bytes, unit)} {unit_label})"
        )
    if sev == "low" and free_pages is not None and low is not None:
        return (
            f"free <= low ({pages_to_display(free_pages, pagesize_bytes, unit)} "
            f"<= {pages_to_display(low, pagesize_bytes, unit)} {unit_label})"
        )
    if sev == "below-high" and free_pages is not None and high is not None:
        return (
            f"free <= high ({pages_to_display(free_pages, pagesize_bytes, unit)} "
            f"<= {pages_to_display(high, pagesize_bytes, unit)} {unit_label})"
        )
    if sev == "ok":
        return "above high"
    return ""


def color_line(line: str, severity: str, use_color: bool) -> str:
    if not use_color or severity in ("none", "ok"):
        return line
    reset = "\033[0m"
    if severity == "min":
        return f"\033[1m\033[91m{line}{reset}"
    if severity == "low":
        return f"\033[1m\033[93m{line}{reset}"
    if severity == "below-high":
        return f"\033[93m{line}{reset}"
    return line


def meminfo_path_for_zoneinfo(zoneinfo_path: str) -> Optional[str]:
    d = os.path.dirname(os.path.abspath(zoneinfo_path))
    for cand in (
        os.path.join(d, "meminfo"),
        os.path.join(d, "proc", "meminfo"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def parse_meminfo(path: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    out[parts[0].rstrip(":")] = int(parts[1])
    except FileNotFoundError:
        pass
    return out


def display_path_near_zoneinfo(path: str, zoneinfo_path: str) -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(zoneinfo_path)))
    try:
        rel = os.path.relpath(os.path.abspath(path), base)
    except ValueError:
        return path
    if rel.startswith(".."):
        return path
    return f".{os.sep}{rel}"


def resolve_zoneinfo_path(path: str) -> str:
    if os.path.isfile(path):
        return path
    return os.path.join(path, "proc", "zoneinfo")


def print_pcp_details(
    zones: List[ZoneRecord],
    unit: str,
    unit_label: str,
    pagesize_bytes: int,
    node_col: int,
    zone_w: int,
) -> None:
    pcp_zones = [z for z in zones if z.pcp_sets]
    print(f"\nPCP cache summary ({unit_label}; summed across CPUs per zone):")
    if not pcp_zones:
        print("  No pageset details found.")
        return

    mem_keys = ("count", "high", "batch", "high_min", "high_max")
    summaries: List[Tuple[ZoneRecord, int, Dict[str, Optional[int]], str]] = []
    for z in pcp_zones:
        sums: Dict[str, Optional[int]] = {}
        for key in mem_keys:
            vals = [pcp[key] for pcp in z.pcp_sets if key in pcp]
            sums[key] = sum(vals) if vals else None

        vmstats = sorted(
            {pcp["vm_stats_threshold"] for pcp in z.pcp_sets if "vm_stats_threshold" in pcp}
        )
        if not vmstats:
            vmstat_disp = "-"
        elif len(vmstats) == 1:
            vmstat_disp = f"{vmstats[0]:,}"
        else:
            vmstat_disp = f"{vmstats[0]:,}..{vmstats[-1]:,}"
        summaries.append((z, len(z.pcp_sets), sums, vmstat_disp))

    mem_w = max(
        10,
        *(
            len(pages_to_display(sums.get(key), pagesize_bytes, unit))
            for _, _, sums, _ in summaries
            for key in mem_keys
        ),
    )
    cpu_w = max(5, *(len(str(cpu_count)) for _, cpu_count, _, _ in summaries))
    vmstat_w = max(8, *(len(vmstat) for _, _, _, vmstat in summaries))
    hdr = (
        f"{'':>{node_col}} {'zone':<{zone_w}}"
        f"{'cpus':>{cpu_w}}"
        f"{'count':>{mem_w}}{'high':>{mem_w}}{'batch':>{mem_w}}"
        f"{'high_min':>{mem_w}}{'high_max':>{mem_w}}"
        f"{'vmstat':>{vmstat_w}}"
    )
    print(hdr)
    print("-" * len(hdr))
    for z, cpu_count, sums, vmstat_disp in summaries:
        print(
            f"{('N' + str(z.node)):>{node_col}} {z.zone:<{zone_w}}"
            f"{cpu_count:>{cpu_w}}"
            f"{pages_to_display(sums.get('count'), pagesize_bytes, unit):>{mem_w}}"
            f"{pages_to_display(sums.get('high'), pagesize_bytes, unit):>{mem_w}}"
            f"{pages_to_display(sums.get('batch'), pagesize_bytes, unit):>{mem_w}}"
            f"{pages_to_display(sums.get('high_min'), pagesize_bytes, unit):>{mem_w}}"
            f"{pages_to_display(sums.get('high_max'), pagesize_bytes, unit):>{mem_w}}"
            f"{vmstat_disp:>{vmstat_w}}"
        )


def print_zone_summary(
    zoneinfo_path: str,
    unit: str,
    pagesize_bytes: int = DEFAULT_PAGE_SIZE_BYTES,
    use_color: bool = True,
    verbose: bool = False,
    show_pcp: bool = False,
) -> None:
    unit_label = UNIT_LABELS[unit]
    zones = parse_zoneinfo(zoneinfo_path)
    if not zones:
        print(f"Warning: no zoneinfo data (missing or empty): {zoneinfo_path}")
        return

    print(f"\n/proc/zoneinfo  ({zoneinfo_path})")
    print(
        f"Assumed PAGE_SIZE = {pagesize_bytes} bytes ({pagesize_bytes / 1024:g} KiB per page)."
    )
    print(
        "Free memory includes buddy lists (nr_free_pages) + per-CPU pageset (PCP) caches.\n"
    )
    if use_color and verbose:
        print(
            "    (TTY colors: bold red = at/below min; bold yellow = at/below low; "
            "yellow = at/below high; watermarks use buddy+PCP free)\n"
        )

    total_managed = 0
    total_free = 0
    total_buddy = 0
    total_pcp = 0
    have_totals = True
    for z in zones:
        cap = zone_capacity_pages(z)
        free = zone_total_free_pages(z)
        if cap is None or free is None:
            have_totals = False
            continue
        total_managed += cap
        total_free += free
        total_buddy += zone_buddy_free_pages(z) or 0
        total_pcp += zone_pcp_pages(z)

    if have_totals and total_managed > 0:
        total_used = total_managed - total_free
        print(
            f"Zones (sum): managed {pages_to_display(total_managed, pagesize_bytes, unit)} {unit_label}  "
            f"free {pages_to_display(total_free, pagesize_bytes, unit)} {unit_label}  "
            f"(buddy {pages_to_display(total_buddy, pagesize_bytes, unit)} + "
            f"PCP {pages_to_display(total_pcp, pagesize_bytes, unit)} {unit_label})  "
            f"used {pages_to_display(total_used, pagesize_bytes, unit)} {unit_label}  "
            f"({100.0 * total_free / total_managed:.1f}% free)"
        )
        meminfo_path = meminfo_path_for_zoneinfo(zoneinfo_path)
        if meminfo_path:
            mi = parse_meminfo(meminfo_path)
            mem_total = mi.get("MemTotal")
            if mem_total and mem_total > 0:
                zone_kib = pages_to_kib(total_managed, pagesize_bytes)
                pct = 100.0 * zone_kib / mem_total
                print(
                    f"MemTotal ({display_path_near_zoneinfo(meminfo_path, zoneinfo_path)}): "
                    f"{kib_to_display(float(mem_total), pagesize_bytes, unit)} {unit_label}  "
                    f"→ managed sum is {pct:.1f}% of MemTotal "
                    "(normal: slightly below RAM; excludes some reserved/unmanaged)\n"
                )
        else:
            print(
                "MemTotal: not found (place proc/meminfo next to zoneinfo for RAM comparison).\n"
            )
    else:
        print()

    zone_w = max(len("zone"), *(len(z.zone) for z in zones)) + 1
    node_col = 4
    mem_w = max(
        12,
        len(f"managed ({unit_label})"),
        *(
            len(pages_to_display(p, pagesize_bytes, unit))
            for z in zones
            for p in (
                zone_capacity_pages(z),
                zone_buddy_free_pages(z),
                zone_pcp_pages(z),
                zone_total_free_pages(z),
            )
        ),
    )

    hdr = (
        f"{'':>{node_col}} {'zone':<{zone_w}}"
        f"{f'managed ({unit_label})':>{mem_w}}"
        f"{f'buddy ({unit_label})':>{mem_w}}"
        f"{f'PCP ({unit_label})':>{mem_w}}"
        f"{f'total ({unit_label})':>{mem_w}}"
        f"{'used %':>8}{'free %':>8}{'WM':>6}  note"
    )
    print(hdr)
    print("-" * len(hdr))

    for z in zones:
        cap = zone_capacity_pages(z)
        buddy = zone_buddy_free_pages(z)
        pcp = zone_pcp_pages(z)
        free_p = zone_total_free_pages(z)
        sev = watermark_severity(free_p, z)
        if cap is None or free_p is None:
            used_pct_s = free_pct_s = "      -"
            free_disp = "-"
            managed_disp = pages_to_display(cap, pagesize_bytes, unit)
            buddy_disp = pcp_disp = "-"
            extra_note = ""
        elif cap == 0:
            used_pct_s = free_pct_s = "    n/a"
            free_disp = (
                pages_to_display(free_p, pagesize_bytes, unit) if free_p else "-"
            )
            managed_disp = pages_to_display(cap, pagesize_bytes, unit)
            buddy_disp = pages_to_display(buddy, pagesize_bytes, unit)
            pcp_disp = pages_to_display(pcp, pagesize_bytes, unit)
            extra_note = "empty zone"
            sev = "none"
        else:
            used_pct_s = f"{100.0 * max(cap - free_p, 0) / cap:>7.1f}%"
            free_pct_s = f"{100.0 * free_p / cap:>7.1f}%"
            free_disp = pages_to_display(free_p, pagesize_bytes, unit)
            managed_disp = pages_to_display(cap, pagesize_bytes, unit)
            buddy_disp = pages_to_display(buddy, pagesize_bytes, unit)
            pcp_disp = pages_to_display(pcp, pagesize_bytes, unit)
            extra_note = ""
        wm = {"min": "MIN", "low": "LOW", "below-high": "HIGH", "ok": "ok", "none": "-"}.get(
            sev, "-"
        )
        note = watermark_note(free_p, z, pagesize_bytes, unit, unit_label)
        if extra_note:
            note = f"{note}  {extra_note}".strip() if note else extra_note
        line = (
            f"{('N' + str(z.node)):>{node_col}} {z.zone:<{zone_w}}"
            f"{managed_disp:>{mem_w}}{buddy_disp:>{mem_w}}"
            f"{pcp_disp:>{mem_w}}{free_disp:>{mem_w}}"
            f"{used_pct_s:>8}{free_pct_s:>8}{wm:>6}  {note}"
        )
        print(color_line(line, sev, use_color))

    # Per-node rollup
    by_node: Dict[int, Tuple[int, int, int, int]] = {}
    for z in zones:
        cap = zone_capacity_pages(z)
        free_p = zone_total_free_pages(z)
        if cap is None or free_p is None:
            continue
        prev = by_node.get(z.node, (0, 0, 0, 0))
        by_node[z.node] = (
            prev[0] + cap,
            prev[1] + free_p,
            prev[2] + (zone_buddy_free_pages(z) or 0),
            prev[3] + zone_pcp_pages(z),
        )

    if len(by_node) > 1:
        print("\nPer-node zone totals (managed / free, buddy + PCP):")
        for node in sorted(by_node.keys()):
            managed, free_p, buddy, pcp = by_node[node]
            used = managed - free_p
            print(
                f"  Node {node}: managed {pages_to_display(managed, pagesize_bytes, unit)} {unit_label}  "
                f"free {pages_to_display(free_p, pagesize_bytes, unit)} {unit_label}  "
                f"(buddy {pages_to_display(buddy, pagesize_bytes, unit)} + "
                f"PCP {pages_to_display(pcp, pagesize_bytes, unit)} {unit_label})  "
                f"used {pages_to_display(used, pagesize_bytes, unit)} {unit_label}"
            )

    # Zone LRU counters (nr_zone_* on modern kernels)
    mix_cols = ("anon", "file", "dirty", "unevict")
    mix_w = 14
    print(
        f"\nZone LRU per zone ({unit_label}; anon/file = active+inactive nr_zone_*):\n"
        f"{'':>{node_col}} {'zone':<{zone_w}}"
        + "".join(f"{h:>{mix_w}}" for h in mix_cols)
    )
    print("-" * (node_col + 1 + zone_w + mix_w * len(mix_cols)))
    for z in zones:
        vals = (
            zone_anon_pages(z),
            zone_file_pages(z),
            zone_dirty_pages(z),
            zone_unevictable_pages(z),
        )
        print(
            f"{('N' + str(z.node)):>{node_col}} {z.zone:<{zone_w}}"
            + "".join(
                f"{pages_to_display(v, pagesize_bytes, unit):>{mix_w}}" for v in vals
            )
        )

    per_node = node_stats_by_node(zones)
    if per_node:
        node_mix_cols = ("anon", "file", "slab", "shmem", "dirty", "unevict")
        node_mix_w = 14
        print(
            f"\nPer-node stats ({unit_label}; printed once in zoneinfo under first populated zone):\n"
            f"{'node':>6}"
            + "".join(f"{h:>{node_mix_w}}" for h in node_mix_cols)
        )
        print("-" * (6 + node_mix_w * len(node_mix_cols)))
        for node in sorted(per_node.keys()):
            ns = per_node[node]
            anon = ns.get("nr_anon_pages", 0)
            file_p = ns.get("nr_file_pages", 0)
            slab = (ns.get("nr_slab_reclaimable", 0) + ns.get("nr_slab_unreclaimable", 0))
            shmem = ns.get("nr_shmem", 0)
            dirty = ns.get("nr_dirty", 0)
            unevict = ns.get("nr_unevictable", 0)
            print(
                f"{node:>6}"
                + f"{pages_to_display(anon, pagesize_bytes, unit):>{node_mix_w}}"
                + f"{pages_to_display(file_p, pagesize_bytes, unit):>{node_mix_w}}"
                + f"{pages_to_display(slab, pagesize_bytes, unit):>{node_mix_w}}"
                + f"{pages_to_display(shmem, pagesize_bytes, unit):>{node_mix_w}}"
                + f"{pages_to_display(dirty, pagesize_bytes, unit):>{node_mix_w}}"
                + f"{pages_to_display(unevict, pagesize_bytes, unit):>{node_mix_w}}"
            )

    if verbose:
        wm_w = max(10, mem_w)
        print(f"\nFree breakdown vs watermarks ({unit_label}; WM compares buddy+PCP total):")
        print(
            f"{'':>{node_col}} {'zone':<{zone_w}}"
            f"{'buddy':>{wm_w}}{'PCP':>{wm_w}}{'total':>{wm_w}}"
            f"{'min':>{wm_w}}{'low':>{wm_w}}{'high':>{wm_w}}"
        )
        print("-" * (node_col + 1 + zone_w + wm_w * 6))
        for z in zones:
            buddy = zone_buddy_free_pages(z) or 0
            pcp = zone_pcp_pages(z)
            total = zone_total_free_pages(z) or 0
            print(
                f"{('N' + str(z.node)):>{node_col}} {z.zone:<{zone_w}}"
                f"{pages_to_display(buddy, pagesize_bytes, unit):>{wm_w}}"
                f"{pages_to_display(pcp, pagesize_bytes, unit):>{wm_w}}"
                f"{pages_to_display(total, pagesize_bytes, unit):>{wm_w}}"
                f"{pages_to_display(stat(z, 'min') or 0, pagesize_bytes, unit):>{wm_w}}"
                f"{pages_to_display(stat(z, 'low') or 0, pagesize_bytes, unit):>{wm_w}}"
                f"{pages_to_display(stat(z, 'high') or 0, pagesize_bytes, unit):>{wm_w}}"
            )

    if show_pcp:
        print_pcp_details(zones, unit, unit_label, pagesize_bytes, node_col, zone_w)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize /proc/zoneinfo memory state per NUMA node and zone."
    )
    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument(
        "-P", "--pages",
        action="store_const", const="P", dest="unit",
        help="Show sizes in pages",
    )
    unit_group.add_argument(
        "-K", "--kib",
        action="store_const", const="K", dest="unit",
        help="Show sizes in KiB",
    )
    unit_group.add_argument(
        "-M", "--mib",
        action="store_const", const="M", dest="unit",
        help="Show sizes in MiB",
    )
    unit_group.add_argument(
        "-G", "--gib",
        action="store_const", const="G", dest="unit",
        help="Show sizes in GiB (default)",
    )
    parser.set_defaults(unit="G")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show buddy/PCP/total free vs watermarks and TTY color legend",
    )
    parser.add_argument(
        "--pcp",
        action="store_true",
        help="Show per-CPU pageset cache details",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI watermark highlights",
    )
    parser.add_argument(
        "--pagesize",
        type=int,
        default=DEFAULT_PAGE_SIZE_BYTES,
        metavar="BYTES",
        help="Machine page size in bytes (default: %(default)s, x86_64).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="sosreport root, or path to proc/zoneinfo (default: cwd)",
    )
    args = parser.parse_args()

    if args.pagesize <= 0:
        print("Error: --pagesize must be a positive integer (bytes).", file=sys.stderr)
        sys.exit(1)

    root = args.path or "."
    zoneinfo_path = resolve_zoneinfo_path(root)
    unit = args.unit
    use_color = sys.stdout.isatty() and not args.no_color

    print_zone_summary(
        zoneinfo_path,
        unit,
        pagesize_bytes=args.pagesize,
        use_color=use_color,
        verbose=args.verbose,
        show_pcp=args.pcp,
    )


if __name__ == "__main__":
    main()
