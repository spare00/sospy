#!/usr/bin/env python3

import os
import re
import sys
import argparse
from typing import List, Optional, Tuple

def scale_value(kb, unit):
    if unit == "K": return kb
    if unit == "M": return kb / 1024
    if unit == "G": return kb / (1024 * 1024)

def parse_buddyinfo(path: str) -> List[Tuple[int, str, List[int]]]:
    """
    Parse /proc/buddyinfo lines into (node_id, zone_name, counts_per_order).
    Each count is free blocks of that buddy order; order k holds 2^k base pages.
    """
    rows: List[Tuple[int, str, List[int]]] = []
    prefix = re.compile(r"^Node\s+(\d+),\s*zone\s+")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                m = prefix.match(line)
                if not m:
                    continue
                node = int(m.group(1))
                rest = line[m.end() :].strip()
                parts = rest.split()
                if not parts:
                    continue
                i = len(parts) - 1
                counts_rev: List[int] = []
                while i >= 0 and parts[i].isdigit():
                    counts_rev.append(int(parts[i]))
                    i -= 1
                if not counts_rev:
                    continue
                counts_rev.reverse()
                zone = " ".join(parts[: i + 1]).strip()
                rows.append((node, zone, counts_rev))
    except FileNotFoundError:
        pass
    return rows


def format_buddy_order_count(n: int) -> str:
    """
    Table cell for one buddy order: thousands separators, or scientific if huge
    (often indicates a capture where spaces between counts were lost).
    """
    if n < 0:
        return str(n)
    if n == 0:
        return "0"
    # Per-order free counts are usually modest; monstrous values are rarely real.
    if n >= 10**11 or len(str(n)) > 11:
        return f"{n:.4e}"
    return f"{n:,}"


def buddy_zone_totals_kb(counts: List[int], page_kb: int = 4) -> Tuple[int, float]:
    """Total free pages (4 KiB units) and same in KiB for this zone's buddy list."""
    total_pages = 0
    for order, c in enumerate(counts):
        total_pages += c * (2**order)
    return total_pages, float(total_pages * page_kb)


def buddy_row_severity(counts: List[int], page_kb: int = 4) -> str:
    """
    Highlight class for buddy zone row: risk (fragmentation), o0, empty, or none.
    """
    total_pages, _ = buddy_zone_totals_kb(counts, page_kb)
    if total_pages <= 0:
        return "empty"
    p0 = counts[0] if len(counts) > 0 else 0
    p02 = sum(counts[i] * (2**i) for i in range(min(3, len(counts))))
    pct0 = 100.0 * (p0 * 1) / total_pages
    pct02 = 100.0 * p02 / total_pages
    if pct02 >= 75:
        return "risk"
    if pct0 >= 50:
        return "o0"
    return "none"


def buddy_color_line(line: str, severity: str, use_color: bool) -> str:
    if not use_color or severity == "none":
        return line
    reset = "\033[0m"
    if severity == "risk":
        return f"\033[1m\033[91m{line}{reset}"
    if severity == "o0":
        return f"\033[1m\033[93m{line}{reset}"
    if severity == "empty":
        return f"\033[2m{line}{reset}"
    return line


def meminfo_path_for_buddy(buddy_path: str) -> Optional[str]:
    """Locate proc/meminfo next to a buddyinfo capture (same dir or …/proc/)."""
    d = os.path.dirname(os.path.abspath(buddy_path))
    for cand in (
        os.path.join(d, "meminfo"),
        os.path.join(d, "proc", "meminfo"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def print_buddyinfo(
    buddy_path: str,
    unit: str,
    page_kb: int = 4,
    use_color: bool = True,
    verbose: bool = False,
) -> None:
    """
    Rich buddyinfo: per-order counts, chunk sizes, free totals, fragmentation hints.
    """
    unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}[unit]
    rows = parse_buddyinfo(buddy_path)
    if not rows:
        print(f"Warning: no buddyinfo data (missing or empty): {buddy_path}")
        return

    n_orders = max(len(r[2]) for r in rows)
    print(f"\n/proc/buddyinfo  ({buddy_path})")
    print(f"Assumed PAGE_SIZE = {page_kb} KiB per page for size totals.")
    print(
        "Each column is the count of free buddy blocks of that order "
        f"(order k spans {page_kb}×2^k KiB per block).\n"
    )
    if use_color and verbose:
        print(
            "    (TTY colors: bold red = fragmentation risk; bold yellow = O0-heavy; dim = empty zone)\n"
        )

    total_buddy_kb = sum(buddy_zone_totals_kb(r[2], page_kb)[1] for r in rows)
    meminfo_path = meminfo_path_for_buddy(buddy_path)
    mem_total_kb: Optional[int] = None
    if meminfo_path:
        mi = parse_meminfo(meminfo_path)
        mem_total_kb = mi.get("MemTotal")
    buddy_s = scale_value(total_buddy_kb, unit)
    print(
        f"Buddy free (sum of all zones): {buddy_s:.2f} {unit_label}  "
        f"({total_buddy_kb:,.0f} KiB as base pages)"
    )
    if mem_total_kb and mem_total_kb > 0:
        pct_ram = 100.0 * total_buddy_kb / mem_total_kb
        mt_s = scale_value(float(mem_total_kb), unit)
        print(
            f"MemTotal ({meminfo_path}): {mt_s:.2f} {unit_label}  "
            f"→ buddy sum is {pct_ram:.2f}% of system RAM"
        )
        print(
            "    (Buddy lists are only physically contiguous free pages in each zone; "
            "this sum is usually well below MemFree.)\n"
        )
    else:
        print(
            "MemTotal: not found (place proc/meminfo next to buddyinfo or use a sosreport root "
            "with proc/meminfo to show percent of RAM).\n"
        )

    zone_w = max(len(r[1]) for r in rows) + 1
    node_col = 4  # "N0", "N10"
    first_col_w = node_col + 1 + zone_w
    hdr_orders = [f"O{o}" for o in range(n_orders)]
    hdr_kb = [f"{page_kb * (2**o):g}k" for o in range(n_orders)]
    # +1 gutter so right-aligned counts never touch (e.g. "92,115" + "613,415").
    col_w = max(6, max(len(h) for h in hdr_orders) + 1) + 1
    for _node, _zone, counts in rows:
        for i in range(n_orders):
            if i < len(counts):
                col_w = max(col_w, len(format_buddy_order_count(counts[i])) + 1)
    for h in hdr_kb:
        col_w = max(col_w, len(h) + 1)

    print(f"{'':{node_col}} {'zone':<{zone_w}}" + "".join(f"{h:>{col_w}}" for h in hdr_orders))
    print(f"{'':{node_col}} {'KiB/blk':<{zone_w}}" + "".join(f"{h:>{col_w}}" for h in hdr_kb))
    huge_note = False
    for node, zone, counts in rows:
        cells = []
        for i in range(n_orders):
            if i < len(counts):
                v = counts[i]
                fs = format_buddy_order_count(v)
                if "e" in fs.lower():
                    huge_note = True
                cells.append(f"{fs:>{col_w}}")
            else:
                cells.append(f"{'-':>{col_w}}")
        left = f"{('N' + str(node)):>{node_col}} {zone:<{zone_w}}"
        print(f"{left:<{first_col_w}}" + "".join(cells))
    if huge_note:
        print(
            "\n    Note: some counts use scientific notation (e.g. 9.2116e+16) when a value "
            "is huge — often the sos capture lost spaces between buddy columns; "
            "re-copy /proc/buddyinfo with spacing preserved.\n"
        )

    # --- Summary + fragmentation ---
    zlab = max(zone_w - 2, 8)
    print(
        f"\n{'Node':<6}{'Zone':<{zlab}}{'Free pages':>14}{f'Free ({unit_label})':>16}"
        f"{'% in O0':>10}{'% in O0-2':>12}{'max order':>10}  note"
    )
    sep = 6 + zlab + 14 + 16 + 10 + 12 + 10 + 28
    print("-" * sep)

    for node, zone, counts in rows:
        total_pages, kb = buddy_zone_totals_kb(counts, page_kb)
        sev = buddy_row_severity(counts, page_kb)
        if total_pages <= 0:
            pct0 = pct02 = 0.0
            max_o_str = "-"
            note = "empty"
        else:
            p0 = counts[0] if len(counts) > 0 else 0
            p02 = sum(counts[i] * (2**i) for i in range(min(3, len(counts))))
            pct0 = 100.0 * (p0 * 1) / total_pages
            pct02 = 100.0 * p02 / total_pages
            max_o = max((i for i, c in enumerate(counts) if c > 0), default=-1)
            max_o_str = str(max_o) if max_o >= 0 else "-"
            if pct02 >= 75:
                note = "many small blocks (fragmentation risk)"
            elif pct0 >= 50:
                note = "O0-heavy"
            elif max_o >= 6:
                note = "large blocks present"
            else:
                note = ""
        line = (
            f"{node:<6}{zone:<{zlab}}{total_pages:>14,}"
            f"{scale_value(kb, unit):>16.2f}"
            f"{pct0:>9.1f}%"
            f"{pct02:>11.1f}%"
            f"{max_o_str:>10}  {note}"
        )
        print(buddy_color_line(line, sev, use_color))

    # --- Node rollup ---
    by_node: dict[int, Tuple[int, float]] = {}
    for node, zone, counts in rows:
        tp, kb = buddy_zone_totals_kb(counts, page_kb)
        prev = by_node.get(node, (0, 0.0))
        by_node[node] = (prev[0] + tp, prev[1] + kb)
    if len(by_node) > 1:
        print("\nPer-node buddy free (sum of zones):")
        for node in sorted(by_node.keys()):
            tp, kb = by_node[node]
            print(
                f"  Node {node}: {tp:,} pages  "
                f"{scale_value(kb, unit):.2f} {unit_label}"
            )

    # --- Stacked bar: width proportional to free pages in each order ---
    print(
        "\nOrder mix (each row = 40 chars; segment width ~ share of zone free pages; "
        "O0 left → higher orders right):"
    )
    bar_w = 40
    suffix_w = (
        max(len(f"O0..O{max(0, len(r[2]) - 1)}") for r in rows) if rows else 8
    )
    for node, zone, counts in rows:
        total_pages, _ = buddy_zone_totals_kb(counts, page_kb)
        if total_pages <= 0:
            bar_fill = "(empty)".ljust(bar_w)[:bar_w]
        else:
            widths: List[int] = []
            acc = 0
            for o, c in enumerate(counts):
                contrib = c * (2**o)
                if o < len(counts) - 1:
                    w = int(round(bar_w * contrib / total_pages))
                    w = max(0, min(w, bar_w - acc))
                else:
                    w = max(0, bar_w - acc)
                widths.append(w)
                acc += w
            chars = "█▓░▒"
            parts = []
            for o, w in enumerate(widths):
                parts.append(chars[o % len(chars)] * w)
            bar_fill = "".join(parts)
            if len(bar_fill) < bar_w:
                bar_fill = bar_fill + " " * (bar_w - len(bar_fill))
            else:
                bar_fill = bar_fill[:bar_w]
        om = len(counts) - 1 if counts else 0
        prefix = f"{('N' + str(node)):>{node_col}} {zone:<{zone_w}}"
        prefix = f"{prefix:<{first_col_w}}"
        suffix = f"O0..O{om}"
        mix_line = f"{prefix}[{bar_fill}] {suffix:>{suffix_w}}"
        print(buddy_color_line(mix_line, buddy_row_severity(counts, page_kb), use_color))
    print()


def parse_meminfo(path: str) -> dict:
    out = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    out[parts[0].rstrip(':')] = int(parts[1])
    except FileNotFoundError:
        pass
    return out

def parse_sysvipc_shm(path: str) -> Tuple[int, int, int]:
    vss = rss = swapped = 0
    try:
        with open(path) as f:
            header = f.readline().split()
            idx_size = header.index("size")
            idx_rss = header.index("rss")
            idx_swap = header.index("swap")
            for line in f:
                parts = line.split()
                if len(parts) > max(idx_size, idx_rss, idx_swap):
                    vss += int(parts[idx_size])
                    rss += int(parts[idx_rss])
                    swapped += int(parts[idx_swap])
    except FileNotFoundError:
        pass
    return vss // 1024, rss // 1024, swapped // 1024

def parse_tmpfs_df(path: str) -> Optional[int]:
    try:
        with open(path) as f:
            used_total = 0
            _ = f.readline()  # Skip header
            for line in f:
                parts = line.split()
                if len(parts) >= 6 and "tmpfs" in parts[0] and parts[5] != "/dev":
                    try:
                        used_total += int(parts[2])
                    except ValueError:
                        # Skip lines with non-integer 'Used' values like '-'
                        continue
            return used_total
    except FileNotFoundError:
        return None

def get_hugepages_used_kb(meminfo: dict) -> int:
    """
    Returns the amount of memory used by HugePages in KiB.
    Tries 'Hugetlb' field first; if missing, falls back to calculation.
    """
    if "Hugetlb" in meminfo:
        return meminfo["Hugetlb"]

    huge_total = meminfo.get("HugePages_Total", 0)
    huge_free = meminfo.get("HugePages_Free", 0)
    huge_size = meminfo.get("Hugepagesize", 0)

    used_kb = (huge_total - huge_free) * huge_size
    return used_kb

def get_hugepages_reserved_kb(meminfo: dict) -> int:
    """
    Returns the total memory reserved for HugePages in KiB.
    This is HugePages_Total * Hugepagesize.
    """
    huge_total = meminfo.get("HugePages_Total", 0)
    huge_size = meminfo.get("Hugepagesize", 0)

    reserved_kb = huge_total * huge_size
    return reserved_kb

def calculate_unaccounted(meminfo):
    total = meminfo.get("MemTotal", 0)

    fields = [
        "MemFree", "Buffers", "Cached", "SwapCached",
        "AnonPages", "Slab", "KernelStack",
        "PageTables", "Percpu"
    ]
    accounted_sum = sum(meminfo.get(field, 0) for field in fields)

    huge_reserved_kb = get_hugepages_reserved_kb(meminfo)
    accounted_sum += huge_reserved_kb
    fields.append("HugePagesReserved")

    return total, accounted_sum, total - accounted_sum, fields

def print_simple(meminfo, unit):
    """Simplified version for a standalone /proc/meminfo file"""
    unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}[unit]
    def show(label, value, extra=None):
        line = f"{label:<30} {scale_value(value, unit):>10.2f}"
        if extra:
            line += f"  ({extra})"
        print(line)

    active_anon = meminfo.get("Active(anon)", 0)
    inactive_anon = meminfo.get("Inactive(anon)", 0)
    anonpages = meminfo.get("AnonPages", 0)
    shmem = meminfo.get("Shmem", 0)

    # no tmpfs/sysv breakdown in this mode
    anon_shared_kb = max((active_anon + inactive_anon) - meminfo.get("AnonPages", 0), 0)
    anon_extra_text = f"diff={scale_value(anon_shared_kb, unit):.2f} {unit_label}"

    huge_total_kb = get_hugepages_reserved_kb(meminfo)
    huge_used_kb = get_hugepages_used_kb(meminfo)

    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)

    total, accounted, unaccounted, _ = calculate_unaccounted(meminfo)

    print(f"{'Field':<30} {'Size (' + unit_label + ')':>10}")
    print("=" * 42)
    show("MemTotal:", meminfo.get("MemTotal", 0))
    show("MemFree", meminfo.get("MemFree", 0))
    show("Buffers", meminfo.get("Buffers", 0))
    show("Cached", meminfo.get("Cached", 0))
    show("SwapCached", meminfo.get("SwapCached", 0))
    show("AnonPages", meminfo.get("AnonPages", 0), anon_extra_text)
    show("  Active(anon)", active_anon)
    show("  Inactive(anon)", inactive_anon)
    show("Slab", meminfo.get("Slab", 0))
    show("KernelStack", meminfo.get("KernelStack", 0))
    show("PageTables", meminfo.get("PageTables", 0))
    show("Percpu", meminfo.get("Percpu", 0))
    show("HugePages_Total", huge_total_kb)
    show("HugePagesUsed", huge_used_kb)
    show("SwapTotal", swap_total)
    show("SwapUsed", swap_used)
    print("=" * 42)
    show("Unaccounted:", total - accounted, unit_label)

def print_detailed(meminfo, tmpfs_used, sysv_rss_kb, unit, verbose=False):
    unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}[unit]
    def show(label, value, extra=None):
        line = f"{label:<30} {scale_value(value, unit):>10.2f}"
        if extra:
            line += f"  ({extra})"
        print(line)

    huge_total = meminfo.get("HugePages_Total", 0)
    huge_free = meminfo.get("HugePages_Free", 0)
    huge_size = meminfo.get("Hugepagesize", 0)
    huge_total_kb = huge_total * huge_size
    hugetlb_used_kb = (huge_total - huge_free) * huge_size

    non_hugetlb_sysv_rss = max(sysv_rss_kb - hugetlb_used_kb, 0)
    tmpfs_used = tmpfs_used or 0

    shmem_kb = meminfo.get("Shmem", 0)
    shmem_extra_kb = max(non_hugetlb_sysv_rss + tmpfs_used - shmem_kb, 0)
    shmem_extra_text = f"extra={scale_value(shmem_extra_kb, unit):.2f} {unit_label}"

    active_anon = meminfo.get("Active(anon)", 0)
    inactive_anon = meminfo.get("Inactive(anon)", 0)
    anonpages = meminfo.get("AnonPages", 0)
    anon_diff = (active_anon + inactive_anon) - anonpages
    anon_shared_kb = anon_diff if anon_diff > 0 else 0

    anon_extra_text = f"diff={scale_value(anon_shared_kb, unit):.2f} {unit_label}"

    print(f"{'Field':<30} {'Size (' + unit_label + ')':>10}")
    print("=" * 42)
    show("MemTotal:", meminfo.get("MemTotal", 0))
    show("MemFree", meminfo.get("MemFree", 0))
    show("Buffers", meminfo.get("Buffers", 0))

    cached = meminfo.get("Cached", 0)
    show("Cached", cached)
    show("  pagecache", cached - shmem_kb)
    show("  Shmem", shmem_kb, shmem_extra_text)
    show("    SysV (non-Hugetlb)", non_hugetlb_sysv_rss)
    show("    tmpfs", tmpfs_used)

    show("SwapCached", meminfo.get("SwapCached", 0))
    show("AnonPages", meminfo.get("AnonPages", 0), anon_extra_text)
    show("  Active(anon)", active_anon)
    show("  Inactive(anon)", inactive_anon)
    show("Slab", meminfo.get("Slab", 0))
    show("KernelStack", meminfo.get("KernelStack", 0))
    show("PageTables", meminfo.get("PageTables", 0))
    show("Percpu", meminfo.get("Percpu", 0))
    show("HugePages_Total", huge_total_kb)
    show("HugePages_Used", hugetlb_used_kb)

    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)
    show("SwapTotal", swap_total)
    show("SwapUsed", swap_used)
    print("=" * 42)

    total, accounted, unaccounted, fields = calculate_unaccounted(meminfo)
    if verbose:
        print("\nFormula used for calculation:")
        print("  Unaccounted Memory = MemTotal - " + " - ".join(fields))

        total_val = scale_value(meminfo.get("MemTotal", 0), unit)

        huge_reserved_kb = get_hugepages_reserved_kb(meminfo)
        # Map each field name to its displayed numeric value
        values = []
        for f in fields:
            if f == "HugePagesReserved":
                v = scale_value(huge_reserved_kb, unit)
            else:
                v = scale_value(meminfo.get(f, 0), unit)
            values.append(v)

        expr = " - ".join(f"{v:.2f}" for v in values)
        result = scale_value(unaccounted, unit)

        print(f"  {result:.2f} = {total_val:.2f} - {expr}\n")

    show("Unaccounted:", unaccounted, unit_label)

def parse_ps(path: str, top_n: int = 10) -> Optional[list]:
    """
    Parse a ps output file and return the top N processes by RSS.
    Skips continuation/thread lines where PID is '-'.
    Returns list of raw line strings (header + top N), or None if file not found.
    """
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None

    if not lines:
        return None

    header = lines[0]
    header_parts = header.split()
    try:
        pid_col = header_parts.index("PID")
        rss_col = header_parts.index("RSS")
    except ValueError:
        return None

    procs = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= max(pid_col, rss_col):
            continue
        if parts[pid_col] == "-":
            # Continuation/thread line — skip
            continue
        try:
            rss = int(parts[rss_col])
        except ValueError:
            continue
        procs.append((rss, line))

    procs.sort(key=lambda x: x[0], reverse=True)
    return [header] + [line for _, line in procs[:top_n]]


def print_top_procs(ps_path: str, top_n: int = 10):
    rows = parse_ps(ps_path, top_n)
    if rows is None:
        print(f"Warning: ps file not found: {ps_path}")
        return

    header_parts = rows[0].split()
    try:
        rss_col = header_parts.index("RSS")
    except ValueError:
        print("Warning: could not find RSS column in ps header")
        return

    total_rss = 0
    for row in rows[1:]:
        parts = row.split()
        if len(parts) <= rss_col:
            continue
        try:
            total_rss += int(parts[rss_col])
        except ValueError:
            continue

    print(f"\nTop {top_n} processes by RSS:  Total={total_rss} KiB ({total_rss / 2**20:.2f} GiB)")
    print("=" * 80)
    for row in rows:
        print(row, end="")
    print()


def print_top_cmds(ps_path: str, top_n: int = 10):
    """
    Aggregate RSS by the first token of COMMAND, skipping continuation lines
    where PID is '-'. Prints top N commands by total RSS plus a grand total.
    """
    try:
        with open(ps_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Warning: ps file not found: {ps_path}")
        return

    if not lines:
        return

    header_parts = lines[0].split()
    try:
        pid_col = header_parts.index("PID")
        rss_col = header_parts.index("RSS")
        cmd_col = header_parts.index("COMMAND")
    except ValueError:
        print("Warning: could not find PID, RSS, or COMMAND column in ps header")
        return

    rss_by_cmd = {}
    cnt_by_cmd = {}
    total_rss = 0

    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= max(pid_col, rss_col, cmd_col):
            continue
        if parts[pid_col] == "-":
            continue
        try:
            rss = int(parts[rss_col])
        except ValueError:
            continue
        cmd = parts[cmd_col]  # first token of COMMAND
        rss_by_cmd[cmd] = rss_by_cmd.get(cmd, 0) + rss
        cnt_by_cmd[cmd] = cnt_by_cmd.get(cmd, 0) + 1
        total_rss += rss

    ranked = sorted(rss_by_cmd.items(), key=lambda x: x[1], reverse=True)

    print(f"\nTop {top_n} commands by aggregated RSS:  Total={total_rss} KiB ({total_rss / 2**20:.2f} GiB)")
    print(f"{'KiB':>10}  {'GiB':>8}   CNT  COMMAND")
    print("=" * 80)
    for cmd, rss in ranked[:top_n]:
        print(f"{rss:>10d} KiB ({rss / 2**20:>6.2f} GiB) {cnt_by_cmd[cmd]:>4} {cmd}")
    print()



def print_top_users(ps_path: str, top_n: int = 10):
    """
    Aggregate RSS by USER, skipping continuation lines where PID is '-'.
    Prints top N users by total RSS plus a grand total.
    """
    try:
        with open(ps_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Warning: ps file not found: {ps_path}")
        return

    if not lines:
        return

    header_parts = lines[0].split()
    try:
        user_col = header_parts.index("USER")
        pid_col = header_parts.index("PID")
        rss_col = header_parts.index("RSS")
    except ValueError:
        print("Warning: could not find USER, PID, or RSS column in ps header")
        return

    rss_by_user = {}
    cnt_by_user = {}
    total_rss = 0

    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= max(user_col, pid_col, rss_col):
            continue
        if parts[pid_col] == "-":
            continue
        try:
            rss = int(parts[rss_col])
        except ValueError:
            continue
        user = parts[user_col]
        rss_by_user[user] = rss_by_user.get(user, 0) + rss
        cnt_by_user[user] = cnt_by_user.get(user, 0) + 1
        total_rss += rss

    ranked = sorted(rss_by_user.items(), key=lambda x: x[1], reverse=True)

    print(f"\nTop {top_n} users by aggregated RSS:  Total={total_rss} KiB ({total_rss / 2**20:.2f} GiB)")
    print(f"{'KiB':>10}  {'GiB':>8}   CNT  USER")
    print("=" * 80)
    for user, rss in ranked[:top_n]:
        print(f"{rss:>10d} KiB ({rss / 2**20:>6.2f} GiB) {cnt_by_user[user]:>4} {user}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-K", action="store_const", const="K", dest="unit", help="Show output in KiB")
    parser.add_argument("-M", action="store_const", const="M", dest="unit", help="Show output in MiB")
    parser.add_argument("-G", action="store_const", const="G", dest="unit", help="Show output in GiB")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show formula for unaccounted memory (-i); TTY color legend for buddy (-b)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors (buddy -b highlights)",
    )
    parser.add_argument("path", nargs="?", help="sosreport root or /proc/meminfo file (default: cwd)")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-i", nargs="?", const=True, metavar="MEMINFO_FILE",
                      help="Show memory info (default mode). Optionally specify a meminfo file path.")
    mode.add_argument("-p", nargs="?", const=True, metavar="PS_FILE",
                      help="Show top 10 processes by RSS. Optionally specify a ps file path; "
                           "defaults to <sosroot>/ps or ./ps")
    mode.add_argument("-c", nargs="?", const=True, metavar="PS_FILE",
                      help="Show top 10 commands by aggregated RSS. Optionally specify a ps file path; "
                           "defaults to <sosroot>/ps or ./ps")
    mode.add_argument("-u", nargs="?", const=True, metavar="PS_FILE",
                      help="Show top 10 users by aggregated RSS. Optionally specify a ps file path; "
                           "defaults to <sosroot>/ps or ./ps")
    mode.add_argument(
        "-b",
        nargs="?",
        const=True,
        metavar="BUDDYINFO_FILE",
        help="Show /proc/buddyinfo: per-order free blocks, totals, fragmentation hints, node rollups",
    )

    args = parser.parse_args()

    # Default to -i mode when none of the mode flags is given
    if not args.i and not args.p and not args.c and not args.u and not args.b:
        args.i = True

    path = args.path or "."
    unit = args.unit or "G"

    if args.i:
        # If -i was given an explicit file, use it directly
        if isinstance(args.i, str):
            meminfo = parse_meminfo(args.i)
            print_simple(meminfo, unit)
            return

        # Detect meminfo-only mode (bare meminfo file passed as positional)
        if os.path.isfile(path) and os.path.basename(path) == "meminfo":
            meminfo = parse_meminfo(path)
            print_simple(meminfo, unit)
            return

        sosroot = path
        meminfo = parse_meminfo(os.path.join(sosroot, "proc/meminfo"))
        tmpfs_used = parse_tmpfs_df(os.path.join(sosroot, "df"))
        _, sysv_rss, _ = parse_sysvipc_shm(os.path.join(sosroot, "proc/sysvipc/shm"))
        print_detailed(meminfo, tmpfs_used, sysv_rss, unit, args.verbose)

    elif args.p:
        if isinstance(args.p, str):
            ps_path = args.p
        elif os.path.isfile(path):
            # positional arg is a file (e.g. sos_commands/process/ps_auxwwwm) — use it directly
            ps_path = path
        else:
            ps_path = os.path.join(path, "ps")
        print_top_procs(ps_path)

    elif args.c:
        if isinstance(args.c, str):
            ps_path = args.c
        elif os.path.isfile(path):
            # positional arg is a file (e.g. sos_commands/process/ps_auxwwwm) — use it directly
            ps_path = path
        else:
            ps_path = os.path.join(path, "ps")
        print_top_cmds(ps_path)

    elif args.u:
        if isinstance(args.u, str):
            ps_path = args.u
        elif os.path.isfile(path):
            # positional arg is a file (e.g. sos_commands/process/ps_auxwwwm) — use it directly
            ps_path = path
        else:
            ps_path = os.path.join(path, "ps")
        print_top_users(ps_path)

    elif args.b:
        if isinstance(args.b, str):
            buddy_path = args.b
        elif os.path.isfile(path) and "buddyinfo" in os.path.basename(path).lower():
            buddy_path = path
        else:
            buddy_path = os.path.join(path, "proc", "buddyinfo")
        use_color = sys.stdout.isatty() and not args.no_color
        print_buddyinfo(
            buddy_path, unit, use_color=use_color, verbose=args.verbose
        )


if __name__ == "__main__":
    main()
