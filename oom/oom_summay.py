#!/usr/bin/env python3

import sys
import re

def parse_log_file(filename):
    with open(filename, 'r') as file:
        log_data = file.read()
    return log_data

def extract_memory_info(log_data):
    # Pattern to detect the start of a Mem-Info section
    pattern = r'( Mem-Info:)'
    # Pattern to detect the OOM invocation line
    oom_invocation_pattern = r'(.* invoked oom-killer:.*)'

    # Try splitting using the first pattern
    mem_info_sections = re.split(pattern, log_data)[1:]

    timestamps = mem_info_sections[0::2]
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
        'total_pages_ram': r'(\d+) pages RAM',
        'hugepages_total_1048576': r'hugepages_total=(\d+).*hugepages_size=1048576kB',
        'hugepages_free_1048576': r'hugepages_free=(\d+).*hugepages_size=1048576kB',
        'hugepages_surp_1048576': r'hugepages_surp=(\d+).*hugepages_size=1048576kB',
        'hugepages_total_2048': r'hugepages_total=(\d+).*hugepages_size=2048kB',
        'hugepages_free_2048': r'hugepages_free=(\d+).*hugepages_size=2048kB',
        'hugepages_surp_2048': r'hugepages_surp=(\d+).*hugepages_size=2048kB'
    }

    mem_info_list = []

    for timestamp, section in zip(timestamps, mem_info_sections):
        memory_info = {}
        # Find the line where the OOM killer is invoked
        oom_invocation_line = re.search(oom_invocation_pattern, section)
        if oom_invocation_line:
            oom_invocation_line = oom_invocation_line.group(1)
        else:
            oom_invocation_line = "OOM invocation line not found."

        for key, pattern in patterns.items():
            match = re.search(pattern, section)
            if match:
                memory_info[key] = int(match.group(1))
        if memory_info:
            timestamp_part = ' '.join(timestamp.split()[:3])
            mem_info_list.append((timestamp_part, oom_invocation_line, memory_info))

    return mem_info_list

def calculate_memory_usage(memory_info):
    page_size_kb = 4  # Page size in KB

    # Safely get the value from the memory_info dict, default to 0 if not found
    total_memory = memory_info.get('total_pages_ram', 0)
    total_memory_mb = total_memory * page_size_kb / 1024
    total_memory_gb = total_memory_mb / 1024

    hugepages_total_mb = (memory_info.get('hugepages_total_1048576', 0) * 1048576 + memory_info.get('hugepages_total_2048', 0) * 2048) / 1024
    hugepages_total_gb = hugepages_total_mb / 1024
    hugepages_free_mb = (memory_info.get('hugepages_free_1048576', 0) * 1048576 + memory_info.get('hugepages_free_2048', 0) * 2048) / 1024
    hugepages_free_gb = hugepages_free_mb / 1024
    hugepages_surp_mb = (memory_info.get('hugepages_surp_1048576', 0) * 1048576 + memory_info.get('hugepages_surp_2048', 0) * 2048) / 1024
    hugepages_surp_gb = hugepages_surp_mb / 1024

    memory_summary = {
        'Total Memory': (total_memory_mb, total_memory_gb),
        'Active Anon': ((memory_info.get('active_anon', 0) * page_size_kb) / 1024, (memory_info.get('active_anon', 0) * page_size_kb) / (1024 ** 2)),
        'Inactive Anon': ((memory_info.get('inactive_anon', 0) * page_size_kb) / 1024, (memory_info.get('inactive_anon', 0) * page_size_kb) / (1024 ** 2)),
        'Active File': ((memory_info.get('active_file', 0) * page_size_kb) / 1024, (memory_info.get('active_file', 0) * page_size_kb) / (1024 ** 2)),
        'Inactive File': ((memory_info.get('inactive_file', 0) * page_size_kb) / 1024, (memory_info.get('inactive_file', 0) * page_size_kb) / (1024 ** 2)),
        'Slab': (((memory_info.get('slab_reclaimable', 0) + memory_info.get('slab_unreclaimable', 0)) * page_size_kb) / 1024, ((memory_info.get('slab_reclaimable', 0) + memory_info.get('slab_unreclaimable', 0)) * page_size_kb) / (1024 ** 2)),
        'Shmem': ((memory_info.get('shmem', 0) * page_size_kb) / 1024, (memory_info.get('shmem', 0) * page_size_kb) / (1024 ** 2)),
        'Page Tables': ((memory_info.get('pagetables', 0) * page_size_kb) / 1024, (memory_info.get('pagetables', 0) * page_size_kb) / (1024 ** 2)),
        'Free': (((memory_info.get('free', 0) + memory_info.get('free_pcp', 0)) * page_size_kb) / 1024, ((memory_info.get('free', 0) + memory_info.get('free_pcp', 0)) * page_size_kb) / (1024 ** 2)),
        'Page Cache': ((memory_info.get('pagecache', 0) * page_size_kb) / 1024, (memory_info.get('pagecache', 0) * page_size_kb) / (1024 ** 2)),
        'Reserved': ((memory_info.get('reserved', 0) * page_size_kb) / 1024, (memory_info.get('reserved', 0) * page_size_kb) / (1024 ** 2)),
        'Hugepages Total': (hugepages_total_mb, hugepages_total_gb),
        'Hugepages Free': (hugepages_free_mb, hugepages_free_gb),
        'Hugepages Surplus': (hugepages_surp_mb, hugepages_surp_gb)
    }

    return memory_summary

def print_summary(memory_summary, index, timestamp, oom_invocation_line):
    print(f"\nEvent: {oom_invocation_line}")
    print(f"{'Category':<20} {'MB':>15} {'GB':>15}")
    print("="*52)
    for key, (value_mb, value_gb) in memory_summary.items():
        print(f"{key:<20} {value_mb:>15.2f} {value_gb:>15.2f}")
    print("\n")

def main():
    if len(sys.argv) != 2:
        print("Usage: oom_summary.py <log_filename>")
        sys.exit(1)

    log_filename = sys.argv[1]
    log_data = parse_log_file(log_filename)
    mem_info_list = extract_memory_info(log_data)

    for index, (timestamp, oom_invocation_line, memory_info) in enumerate(mem_info_list):
        memory_summary = calculate_memory_usage(memory_info)
        print_summary(memory_summary, index, timestamp, oom_invocation_line)

if __name__ == "__main__":
    main()
