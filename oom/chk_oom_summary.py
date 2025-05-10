#!/usr/bin/env python3

import sys
import re
import argparse

from config import patterns, mem_info_pattern, oom_pattern

def scale_value(value, from_unit="pages", to_unit="M", pagesize_kb=4):
    """
    Convert a memory value from pages or KB to the desired unit.
    - from_unit: "pages" or "K"
    - to_unit: "K", "M", "G"
    """
    kb = value * pagesize_kb if from_unit == "pages" else value
    if to_unit == "K":
        return kb
    elif to_unit == "M":
        return kb / 1024
    elif to_unit == "G":
        return kb / (1024 * 1024)
    return kb


def parse_log_file(file_path):
    """Reads the log file and returns its contents as a string."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
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

def calculate_memory_usage(memory_info, hugepages_total_kb, hugepages_used_kb,
                           show_full, unit='M', pagesize_kb=4, verbose=False):
    """Calculate memory usage summary from memory info."""

    total_memory_pages = memory_info.get('total_pages_ram', 0)
    # Convert hugepage memory from KB to MB
    hugepages_total_pages = hugepages_total_kb / pagesize_kb
    hugepages_used_pages = hugepages_used_kb / pagesize_kb
    memory_summary = {
        'Active Anon': memory_info['active_anon'],
        'Inactive Anon': memory_info['inactive_anon'],
        'Active File': memory_info['active_file'],
        'Inactive File': memory_info['inactive_file'],
        'Slab Reclaimable': memory_info['slab_reclaimable'],
        'Slab Unreclaimable': memory_info['slab_unreclaimable'],
        'Shmem': memory_info['shmem'],
        'Pagetables': memory_info['pagetables'],
        'Free': memory_info['free'],
        'Free Pcp': memory_info['free_pcp'],
        'Pagecache': memory_info['pagecache'],
        'Reserved': memory_info['reserved'],
        'Hugepages Total': hugepages_total_pages,
        'Hugepages Used': hugepages_used_pages,
        # Add other memory categories...
    }

    # Add swap usage to the memory summary
    swap_free_pages = memory_info.get('free_swap', 0) / pagesize_kb
    swap_total_pages = memory_info.get('total_swap', 0) / pagesize_kb
    swap_used_pages = swap_total_pages - swap_free_pages

    # Add swap usage to the memory summary
    memory_summary.update({
        'Swap Total': swap_total_pages,
        'Swap Free': swap_free_pages,
        'Swap Used': swap_used_pages,
    })

    # Include additional fields if show_full is True
    if show_full:
        memory_summary.update({
            'Isolated Anon': memory_info['isolated_anon'],
            'Isolated File': memory_info['isolated_file'],
            'Unevictable': memory_info['unevictable'],
            'Dirty': memory_info['dirty'],
            'Writeback': memory_info['writeback'],
            'Mapped': memory_info['mapped'],
            'Bounce': memory_info['bounce'],
            'Free CMA': memory_info['free_cma'],
            'Swap cache': memory_info['swapcache'],

        })

    # Subtract the rest accounted memory
    unaccounted_pages = total_memory_pages \
        - memory_info['active_anon'] \
        - memory_info['inactive_anon'] \
        - memory_info['isolated_anon'] \
        - memory_info['pagecache'] \
        - memory_info['swapcache'] \
        - memory_info['slab_reclaimable'] \
        - memory_info['slab_unreclaimable'] \
        - memory_info['pagetables'] \
        - memory_info['free'] \
        - memory_info['reserved'] \
        - memory_info['unevictable'] \
        - memory_info['bounce'] \
        - memory_info['free_cma'] \
        - hugepages_total_pages

    if verbose:
        print("\nUnaccounted memory = Total Memory"
              " - Active Anon"
              " - Inactive Anon"
              " - Isolated Anon"
              " - Pagecache"
              " - Swapcache"
              " - Slab Reclaimable"
              " - Slab Unreclaimable"
              " - Pagetables"
              " - Free"
              " - Reserved"
              " - Unevictable"
              " - Bounce"
              " - Free Cma"
              " - Huge pages\n")

        print(f"{scale_value(unaccounted_pages, 'pages', 'M', pagesize_kb):.2f} = "
              f"{scale_value(total_memory_pages, 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('active_anon', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('inactive_anon', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('isolated_anon', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('pagecache', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('swapcache', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('slab_reclaimable', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('slab_unreclaimable', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('pagetables', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('free', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('reserved', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('unevictable', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('bounce', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(memory_info.get('free_cma', 0), 'pages', 'M', pagesize_kb):.2f} "
              f"- {scale_value(hugepages_total_pages, 'pages', 'M', pagesize_kb):.2f}")

    # Return memory summary, total memory details, etc.
    return memory_summary, total_memory_pages, unaccounted_pages

def print_summary(memory_summary, total_memory_pages, unaccounted_pages, timestamp,
                  unit='M', pagesize_kb=4, show_unaccounted=False, verbose=False):
    """Prints the memory summary in a formatted table and displays the total memory size at the bottom."""

    unit_label = {
        'P': 'Pages',
        'K': 'KiB',
        'M': 'MB',
        'G': 'GB'
    }.get(unit, 'MB')

    print(f"\nTimestamp: {timestamp}")
    print(f"{'Category':<25} {unit_label:>15}")
    print("=" * 40)

    for key, pages in memory_summary.items():
        val = scale_value(pages, 'pages', unit, pagesize_kb)

        print(f"{key:<25} {val:>15,.2f}")

    # Show unaccounted memory if requested
    if show_unaccounted:
        print("-" * 40)
        print(f"{'Unaccounted Memory':<25}", end=' ')
        if unit == 'P':
            pages = unaccounted_pages
            print(f"{pages:>15,}")
        elif unit == 'K':
            print(f"{scale_value(unaccounted_pages, 'pages', 'K', pagesize_kb):>15,.2f}")
        elif unit == 'M':
            print(f"{scale_value(unaccounted_pages, 'pages', 'M', pagesize_kb):>15,.2f}")
        elif unit == 'G':
            print(f"{scale_value(unaccounted_pages, 'pages', 'G', pagesize_kb):>15,.2f}")

    # Footer
    print("=" * 40)
    print(f"{'Total Memory':<25}", end=' ')
    if unit == 'P':
        print(f"{total_memory_pages:>15,}")
    elif unit == 'K':
        print(f"{scale_value(total_memory_pages, 'pages', 'K', pagesize_kb):>15,.2f}")
    elif unit == 'M':
        print(f"{scale_value(total_memory_pages, 'pages', 'M', pagesize_kb):>15,.2f}")
    elif unit == 'G':
        print(f"{scale_value(total_memory_pages, 'pages', 'G', pagesize_kb):>15,.2f}")

def main():
    # Use argparse for flexible option parsing
    parser = argparse.ArgumentParser(description="Parse OOM logs and display memory summaries.")
    parser.add_argument("-K", action="store_const", const="K", dest="unit", help="Display memory in KiB")
    parser.add_argument("-M", action="store_const", const="M", dest="unit", help="Display memory in MiB")
    parser.add_argument("-P", action="store_const", const="P", dest="unit", help="Display memory in pages")
    parser.add_argument("-G", action="store_const", const="G", dest="unit", help="Display memory in GiB")
    parser.set_defaults(unit="M")

    # Define the flags
    parser.add_argument('--pagesize', type=int, default=4, help="Page size in KB (default: 4)")

    parser.add_argument('-u', '--unaccounted', action='store_true', help="Show unaccounted memory.")
    parser.add_argument('-f', '--full', action='store_true', help="Show full memory info.")
    parser.add_argument('-v', '--verbose', action='store_true', help="Show verbose memory info.")
    parser.add_argument('log_filename', metavar='log_filename', type=str, help="Log file to parse.")

    # Parse arguments
    args = parser.parse_args()
    pagesize_kb = args.pagesize
    show_unaccounted = args.unaccounted
    show_full = args.full
    log_filename = args.log_filename
    verbose = args.verbose
    unit = args.unit

    # Read and parse the log file
    log_data = parse_log_file(log_filename)
    mem_info_list = extract_memory_info(log_data)

    # Iterate over each memory event in the log file
    for timestamp, memory_info, total_hugepages_kb, used_hugepages_kb in mem_info_list:
        # Calculate memory usage, now including hugepage memory
        memory_summary, total_memory_pages, unaccounted_pages = calculate_memory_usage(memory_info, total_hugepages_kb, used_hugepages_kb, show_full, unit, pagesize_kb, verbose)

        # Print memory summary for each event
        print_summary(memory_summary, total_memory_pages, unaccounted_pages, timestamp, unit, pagesize_kb, show_unaccounted, verbose)

if __name__ == "__main__":
    main()

