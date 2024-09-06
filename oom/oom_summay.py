#!/usr/bin/env python3

import sys
import re
from config import patterns, mem_info_pattern, oom_pattern

def parse_log_file(filename):
    """Reads the log file and returns its contents as a string."""
    try:
        with open(filename, 'r') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        sys.exit(1)

    return mem_info_list

def extract_memory_info(log_data):
    """Extracts memory information and timestamps from different log patterns."""

    if re.search(mem_info_pattern, log_data):
        mem_info_sections = re.split(mem_info_pattern, log_data)[1:]
    else:
        print("Error: Unknown log format.")
        sys.exit(1)

    # Extract timestamps and memory info blocks
    timestamps = mem_info_sections[0::2]
    mem_info_sections = mem_info_sections[1::2]

    mem_info_list = []

    for timestamp, section in zip(timestamps, mem_info_sections):
        # Normalize the timestamp (remove unnecessary kernel info if needed)
        if "kernel:" in timestamp:
            timestamp = re.sub(r'kernel:.*Mem-Info:', '', timestamp).strip()

        # Extract memory information based on predefined patterns
        memory_info = {}
        for key, regex in patterns.items():
            match = re.search(regex, section)
            if match:
                memory_info[key] = int(match.group(1))
            else:
                memory_info[key] = 0  # Default to 0 if key is missing

        # Try to match the OOM invocation line
        oom_invocation_line = re.search(oom_pattern, section)
        oom_invocation_line = oom_invocation_line.group(1) if oom_invocation_line else ""

        mem_info_list.append((timestamp, oom_invocation_line, memory_info))

    return mem_info_list

def calculate_memory_usage(memory_info, show_full):
    """Calculate memory usage summary from memory info."""
    page_size_kb = 4  # Page size in KB
    mb_conversion = lambda x: x * page_size_kb / 1024 if x else 0

    # Extract total memory and remove it from the main summary
    total_memory_pages = memory_info.pop('total_pages_ram', 0)
    total_memory_mb = total_memory_pages * page_size_kb / 1024
    total_memory_gb = total_memory_mb / 1024

    # Handle pagecache correctly by avoiding double-counting
    pagecache_mb = mb_conversion(memory_info.get('pagecache', 0))
    accounted_memory_mb = pagecache_mb + mb_conversion(memory_info.get('shmem', 0)) + \
                          mb_conversion(memory_info.get('slab_reclaimable', 0)) + \
                          mb_conversion(memory_info.get('slab_unreclaimable', 0)) + \
                          mb_conversion(memory_info.get('pagetables', 0)) + \
                          mb_conversion(memory_info.get('free', 0)) + \
                          mb_conversion(memory_info.get('free_pcp', 0)) + \
                          mb_conversion(memory_info.get('reserved', 0)) + \
                          mb_conversion(memory_info.get('active_anon', 0)) + \
                          mb_conversion(memory_info.get('inactive_anon', 0))

    # Calculate unaccounted memory
    unaccounted_memory_mb = total_memory_mb - accounted_memory_mb

    memory_summary = {
        'Active Anon': (mb_conversion(memory_info['active_anon']), mb_conversion(memory_info['active_anon']) / 1024, memory_info['active_anon']),
        'Inactive Anon': (mb_conversion(memory_info['inactive_anon']), mb_conversion(memory_info['inactive_anon']) / 1024, memory_info['inactive_anon']),
        'Active File': (mb_conversion(memory_info['active_file']), mb_conversion(memory_info['active_file']) / 1024, memory_info['active_file']),
        'Inactive File': (mb_conversion(memory_info['inactive_file']), mb_conversion(memory_info['inactive_file']) / 1024, memory_info['inactive_file']),
        'Slab Reclaimable': (mb_conversion(memory_info['slab_reclaimable']), mb_conversion(memory_info['slab_reclaimable']) / 1024, memory_info['slab_reclaimable']),
        'Slab Unreclaimable': (mb_conversion(memory_info['slab_unreclaimable']), mb_conversion(memory_info['slab_unreclaimable']) / 1024, memory_info['slab_unreclaimable']),
        'Shmem': (mb_conversion(memory_info['shmem']), mb_conversion(memory_info['shmem']) / 1024, memory_info['shmem']),
        'Pagetables': (mb_conversion(memory_info['pagetables']), mb_conversion(memory_info['pagetables']) / 1024, memory_info['pagetables']),
        'Free': (mb_conversion(memory_info['free']), mb_conversion(memory_info['free']) / 1024, memory_info['free']),
        'Free Pcp': (mb_conversion(memory_info['free_pcp']), mb_conversion(memory_info['free_pcp']) / 1024, memory_info['free_pcp']),
        'Pagecache': (pagecache_mb, pagecache_mb / 1024, memory_info['pagecache']),
        'Reserved': (mb_conversion(memory_info['reserved']), mb_conversion(memory_info['reserved']) / 1024, memory_info['reserved']),
    }

    # Include additional fields if show_full is True
    if show_full:
        memory_summary.update({
            'Isolated Anon': (mb_conversion(memory_info['isolated_anon']), mb_conversion(memory_info['isolated_anon']) / 1024, memory_info['isolated_anon']),
            'Isolated File': (mb_conversion(memory_info['isolated_file']), mb_conversion(memory_info['isolated_file']) / 1024, memory_info['isolated_file']),
            'Unevictable': (mb_conversion(memory_info['unevictable']), mb_conversion(memory_info['unevictable']) / 1024, memory_info['unevictable']),
            'Dirty': (mb_conversion(memory_info['dirty']), mb_conversion(memory_info['dirty']) / 1024, memory_info['dirty']),
            'Writeback': (mb_conversion(memory_info['writeback']), mb_conversion(memory_info['writeback']) / 1024, memory_info['writeback']),
            'Mapped': (mb_conversion(memory_info['mapped']), mb_conversion(memory_info['mapped']) / 1024, memory_info['mapped']),
            'Bounce': (mb_conversion(memory_info['bounce']), mb_conversion(memory_info['bounce']) / 1024, memory_info['bounce']),
            'Free CMA': (mb_conversion(memory_info['free_cma']), mb_conversion(memory_info['free_cma']) / 1024, memory_info['free_cma']),
        })

    return memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, unaccounted_memory_mb

def print_summary(memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, unaccounted_memory_mb, timestamp, oom_invocation_line, show_pages, show_unaccounted):
    """Prints the memory summary in a formatted table and displays the total memory size at the bottom."""
    header = f"\nTimestamp: {timestamp}"
    if oom_invocation_line:
        header += f"\nEvent: {oom_invocation_line}"
    if show_pages:
        header += f"\n{'Category':<25} {'Pages':>15} {'MB':>15} {'GB':>10}\n{'='*68}"
    else:
        header += f"\n{'Category':<25} {'MB':>15} {'GB':>10}\n{'='*52}"

    print(header)

    for key, (mb, gb, pages) in memory_summary.items():
        if show_pages:
            print(f"{key:<25} {pages:>15,} {mb:>15,.2f} {gb:>10,.2f}")
        else:
            print(f"{key:<25} {mb:>15,.2f} {gb:>10,.2f}")

    # Print the unaccounted memory if the -u flag is provided
    if show_unaccounted:
        print(f"{'-'*68}")
        if show_pages:
            print(f"{'Unaccounted Memory':<25} {'':>15} {unaccounted_memory_mb:>15,.2f} {unaccounted_memory_mb/1024:>10,.2f}")
        else:
            print(f"{'Unaccounted Memory':<25} {unaccounted_memory_mb:>15,.2f} {unaccounted_memory_mb/1024:>10,.2f}")

    # Print the total memory size at the bottom
    if show_pages:
        print(f"{'-'*68}")
        print(f"{'Total Memory':<25} {total_memory_pages:>15,} {total_memory_mb:>15,.2f} {total_memory_gb:>10,.2f}")
    else:
        print(f"{'-'*52}")
        print(f"{'Total Memory':<25} {total_memory_mb:>15,.2f} {total_memory_gb:>10,.2f}")
    print("\n")

def main():
    if len(sys.argv) < 2 or len(sys.argv) > 4:
        print("Usage: oom_summary.py [-p|-u|-f] <log_filename>")
        sys.exit(1)

    show_pages = '-p' in sys.argv
    show_unaccounted = '-u' in sys.argv
    show_full = '-f' in sys.argv
    log_filename = sys.argv[-1]

    log_data = parse_log_file(log_filename)
    mem_info_list = extract_memory_info(log_data)

    for timestamp, oom_invocation_line, memory_info in mem_info_list:
        memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, unaccounted_memory_mb = calculate_memory_usage(memory_info, show_full)
        print_summary(memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, unaccounted_memory_mb, timestamp, oom_invocation_line, show_pages, show_unaccounted)

if __name__ == "__main__":
    main()
