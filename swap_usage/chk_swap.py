#!/usr/bin/env python3
import os
import sys
import argparse
from typing import Optional, Tuple

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
    return vss // 1024, rss // 1024, swapped // 1024  # convert to KB

def parse_df(path: str) -> Optional[int]:
    try:
        with open(path) as f:
            used_total = 0
            header = f.readline()
            for line in f:
                parts = line.split()
                if len(parts) >= 6 and "tmpfs" in parts[0] and parts[5] != "/dev":
                    used_total += int(parts[2])  # "Used" column
            return used_total
    except FileNotFoundError:
        return None

def main(sosroot: Optional[str] = None, verbose: bool = False) -> None:
    if sosroot is None:
        sosroot = "."

    print("=======================================")
    print("         Swap Usage (sosreport)")
    print("=======================================\n")

    # ---- parse /proc/meminfo ----
    meminfo_path = os.path.join(sosroot, "proc/meminfo")
    meminfo = parse_meminfo(meminfo_path)

    swap_total = meminfo.get("SwapTotal", 0)
    swap_free  = meminfo.get("SwapFree", 0)
    swap_used  = swap_total - swap_free
    shmem      = meminfo.get("Shmem", 0)
    huge_total = meminfo.get("HugePages_Total", 0)
    huge_free  = meminfo.get("HugePages_Free", 0)
    huge_size  = meminfo.get("Hugepagesize", 0)
    huge_used  = (huge_total - huge_free) * huge_size

    print("From proc/meminfo:")
    print(f"  SwapTotal : {swap_total:12,} KB ({kb_gb(swap_total)})")
    print(f"  SwapFree  : {swap_free:12,} KB ({kb_gb(swap_free)})")
    print(f"  SwapUsed  : {swap_used:12,} KB ({kb_gb(swap_used)})")
    print(f"  Shmem     : {shmem:12,} KB ({kb_gb(shmem)})")
    print(f"  HugePages_Total: {huge_total:12,} pages")
    print(f"  HugePages_Free : {huge_free:12,} pages")
    print(f"  Hugepagesize   : {huge_size:12,} KB")
    print(f"  Hugepages Used : {huge_used:12,} KB ({kb_gb(huge_used)})\n")

    # ---- parse df ----
    df_path = os.path.join(sosroot, "df")
    tmpfs_used = parse_df(df_path)
    print("From df (-kP):")
    print(f"  tmpfs Used : {tmpfs_used:12,} KB ({kb_gb(tmpfs_used)})\n")

    # ---- parse shm ----
    shm_path = os.path.join(sosroot, "proc/sysvipc/shm")
    sysv_vss, sysv_rss, sysv_swapped = parse_shm(shm_path)
    print("From proc/sysvipc/shm:")
    print(f"  SysV VSS     : {sysv_vss:12,} KB ({kb_gb(sysv_vss)})")
    print(f"  SysV RSS     : {sysv_rss:12,} KB ({kb_gb(sysv_rss)})")
    print(f"  SysV Swapped : {sysv_swapped:12,} KB ({kb_gb(sysv_swapped)})\n")

    # ---- calculations ----
    likely_swapped_shared = (sysv_rss - huge_used + tmpfs_used) - shmem
    est_swapped_shared = sysv_swapped + likely_swapped_shared
    residual_private = swap_used - est_swapped_shared

    print("Heuristic (practical) inference:")
    print(f"  Likely swapped shared ((SysV RSS - hugetlb + tmpfs) - Shmem):"
          f"{likely_swapped_shared:>15,} KB ({kb_gb(likely_swapped_shared):>6})")
    if verbose:
        print(f"    {sysv_rss} - {huge_used} + {tmpfs_used} - {shmem}")

    print(f"  Estimated swapped shared (SysV swapped + Likely swapped shared):"
          f"{est_swapped_shared:>12,} KB ({kb_gb(est_swapped_shared):>6})")
    if verbose:
        print(f"    {sysv_swapped} + {likely_swapped_shared}")

    print(f"  Residual swapped (Used swap - Estimated swapped shared):"
          f"{residual_private:>20,} KB ({kb_gb(residual_private):>6})")
    if verbose:
        print(f"    {swap_used} - {est_swapped_shared}")

    print("\nThus, Swap usage "
          f"({kb_gb(swap_used)}) would be like: private memory "
          f"({kb_gb(residual_private)}) + shared memory "
          f"(System V IPC: {kb_gb(sysv_swapped)} + tmpfs: {kb_gb(tmpfs_used)})")

    # ---- diagnostics ----
    explained = (sysv_rss - huge_used + tmpfs_used)
    diag_gap = shmem - explained
    if diag_gap > 0:
        print("\n[Diag] Shmem exceeds tmpfs+SysV explained share by:")
        print(f"       {diag_gap:12,} KB ({kb_gb(diag_gap)})")
        print("       This may include memfd, shm_open, or other shared mappings.")

    print("\nNotes:")
    print("- Shmem is resident only; swapped pages are not included there.")
    print("- tmpfs 'Used' is filesystem allocation (resident + swapped).")
    print("- The 'likely swapped-out shared' is a pragmatic estimate of swapped shared pages.")
    print("- SysV hugetlb is not swappable and not accounted in Shmem; we do not add SysV sizes.")
    print("- Without per-process VmSwap, private vs shared split remains approximate.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sosroot", nargs="?", help="sosreport root (default: cwd)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    main(args.sosroot, verbose=args.verbose)

