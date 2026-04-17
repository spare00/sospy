#!/usr/bin/env python3
import os
import sys
import argparse
from typing import List, Optional, Tuple

def kb_gb(kb: int) -> str:
    return f"{kb/1024/1024:4.2f} GB"

def parse_meminfo(path: str) -> dict:
    out = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    out[parts[0].rstrip(':')] = int(parts[1])
                elif parts[0].endswith(":"):
                    out[parts[0].rstrip(':')] = int(parts[1])
    except FileNotFoundError:
        pass
    return out

def parse_shm(path: str) -> Tuple[int, int, int]:
    vss = rss = swapped = 0
    try:
        with open(path) as f:
            header = f.readline().split()
            idx_size = header.index("size")
            idx_rss = header.index("rss")
            idx_swap = header.index("swap")
            for line in f:
                parts = line.split()
                if len(parts) > idx_swap:
                    vss += int(parts[idx_size])
                    rss += int(parts[idx_rss])
                    swapped += int(parts[idx_swap])
    except FileNotFoundError:
        pass
    return vss // 1024, rss // 1024, swapped // 1024

def parse_df(path: str) -> Optional[int]:
    try:
        with open(path) as f:
            used_total = 0
            header = f.readline()
            for line in f:
                parts = line.split()
                if len(parts) >= 6 and "tmpfs" in parts[0] and parts[5] != "/dev":
                    used_total += int(parts[2])
            return used_total
    except FileNotFoundError:
        return None

def is_cgroup_v2(sosroot: str) -> bool:
    return os.path.exists(os.path.join(sosroot, "sys/fs/cgroup/cgroup.controllers"))

def collect_cgroup_swap_usage(sosroot: str) -> List[Tuple[str, int]]:
    cgroup_root = os.path.join(sosroot, "sys/fs/cgroup")
    usage = []

    for root, _, files in os.walk(cgroup_root):
        if "memory.swap.current" not in files:
            continue

        path = os.path.join(root, "memory.swap.current")
        try:
            with open(path) as f:
                value = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            continue

        relpath = os.path.relpath(path, sosroot)
        usage.append((relpath, value))

    usage.sort(key=lambda item: item[1], reverse=True)
    return usage

def print_cgroup_swap_usage(sosroot: str, limit: int = 10) -> None:
    print("\nFrom cgroup v2 memory.swap.current:")

    if not is_cgroup_v2(sosroot):
        print("  cgroup v2 not detected; per-cgroup swap usage is unavailable.")
        return

    usage = collect_cgroup_swap_usage(sosroot)
    if not usage:
        print("  No memory.swap.current files found under sys/fs/cgroup.")
        return

    total = sum(value for _, value in usage)
    top_usage = usage[:limit]

    for path, value in top_usage:
        print(f"  {path}:{value}")

    print(f"\n  Sum of all memory.swap.current: {total:,} bytes ({total / 1024 / 1024 / 1024:4.2f} GB)")

def main(sosroot: Optional[str] = None, verbose: bool = False, show_cgroup: bool = False) -> None:
    if sosroot is None:
        sosroot = "."

    print("=======================================")
    print("         Swap Usage (sosreport)")
    print("=======================================\n")

    meminfo_path = os.path.join(sosroot, "proc/meminfo")
    meminfo = parse_meminfo(meminfo_path)

    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = swap_total - swap_free
    shmem = meminfo.get("Shmem", 0)
    huge_total = meminfo.get("HugePages_Total", 0)
    huge_free = meminfo.get("HugePages_Free", 0)
    huge_size = meminfo.get("Hugepagesize", 0)
    huge_reserved = huge_total * huge_size
    huge_used = (huge_total - huge_free) * huge_size
    huge_total_kb = huge_reserved
    huge_free_kb = huge_free * huge_size

    print("From proc/meminfo:")
    print(f"  SwapTotal      : {swap_total:12,} KB ({kb_gb(swap_total)})")
    print(f"  SwapFree       : {swap_free:12,} KB ({kb_gb(swap_free)})")
    print(f"  SwapUsed       : {swap_used:12,} KB ({kb_gb(swap_used)})")
    print(f"  Shmem          : {shmem:12,} KB ({kb_gb(shmem)})")
    print(f"  HugePages_Total: {huge_total:12,} pages ({kb_gb(huge_total_kb)})")
    print(f"  HugePages_Free : {huge_free:12,} pages ({kb_gb(huge_free_kb)})")
    print(f"  Hugepagesize   : {huge_size:12,} KB")
    print(f"  Hugepages Used : {huge_used:12,} KB ({kb_gb(huge_used)})\n")

    df_path = os.path.join(sosroot, "df")
    tmpfs_used = parse_df(df_path)
    print("From df (-kP):")
    print(f"  tmpfs Used : {tmpfs_used:12,} KB ({kb_gb(tmpfs_used)})\n")

    shm_path = os.path.join(sosroot, "proc/sysvipc/shm")
    sysv_vss, sysv_rss, sysv_swapped = parse_shm(shm_path)
    print("From proc/sysvipc/shm:")
    print(f"  SysV VSS     : {sysv_vss:12,} KB ({kb_gb(sysv_vss)})")
    print(f"  SysV RSS     : {sysv_rss:12,} KB ({kb_gb(sysv_rss)})")
    print(f"  SysV Swapped : {sysv_swapped:12,} KB ({kb_gb(sysv_swapped)})")

    # ------------------------------
    # Adjust SysV RSS for hugepages
    # ------------------------------
    sysv_rss_corrected = sysv_rss
    if huge_used > 0:
        print(f"\n⚠️  Detected HugeTLB used ({kb_gb(huge_used)}) — adjusting SysV RSS to exclude full reservation ({kb_gb(huge_reserved)}).")
        sysv_rss_corrected = max(0, sysv_rss - huge_reserved)
        print(f"   → Corrected SysV RSS (swappable): {sysv_rss_corrected:,} KB ({kb_gb(sysv_rss_corrected)})")
    elif huge_reserved > 0:
        print(f"\n⚠️  HugeTLB is reserved ({kb_gb(huge_reserved)}) but not used (used=0 KB) — skipping adjustment to SysV RSS.")
        print(f"   → Assuming SysV shared memory is not backed by HugeTLB.")
    else:
        print(f"\n  → SysV RSS considered swappable: {sysv_rss:,} KB ({kb_gb(sysv_rss)})")

    s_total = swap_used
    s_sysv = sysv_swapped
    r_sysv = sysv_rss_corrected
    t_used = tmpfs_used
    shared_total_named = r_sysv + t_used
    shared_resident = shmem

    shared_swapped_total = 0
    shared_anon = 0
    anon_swapped = 0
    shared_swapped_tmpfs = 0

    print("\n[Classification & Analysis]")

    if shared_resident < shared_total_named:
        shared_swapped_total = shared_total_named - shared_resident

        if shared_swapped_total < s_sysv:
            print(f"⚠️  SysV swapped ({kb_gb(s_sysv)}) exceeds estimated shared swapped ({kb_gb(shared_swapped_total)})")
            print(f"    → Adjusting shared swapped total = SysV swapped, and tmpfs swapped = 0")
            shared_swapped_total = s_sysv
            shared_swapped_tmpfs = 0
        else:
            shared_swapped_tmpfs = shared_swapped_total - s_sysv

        anon_swapped = max(0, s_total - s_sysv - shared_swapped_tmpfs)

        print(f"Case A: Shmem < SysV + tmpfs (named shared)")
        print(f"  → Shared swapped total: {shared_swapped_total:,} KB ({kb_gb(shared_swapped_total)})")
        print(f"     ├─ SysV swapped: {s_sysv:,} KB ({kb_gb(s_sysv)})")
        print(f"     └─ tmpfs swapped (estimated): {shared_swapped_tmpfs:,} KB ({kb_gb(shared_swapped_tmpfs)})")
        print(f"  → Anonymous swapped (private): {anon_swapped:,} KB ({kb_gb(anon_swapped)})")

    elif shared_resident > shared_total_named:
        shared_anon = shared_resident - shared_total_named
        anon_swapped = max(0, s_total - s_sysv)

        print(f"Case B: Shmem > SysV + tmpfs")
        print(f"  → Shared anonymous resident (MAP_SHARED|MAP_ANON): {shared_anon:,} KB ({kb_gb(shared_anon)})")
        print(f"  → Named shared fully resident, likely no tmpfs swapped.")
        print(f"  → Anonymous swapped (private): {anon_swapped:,} KB ({kb_gb(anon_swapped)})")

    else:
        anon_swapped = max(0, s_total - s_sysv)
        print(f"Case C: Shmem == SysV + tmpfs")
        print(f"  → All named shared resident, no shared anonymous detected.")
        print(f"  → Anonymous swapped (private): {anon_swapped:,} KB ({kb_gb(anon_swapped)})")

    print("\nConclusion:")
    print(f"Swap usage ({kb_gb(s_total)}) ≈ "
          f"anon swapped ({kb_gb(anon_swapped)}) + "
          f"shared swapped (SysV: {kb_gb(s_sysv)}, tmpfs: {kb_gb(shared_swapped_tmpfs)})")

    if show_cgroup:
        print_cgroup_swap_usage(sosroot)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sosroot", nargs="?", help="sosreport root (default: cwd)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--cgroup", action="store_true",
                    help="show top 10 cgroup v2 memory.swap.current entries and their total")
    args = ap.parse_args()
    main(args.sosroot, verbose=args.verbose, show_cgroup=args.cgroup)
