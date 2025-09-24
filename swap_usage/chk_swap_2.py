#!/usr/bin/env python3
import os
import sys

def kb_gb(kb: int) -> str:
    """Format KB with both KB and GB."""
    return f"{kb:12,d} KB ({kb/1048576:8.2f} GB)"

def parse_meminfo(path):
    vals = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            key = parts[0].rstrip(':')
            if key in ["SwapTotal", "SwapFree", "Shmem",
                       "HugePages_Total", "HugePages_Free", "Hugepagesize"]:
                vals[key] = int(parts[1])
    swap_total = vals.get("SwapTotal", 0)
    swap_free = vals.get("SwapFree", 0)
    shmem = vals.get("Shmem", 0)
    hp_total = vals.get("HugePages_Total", 0)
    hp_free = vals.get("HugePages_Free", 0)
    hp_size = vals.get("Hugepagesize", 0)
    hp_used = (hp_total - hp_free) * hp_size
    return swap_total, swap_free, shmem, hp_total, hp_free, hp_size, hp_used

def parse_sysvipc_shm(path):
    vss = rss = swapped = 0
    with open(path) as f:
        header = True
        for line in f:
            if header:
                header = False
                continue
            cols = line.split()
            if len(cols) < 16:
                continue
            try:
                vss      += int(cols[3])    // 1024  # size
                rss      += int(cols[14])   // 1024  # rss
                swapped  += int(cols[15])   // 1024  # swap
            except ValueError:
                continue
    return vss, rss, swapped

def parse_df(path):
    tmpfs_used = 0
    with open(path) as f:
        header = True
        for line in f:
            if header:
                header = False
                continue
            parts = line.split()
            if len(parts) < 6:
                continue
            fs, blocks, used, avail, usep, mount = parts[:6]
            if fs == "tmpfs" and mount not in ["/sys/fs/cgroup"]:
                tmpfs_used += int(used)
    return tmpfs_used

def main(sosroot):
    meminfo_path = os.path.join(sosroot, "proc/meminfo")
    shm_path     = os.path.join(sosroot, "proc/sysvipc/shm")
    df_path      = os.path.join(sosroot, "df")

    swap_total, swap_free, shmem, hp_total, hp_free, hp_size, hp_used = parse_meminfo(meminfo_path)
    sysv_vss, sysv_rss, sysv_swapped = parse_sysvipc_shm(shm_path) if os.path.exists(shm_path) else (0,0,0)
    tmpfs_used = parse_df(df_path) if os.path.exists(df_path) else 0

    swap_used = swap_total - swap_free

    print("=======================================")
    print("         Swap Usage (sosreport)")
    print("=======================================\n")

    print("From proc/meminfo:")
    print(f"  SwapTotal : {kb_gb(swap_total)}")
    print(f"  SwapFree  : {kb_gb(swap_free)}")
    print(f"  SwapUsed  : {kb_gb(swap_used)}")
    print(f"  Shmem     : {kb_gb(shmem)}")
    print(f"  HugePages_Total: {hp_total:10,d} pages")
    print(f"  HugePages_Free : {hp_free:10,d} pages")
    print(f"  Hugepagesize   : {hp_size:10,d} KB")
    print(f"  Hugepages Used : {kb_gb(hp_used)}\n")

    print("From df (-kP):")
    print(f"  tmpfs Used : {kb_gb(tmpfs_used)}\n")

    print("From proc/sysvipc/shm:")
    print(f"  SysV VSS     : {kb_gb(sysv_vss)}")
    print(f"  SysV RSS     : {kb_gb(sysv_rss)}")
    print(f"  SysV Swapped : {kb_gb(sysv_swapped)}\n")

    # Heuristic inference
    tmpfs_gap = max(0, tmpfs_used - shmem)
    est_shared_swapped = min(swap_used, tmpfs_gap)
    residual_private = max(0, swap_used - est_shared_swapped)

    print("Heuristic (practical) inference:")
    print(f"  tmpfs gap (tmpfs_used - Shmem): {kb_gb(tmpfs_gap)}")
    print(f"  ⇒ Estimated swapped shared (capped by UsedSwap): {kb_gb(est_shared_swapped)}")
    print(f"  ⇒ Residual (likely private anon swapped): {kb_gb(residual_private)}")

    # SysV fallback diag
    if sysv_swapped == 0 and sysv_vss and sysv_rss:
        fallback = max(0, (sysv_vss - hp_used) - (sysv_rss - hp_used))
        if fallback:
            print(f"\n[Note] SysV 'swap' column is 0; fallback estimate from (VSS–HP)–(RSS–HP): {kb_gb(fallback)}")

    # Diag: Shmem larger than tmpfs+SysV
    if shmem > (tmpfs_used + sysv_rss):
        gap = shmem - (tmpfs_used + sysv_rss)
        print("\n[Diag] Shmem exceeds tmpfs+SysV explained share by:")
        print(f"       {kb_gb(gap)}")
        print("       This may include memfd, shm_open, or other shared mappings.")

    # Notes
    print("\nNotes:")
    print("- Shmem is resident only; swapped pages are not included there.")
    print("- tmpfs 'Used' is filesystem allocation (resident + swapped).")
    print("- The 'tmpfs gap' is a pragmatic estimate of swapped shared pages.")
    print("- SysV hugetlb is not swappable and not accounted in Shmem; we do not add SysV sizes.")
    print("- Without per-process VmSwap, private vs shared split remains approximate.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <sosreport-root>")
        sys.exit(1)
    main(sys.argv[1])

