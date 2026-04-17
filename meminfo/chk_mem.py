#!/usr/bin/env python3

import os
import argparse
from typing import Optional, Tuple

def scale_value(kb, unit):
    if unit == "K": return kb
    if unit == "M": return kb / 1024
    if unit == "G": return kb / (1024 * 1024)

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
    parser.add_argument("-v", "--verbose", action="store_true", help="Show formula for unaccounted memory")
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

    args = parser.parse_args()

    # Default to -i mode when none of the mode flags is given
    if not args.i and not args.p and not args.c and not args.u:
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

if __name__ == "__main__":
    main()
