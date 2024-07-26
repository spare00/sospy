#!/usr/bin/env python3

import sys
import re

def parse_log_file(filename):
    with open(filename, 'r') as file:
        log_data = file.read()
    return log_data

def extract_memory_info(log_data):
    # First pattern
    pattern1 = r'(Jul \d+ \d+:\d+:\d+ \w+ kernel: Mem-Info:)'
    # Second pattern
    pattern2 = r'(\w+ \d+ \d+:\d+:\d+ [\w-]+ kernel: \[\d+\.\d+\] Mem-Info:)'

    # Try splitting using the first pattern
    mem_info_sections = re.split(pattern1, log_data)[1:]

    # If the result is empty, try the second pattern
    if not mem_info_sections:
        mem_info_sections = re.split(pattern2, log_data)[1:]

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
        for key, pattern in patterns.items():
            match = re.search(pattern, section)
            if match:
                memory_info[key] = int(match.group(1))
        if memory_info:
            timestamp_part = ' '.join(timestamp.split()[:3])
            mem_info_list.append((timestamp_part, memory_info))

    return mem_info_list

def calculate_memory_usage(memory_info):
    page_size_kb = 4  # Page size in KB

    total_memory = memory_info['total_pages_ram']
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
        'Active Anon': ((memory_info['active_anon'] * page_size_kb) / 1024, (memory_info['active_anon'] * page_size_kb) / (1024 ** 2)),
        'Inactive Anon': ((memory_info['inactive_anon'] * page_size_kb) / 1024, (memory_info['inactive_anon'] * page_size_kb) / (1024 ** 2)),
        'Active File': ((memory_info['active_file'] * page_size_kb) / 1024, (memory_info['active_file'] * page_size_kb) / (1024 ** 2)),
        'Inactive File': ((memory_info['inactive_file'] * page_size_kb) / 1024, (memory_info['inactive_file'] * page_size_kb) / (1024 ** 2)),
        'Slab': (((memory_info['slab_reclaimable'] + memory_info['slab_unreclaimable']) * page_size_kb) / 1024, ((memory_info['slab_reclaimable'] + memory_info['slab_unreclaimable']) * page_size_kb) / (1024 ** 2)),
        'Shmem': ((memory_info['shmem'] * page_size_kb) / 1024, (memory_info['shmem'] * page_size_kb) / (1024 ** 2)),
        'Page Tables': ((memory_info['pagetables'] * page_size_kb) / 1024, (memory_info['pagetables'] * page_size_kb) / (1024 ** 2)),
        'Free': (((memory_info['free'] + memory_info['free_pcp']) * page_size_kb) / 1024, ((memory_info['free'] + memory_info['free_pcp']) * page_size_kb) / (1024 ** 2)),
        'Page Cache': ((memory_info['pagecache'] * page_size_kb) / 1024, (memory_info['pagecache'] * page_size_kb) / (1024 ** 2)),
        'Reserved': ((memory_info['reserved'] * page_size_kb) / 1024, (memory_info['reserved'] * page_size_kb) / (1024 ** 2)),
        'Hugepages Total': (hugepages_total_mb, hugepages_total_gb),
        'Hugepages Free': (hugepages_free_mb, hugepages_free_gb),
        'Hugepages Surplus': (hugepages_surp_mb, hugepages_surp_gb)
    }

    return memory_summary

def print_summary(memory_summary, index, timestamp):
    print(f"Mem-Info Summary {index+1} at {timestamp}:")
    print(f"{'Category':<20} {'MB':>15} {'GB':>15}")
    print("="*50)
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

    for index, (timestamp, memory_info) in enumerate(mem_info_list):
        memory_summary = calculate_memory_usage(memory_info)
        print_summary(memory_summary, index, timestamp)

if __name__ == "__main__":
    main()

