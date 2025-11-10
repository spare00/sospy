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
            _ = f.readline()
            for line in f:
                parts = line.split()
                if len(parts) >= 6 and "tmpfs" in parts[0] and parts[5] != "/dev":
                    used_total += int(parts[2])
            return used_total
    except FileNotFoundError:
        return None

def calculate_unaccounted(meminfo):
    total = meminfo.get("MemTotal", 0)
    fields = [
        "MemFree", "Buffers", "Cached", "SwapCached",
        "AnonPages", "Slab", "KernelStack",
        "PageTables", "Percpu", "Hugetlb"
    ]
    accounted_sum = sum(meminfo.get(field, 0) for field in fields)
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
    anon_shared_kb = max((active_anon + inactive_anon) - meminfo.get("AnonPages", 0), 0)
    anon_extra_text = f"anon shared={scale_value(anon_shared_kb, unit):.2f} {unit_label}"

    huge_total = meminfo.get("HugePages_Total", 0)
    huge_free = meminfo.get("HugePages_Free", 0)
    huge_size = meminfo.get("Hugepagesize", 0)
    huge_total_kb = huge_total * huge_size
    huge_used_kb = (huge_total - huge_free) * huge_size

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
    anon_shared_kb = max((active_anon + inactive_anon) - meminfo.get("AnonPages", 0), 0)
    anon_extra_text = f"anon shared={scale_value(anon_shared_kb, unit):.2f} {unit_label}"

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
    show("HugePagesUsed", hugetlb_used_kb)

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
        values = [scale_value(meminfo.get(f, 0), unit) for f in fields]
        expr = " - ".join(f"{v:.2f}" for v in values)
        result = scale_value(unaccounted, unit)
        print(f"  {result:.2f} = {total_val:.2f} - {expr}\n")

    show("Unaccounted:", unaccounted, unit_label)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-K", action="store_const", const="K", dest="unit", help="Show output in KiB")
    parser.add_argument("-M", action="store_const", const="M", dest="unit", help="Show output in MiB")
    parser.add_argument("-G", action="store_const", const="G", dest="unit", help="Show output in GiB")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show formula for unaccounted memory")
    parser.add_argument("path", nargs="?", help="sosreport root or /proc/meminfo file (default: cwd)")
    args = parser.parse_args()

    path = args.path or "."
    unit = args.unit or "G"

    # Detect meminfo-only mode
    if os.path.isfile(path) and os.path.basename(path) == "meminfo":
        meminfo = parse_meminfo(path)
        print_simple(meminfo, unit)
        return

    sosroot = path
    meminfo = parse_meminfo(os.path.join(sosroot, "proc/meminfo"))
    tmpfs_used = parse_tmpfs_df(os.path.join(sosroot, "df"))
    _, sysv_rss, _ = parse_sysvipc_shm(os.path.join(sosroot, "proc/sysvipc/shm"))
    print_detailed(meminfo, tmpfs_used, sysv_rss, unit, args.verbose)

if __name__ == "__main__":
    main()
