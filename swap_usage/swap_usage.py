#!/usr/bin/env python3

import re

def swap_info():
    with open('proc/meminfo', 'r') as file:
        content = file.read()
        swap_total = re.search(r'SwapTotal:\s+(\d+) kB', content)
        swap_free = re.search(r'SwapFree:\s+(\d+) kB', content)
        shmem = re.search(r'Shmem:\s+(\d+) kB', content)

        swap_total_value = int(swap_total.group(1)) if swap_total else 0
        swap_free_value = int(swap_free.group(1)) if swap_free else 0
        shmem_value = int(shmem.group(1)) if shmem else 0

        return swap_total_value, swap_free_value, shmem_value

def sysv_vss():
    with open('proc/sysvipc/shm', 'r') as file:
        next(file)  # Skip the header line
        sum_vss = sum(int(line.split()[3]) for line in file)
        return sum_vss

def sysv_rss():
    with open('proc/sysvipc/shm', 'r') as file:
        next(file)  # Skip the header line
        sum_rss = sum(int(line.split()[14]) for line in file)
        return sum_rss

def sysv_swapped():
    with open('proc/sysvipc/shm', 'r') as file:
        next(file)  # Skip the header line
        sum_swapped = sum(int(line.split()[15]) for line in file)
        return sum_swapped

def tmpfs():
    with open('df', 'r') as file:
        sum_tmpfs_kb = sum(int(line.split()[2]) for line in file if 'tmpfs' in line)
        return sum_tmpfs_kb

if __name__ == "__main__":
    swap_total_value, swap_free_value, shmem_value = swap_info()
    sum_vss = sysv_vss()
    sum_rss = sysv_rss()
    sum_swapped = sysv_swapped()
    sum_tmpfs_kb = tmpfs()

    # Convert values from bytes to kilobytes and GB
    sum_rss_kb = sum_rss // 1024
    sum_vss_kb = sum_vss // 1024
    sum_swapped_kb = sum_swapped // 1024
    swap_total_gb = swap_total_value / (2**20)
    swap_free_gb = swap_free_value / (2**20)
    shmem_gb = shmem_value / (2**20)
    sum_vss_gb = sum_vss / (2**30)
    sum_rss_gb = sum_rss / (2**30)
    sum_swapped_gb = sum_swapped / (2**30)
    sum_tmpfs_gb = sum_tmpfs_kb / (2**20)

    used_swap = swap_total_value - swap_free_value
    total_shared = sum_rss_kb + sum_tmpfs_kb
    likely_swapped_out_shared = total_shared - shmem_value
    processes_swapped_private_and_shared = used_swap - sum_swapped_kb
    processes_swapped_private = processes_swapped_private_and_shared - likely_swapped_out_shared

    print("=======================================")
    print("           Swap Usage           ")
    print("=======================================\n")

    print("From proc/meminfo:")
    print(f"   SwapTotal: {swap_total_value:>12} KB ({swap_total_gb:>7.2f} GB)")
    print(f"    SwapFree: {swap_free_value:>12} KB ({swap_free_gb:>7.2f} GB)")
    print(f"       Shmem: {shmem_value:>12} KB ({shmem_gb:>7.2f} GB)")

    print("\nFrom proc/sysvipc/shm:")
    print(f"    SysV VSS: {sum_vss_kb:>12} KB ({sum_vss_gb:>7.2f} GB)")
    print(f"    SysV RSS: {sum_rss_kb:>12} KB ({sum_rss_gb:>7.2f} GB)")
    print(f"SysV Swapped: {sum_swapped_kb:>12} KB ({sum_swapped_gb:>7.2f} GB)")

    print("\nFrom df:")
    print(f"       tmpfs: {sum_tmpfs_kb:>12} KB ({sum_tmpfs_gb:>7.2f} GB)")

    print("\nUsing above data:")
    print(f"  SwapTotal - SwapFree = Used Swap")
    print(f"  {swap_total_value} - {swap_free_value} = {used_swap} KB ({used_swap / (2**20):.2f} GB)")

    print(f"\n  SysV RSS + tmpfs = Total shared")
    print(f"  {sum_rss_kb} + {sum_tmpfs_kb} = {total_shared} KB ({total_shared / (2**20):.2f} GB)")

    print(f"\n  Total shared - Shmem = Likely swapped-out shared")
    print(f"  {total_shared} - {shmem_value} = {likely_swapped_out_shared} KB ({likely_swapped_out_shared / (2**20):.2f} GB)")

    print(f"\n  Used Swap - SysV Swapped = private (swapped from processes) + shared (Mostly swapped tmpfs)")
    print(f"  {used_swap} - {sum_swapped_kb} = {processes_swapped_private_and_shared} KB ({processes_swapped_private_and_shared / (2**20):.2f} GB)")

    print(f"\n  private (swapped from processes) + shared (Mostly swapped tmpfs) - Likely swapped-out shared = Likely private (swapped from processes)")
    print(f"  {processes_swapped_private_and_shared} - {likely_swapped_out_shared} = {processes_swapped_private} KB ({processes_swapped_private / (2**20):.2f} GB)")

    print(f"\nThus, Swap usage ({used_swap / (2**20):.2f} GB) would be like: private memory ({processes_swapped_private / (2**20):.2f} GB) + SysV IPC swapped shared ({sum_swapped_gb:.2f} GB) + tmpfs swapped shared ({likely_swapped_out_shared / (2**20):.2f} GB)")
