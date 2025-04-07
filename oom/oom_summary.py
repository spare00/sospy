#!/usr/bin/env python3

import sys
import re
import argparse

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

    # Check if there is a match for Mem-Info in the log
    if not re.search(mem_info_pattern, log_data):
        print("No OOM events found in the log file.")
        return []  # Return an empty list instead of exiting the script

    # Split the log based on Mem-Info sections
    mem_info_sections = re.split(mem_info_pattern, log_data)[1:]

    # Extract timestamps and memory info blocks
    timestamps = mem_info_sections[0::2]
    mem_info_sections = mem_info_sections[1::2]

    mem_info_list = []

    for timestamp, section in zip(timestamps, mem_info_sections):
        # Normalize the timestamp (remove unnecessary kernel info if needed)
        timestamp = re.sub(r'\[\d+\.\d+\]', '', timestamp)  # Handle bracketed timestamps

        # Extract memory information based on predefined patterns
        memory_info = {}
        for key, regex in patterns.items():
            match = re.search(regex, section)
            if match:
                memory_info[key] = int(match.group(1))
            else:
                memory_info[key] = 0  # Default to 0 if key is missing

        # Initialize accumulators for hugepages across nodes
        total_hugepages_memory_kb = 0  # To accumulate total hugepage memory across nodes
        used_hugepages_memory_kb = 0  # To accumulate used hugepage memory across nodes

        # Extract hugepage information for multiple nodes
        node_sections = re.findall(r'Node \d+ (.*)', section)
        for node_section in node_sections:
            hugepages_total = 0
            hugepages_free = 0
            hugepages_size_kb = 0

            # Extract the total, free, and size of hugepages for each node
            match_total = re.search(r'hugepages_total=(\d+)', node_section)
            match_free = re.search(r'hugepages_free=(\d+)', node_section)
            match_size = re.search(r'hugepages_size=(\d+)kB', node_section)

            if match_total and match_free and match_size:
                hugepages_total = int(match_total.group(1))
                hugepages_free = int(match_free.group(1))
                hugepages_size_kb = int(match_size.group(1))

                # Calculate used hugepages for the node
                hugepages_used = hugepages_total - hugepages_free

                # Accumulate totals across nodes
                total_hugepages_memory_kb += hugepages_total * hugepages_size_kb
                used_hugepages_memory_kb += hugepages_used * hugepages_size_kb

        # Use the Mem-Info timestamp as the primary reference for the event
        mem_info_list.append((timestamp, memory_info, total_hugepages_memory_kb, used_hugepages_memory_kb))

    return mem_info_list

def calculate_memory_usage(memory_info, hugepages_total_kb, hugepages_used_kb, show_full, pagesize_kb):
    """Calculate memory usage summary from memory info."""
    page_size_kb = pagesize_kb if pagesize_kb else 4  # Page size in KB
    mb_conversion = lambda x: x * page_size_kb / 1024 if x else 0

    total_memory_pages = memory_info.pop('total_pages_ram', 0)
    total_memory_mb = total_memory_pages * page_size_kb / 1024
    total_memory_gb = total_memory_mb / 1024

    # Convert hugepage memory from KB to MB
    hugepages_total_mb = hugepages_total_kb / 1024
    hugepages_used_mb = hugepages_used_kb / 1024

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

    # Calculate unaccounted memory (subtracting hugepage memory too)
    unaccounted_memory_mb = total_memory_mb - accounted_memory_mb - hugepages_total_mb

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
        'Hugepages Total': (hugepages_total_mb, hugepages_total_mb / 1024, hugepages_total_kb / 4),
        'Hugepages Used': (hugepages_used_mb, hugepages_used_mb / 1024, hugepages_used_kb / 4),
        # Add other memory categories...
    }

    # Add swap usage to the memory summary
    swap_free_pages = memory_info.get('free_swap', 0) // page_size_kb
    swap_total_pages = memory_info.get('total_swap', 0) // page_size_kb
    swap_used_pages = swap_total_pages - swap_free_pages

    swap_free_mb = mb_conversion(swap_free_pages)
    swap_total_mb = mb_conversion(swap_total_pages)
    swap_used_mb = mb_conversion(swap_used_pages)

    # Add swap usage to the memory summary
    memory_summary.update({
        'Swap Total': (swap_total_mb, swap_total_mb / 1024, swap_total_pages),
        'Swap Free': (swap_free_mb, swap_free_mb / 1024, swap_free_pages),
        'Swap Used': (swap_used_mb, swap_used_mb / 1024, swap_used_pages),
    })

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

    unaccounted_memory_mb = unaccounted_memory_mb - mb_conversion(memory_info['isolated_anon']) \
                                                  - mb_conversion(memory_info['isolated_file']) \
                                                  - mb_conversion(memory_info['unevictable']) \
                                                  - mb_conversion(memory_info['dirty']) \
                                                  - mb_conversion(memory_info['writeback']) \
                                                  - mb_conversion(memory_info['mapped']) \
                                                  - mb_conversion(memory_info['bounce']) \
                                                  - mb_conversion(memory_info['free_cma'])

    # Return memory summary, total memory details, etc.
    return memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, unaccounted_memory_mb

def print_summary(memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, unaccounted_memory_mb, timestamp, show_in_page, show_in_gb, show_unaccounted, show_in_mb=True):
    """Prints the memory summary in a formatted table and displays the total memory size at the bottom."""
    # Only show the Timestamp, without any additional "Event" lines.
    header = f"\nTimestamp: {timestamp}"
    header += f"\n{'Category':<25}"
    lines = 25

    if show_in_page:
        header += f" {'Pages':>15}"
        lines +=16
    if show_in_mb:
        header += f" {'MB':>15}"
        lines +=16
    if show_in_gb:
        header += f" {'GB':>10}"
        lines +=11

    header += f"\n{'='*lines}"
    print(header)

    for key, (mb, gb, pages) in memory_summary.items():
        body = f"{key:<25}"
        if show_in_page:
            body += f" {pages:>15,}"
        if show_in_mb:
            body += f"{mb:>15,.2f} "
        if show_in_gb:
            body += f" {gb:>10,.2f}"
        print(body)

    # Print the unaccounted memory if the -u flag is provided
    if show_unaccounted:
        body_unaccounted = f"{'-'*lines}\n{'Unaccounted Memory':<25}"
        if show_in_page:
            body_unaccounted += f" {'':>15}"
        if show_in_mb:
            body_unaccounted += f" {unaccounted_memory_mb:>15,.2f}"
        if show_in_gb:
            body_unaccounted += f" {unaccounted_memory_mb/1024:>10,.2f}"
        print(body_unaccounted)

    # Print the total memory size at the bottom
    tail = f"{'Total Memory':<25}"
    if show_in_page:
        tail += f" {total_memory_pages:>15,}"
    if show_in_mb:
        tail += f" {total_memory_mb:>15,.2f}"
    if show_in_gb:
        tail += f" {total_memory_gb:>10,.2f}"
    tail = f"{'='*lines}\n" + tail
    print(tail)

def main():
    # Use argparse for flexible option parsing
    parser = argparse.ArgumentParser(description="Parse OOM logs and display memory summaries.")

    # Define the flags
    parser.add_argument('-p', '--page', action='store_true', help="Show memory usage in page.")
    parser.add_argument('-g', '--gigabyte', action='store_true', help="Show memory usage in GB.")
    parser.add_argument('-m', '--megabyte', action='store_true', help="Show memory usage in MB.")
    parser.add_argument('-u', '--unaccounted', action='store_true', help="Show unaccounted memory.")
    parser.add_argument('-f', '--full', action='store_true', help="Show full memory info.")
    parser.add_argument('-s', '--pagesize', metavar='SIZE_KB', type=int, default=4, help="Set custom page size in KB (default: 4 KB).")

    # Define the positional argument for log file name
    parser.add_argument('log_filename', metavar='log_filename', type=str, help="Log file to parse.")

    # Parse arguments
    args = parser.parse_args()
    pagesize_kb = args.pagesize

    show_in_page = args.page
    show_in_gb = args.gigabyte
    show_in_mb = True if not args.megabyte and not show_in_gb and not show_in_page else args.megabyte
    show_unaccounted = args.unaccounted
    show_full = args.full
    log_filename = args.log_filename

    # Read and parse the log file
    log_data = parse_log_file(log_filename)
    mem_info_list = extract_memory_info(log_data)

    # Iterate over each memory event in the log file
    for timestamp, memory_info, total_hugepages_kb, used_hugepages_kb in mem_info_list:
        # Calculate memory usage, now including hugepage memory
        memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, unaccounted_memory_mb = calculate_memory_usage(
            memory_info, total_hugepages_kb, used_hugepages_kb, show_full, pagesize_kb)

        # Print memory summary for each event
        print_summary(memory_summary, total_memory_mb, total_memory_gb, total_memory_pages,
                      unaccounted_memory_mb, timestamp, show_in_page, show_in_gb, show_unaccounted,
                      show_in_mb)

if __name__ == "__main__":
    main()

