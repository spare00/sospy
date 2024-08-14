#!/usr/bin/env python3

import sys
import re

def parse_log_file(filename):
    """Reads the log file and returns its contents as a string."""
    try:
        with open(filename, 'r') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        sys.exit(1)

def extract_memory_info(log_data):
    """Extracts memory information and timestamps from log sections starting with 'Mem-Info:'."""
    mem_info_pattern = r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}).*Mem-Info:'
    oom_pattern = r'(.* invoked oom-killer:.*)'

    # Find all sections starting with 'Mem-Info' along with their timestamps
    mem_info_sections = re.split(mem_info_pattern, log_data)[1:]

    # Extract timestamps
    timestamps = [timestamp.strip() for timestamp in mem_info_sections[0::2]]
    # Extract corresponding Mem-Info sections
    mem_info_sections = mem_info_sections[1::2]

    patterns = {
        'active_anon': r'active_anon:(\d+)',
        'inactive_anon': r'inactive_anon:(\d+)',
        'active_file': r'active_file:(\d+)',
        'inactive_file': r'inactive_file:(\d+)',
        'slab_reclaimable': r'slab_reclaimable:(\d+)',
        'slab_unreclaimable': r'slab_unreclaimable:(\d+)',
        'shmem': r'shmem:(\d+)',
        'pagetables': r'pagetables:(\d+)',
        'free': r'free:(\d+)',
        'free_pcp': r'free_pcp:(\d+)',
        'pagecache': r'(\d+) total pagecache pages',
        'reserved': r'(\d+) pages reserved',
        'total_pages_ram': r'(\d+) pages RAM',  # Key indicating total memory
    }

    mem_info_list = []

    for timestamp, section in zip(timestamps, mem_info_sections):
        # Ensure the timestamp is cleaned up
        timestamp = ' '.join(timestamp.split())
        
        memory_info = {key: int(re.search(regex, section).group(1)) if re.search(regex, section) else 0 
                       for key, regex in patterns.items()}
        oom_invocation_line = re.search(oom_pattern, section)
        oom_invocation_line = oom_invocation_line.group(1) if oom_invocation_line else ""
        mem_info_list.append((timestamp, oom_invocation_line, memory_info))

    return mem_info_list

def calculate_memory_usage(memory_info):
    """Calculate memory usage summary from memory info."""
    page_size_kb = 4  # Page size in KB
    mb_conversion = lambda x: x * page_size_kb / 1024 if x else 0

    # Extract total memory and remove it from the main summary
    total_memory_pages = memory_info.pop('total_pages_ram', 0)
    total_memory_mb = total_memory_pages * page_size_kb / 1024
    total_memory_gb = total_memory_mb / 1024

    memory_summary = {key: (mb_conversion(memory_info[key]), mb_conversion(memory_info[key]) / 1024, memory_info[key])
                      for key in memory_info}

    # Calculate only the combined hugepages totals, free, and surplus
    hugepages_calc = lambda x: (memory_info.get(f'hugepages_{x}_1048576', 0), memory_info.get(f'hugepages_{x}_2048', 0))
    
    for hugepage_type in ['total', 'free', 'surp']:
        pages_1048576, pages_2048 = hugepages_calc(hugepage_type)
        total_pages = pages_1048576 + pages_2048
        total_mb = (pages_1048576 * 1048576 + pages_2048 * 2048) / 1024
        memory_summary[f'Hugepages {hugepage_type.capitalize()}'] = (total_mb, total_mb / 1024, total_pages)

    return memory_summary, total_memory_mb, total_memory_gb, total_memory_pages

def print_summary(memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, timestamp, oom_invocation_line, show_pages):
    """Prints the memory summary in a formatted table and displays the total memory size at the bottom."""
    header = f"\nEvent: {oom_invocation_line}\nTimestamp: {timestamp}\n"
    
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

    # Print the total memory size at the bottom
    if show_pages:
        print(f"{'-'*68}")
        print(f"{'Total Memory':<25} {total_memory_pages:>15,} {total_memory_mb:>15,.2f} {total_memory_gb:>10,.2f}")
    else:
        print(f"{'-'*52}")
        print(f"{'Total Memory':<25} {total_memory_mb:>15,.2f} {total_memory_gb:>10,.2f}")
    print("\n")

def main():
    if len(sys.argv) not in [2, 3]:
        print("Usage: oom_summary.py [-p] <log_filename>")
        sys.exit(1)

    show_pages = sys.argv[1] == '-p'
    log_filename = sys.argv[2] if show_pages else sys.argv[1]

    log_data = parse_log_file(log_filename)
    mem_info_list = extract_memory_info(log_data)

    for timestamp, oom_invocation_line, memory_info in mem_info_list:
        memory_summary, total_memory_mb, total_memory_gb, total_memory_pages = calculate_memory_usage(memory_info)
        print_summary(memory_summary, total_memory_mb, total_memory_gb, total_memory_pages, timestamp, oom_invocation_line, show_pages)

if __name__ == "__main__":
    main()
