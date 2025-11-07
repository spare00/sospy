#!/usr/bin/env python3

import sys
import re
import argparse
from collections import defaultdict

# === Common Utilities ===

def scale_value(value, from_unit="P", to_unit="G", pagesize_kb=4):
    if from_unit == 'P':
        value_kb = value * pagesize_kb
    elif from_unit == 'K':
        value_kb = value
    elif from_unit == 'M':
        value_kb = value * 1024
    elif from_unit == 'G':
        value_kb = value * 1024 * 1024
    else:
        raise ValueError('Unsupported from_unit')

    if to_unit == 'K':
        return value_kb
    elif to_unit == 'M':
        return value_kb / 1024
    elif to_unit == 'G':
        return value_kb / (1024 * 1024)
    elif to_unit == 'P':
        return value_kb / pagesize_kb
    else:
        raise ValueError('Unsupported to_unit')


# === OOM Event Parser ===

def parse_log_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        sys.exit(1)

def parse_oom_log(file_path):
    oom_events = defaultdict(list)
    current_event = None

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
            for line in file:
                if "invoked oom-killer" in line:
                    current_event = line.strip()
                if current_event:
                    oom_events[current_event].append(line.strip())
                    if "Out of memory: Killed process" in line or "Out of memory: Kill process" in line:
                        current_event = None
    except Exception as e:
        print(f"Failed to parse OOM log: {e}")
        sys.exit(1)

    return oom_events


# === RSS + Swap Usage Parser ===

def extract_rss_and_swap_usage(oom_events):
    usage_info = defaultdict(lambda: defaultdict(lambda: {'rss': 0, 'swap': 0, 'count': 0}))
    usage_pattern = re.compile(
        r'\[\s*(\d+)]\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+([^\s]+)'
    )

    for event, lines in oom_events.items():
        for line in lines:
            if match := usage_pattern.search(line):
                name = match.group(9)
                usage_info[event][name]['rss'] += int(match.group(5))
                usage_info[event][name]['swap'] += int(match.group(7))
                usage_info[event][name]['count'] += 1

    return usage_info

def display_usage(event_usage, include_swap=False, unit="G", pagesize_kb=4):
    unit_label = {'P': 'Pages', 'K': 'KiB', 'M': 'MB', 'G': 'GB'}.get(unit, 'GB')

    for event, usage in event_usage.items():
        sorted_usage = sorted(usage.items(), key=lambda x: x[1]['rss'], reverse=True)
        total_rss = scale_value(sum(data['rss'] for data in usage.values()), 'P', unit, pagesize_kb)
        print(f"\nEvent: {event}")
        if include_swap:
            total_swap = scale_value(sum(data['swap'] for data in usage.values()), 'P', unit, pagesize_kb)
            print(f"{'RSS (' + unit_label + ')':>12} {'Swap (' + unit_label + ')':>12} {'Count':>8} {'Name':<20}")
        else:
            print(f"{'RSS (' + unit_label + ')':>10} {'Count':>10} {'Name':<20}")

        for name, data in sorted_usage[:10]:
            rss = scale_value(data['rss'], 'P', unit, pagesize_kb)
            count = data['count']
            if include_swap:
                swap = scale_value(data['swap'], 'P', unit, pagesize_kb)
                print(f"{rss:>10.2f} {swap:>12.2f} {count:>10} {name:<20}")
            else:
                print(f"{rss:>10.2f} {count:>10} {name:<20}")

        print('-' * 50)
        if include_swap:
            print(f"{total_rss:>10.2f} {total_swap:>12.2f} {'RSS Total':>20}")
        else:
            print(f"{total_rss:>10.2f} {'RSS Total':>20}")


# === Memory Summary Placeholder ===

def memory_summary_not_available(*args, **kwargs):
    print("\n[!] Memory summary functionality not available. This feature requires access to the config module and memory patterns.\n")
    return


# === Main CLI Interface ===

def main():
    parser = argparse.ArgumentParser(description="Parse OOM logs and display memory summaries and process usage.")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-i', '--meminfo', action='store_true', help='Show memory usage summary (Mem-Info)')
    group.add_argument('-p', '--processes', action='store_true', help='Show top memory-consuming processes (from OOM dump)')

    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument('-K', action='store_const', const='K', dest='unit', help='Display memory in KiB')
    unit_group.add_argument('-M', action='store_const', const='M', dest='unit', help='Display memory in MiB')
    unit_group.add_argument('-G', action='store_const', const='G', dest='unit', help='Display memory in GiB')
    unit_group.add_argument('-P', action='store_const', const='P', dest='unit', help='Display memory in pages')
    parser.set_defaults(unit='G')

    parser.add_argument('--pagesize', type=int, default=4, help='Page size in KB (default: 4)')
    parser.add_argument('-s', '--swap', action='store_true', help='Include swap usage (for --processes)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('log_file', type=str, help='Path to OOM log file')
    args = parser.parse_args()

    if args.processes:
        events = parse_oom_log(args.log_file)
        usage = extract_rss_and_swap_usage(events)
        display_usage(usage, include_swap=args.swap, unit=args.unit, pagesize_kb=args.pagesize)

    elif args.meminfo:
        try:
            from config import patterns, mem_info_pattern, oom_pattern
            from chk_oom_summary import extract_memory_info, calculate_memory_usage, print_summary

            log_data = parse_log_file(args.log_file)
            mem_info_list = extract_memory_info(log_data)

            for timestamp, memory_info, total_hugepages_kb, used_hugepages_kb in mem_info_list:
                mem_summary, total_pages, unaccounted = calculate_memory_usage(
                    memory_info, total_hugepages_kb, used_hugepages_kb,
                    show_full=True, unit=args.unit,
                    pagesize_kb=args.pagesize, verbose=args.verbose
                )
                print_summary(
                    mem_summary, total_pages, unaccounted, timestamp,
                    unit=args.unit, pagesize_kb=args.pagesize,
                    show_unaccounted=True, verbose=args.verbose
                )

        except ImportError:
            memory_summary_not_available()

if __name__ == "__main__":
    main()
