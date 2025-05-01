#!/usr/bin/env python3

import re
import sys
import argparse
from collections import defaultdict

def parse_oom_log(file_path):
    """
    Parses the OOM log file and extracts OOM events.
    Args:
        file_path (str): Path to the OOM log file.
    Returns:
        defaultdict: A dictionary with OOM event start lines as keys and corresponding log lines as values.
    """
    oom_events = defaultdict(list)
    current_event = None

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
            for line in file:
                # Detect the start of an OOM event
                if "invoked oom-killer" in line:
                    current_event = line.strip()
                # Collect the relevant data within the OOM event
                if current_event:
                    oom_events[current_event].append(line.strip())
                    if "Out of memory: Killed process" in line or \
                       "Out of memory: Kill process" in line:
                        current_event = None
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred while reading the file: {e}")
        sys.exit(1)

    return oom_events

def extract_rss_and_swap_usage(oom_events, include_swap):
    """
    Extracts RSS and optionally swap usage from OOM events.
    Args:
        oom_events (defaultdict): OOM events data.
        include_swap (bool): Whether to include swap usage.
    Returns:
        defaultdict: A dictionary with events as keys and RSS and optional swap usage as values.
    """
    event_usage = defaultdict(lambda: defaultdict(lambda: {'rss_kb': 0, 'swap_kb': 0, 'count': 0}))
    # Pattern to correctly capture the PID, UID, TGID, total_vm, rss, pgtables_bytes, swapents, oom_score_adj, and name
    usage_pattern = re.compile(
        r'\[\s*(\d+)]\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+([^\s]+)'
    )

    for event, lines in oom_events.items():
        for line in lines:
            if match := usage_pattern.search(line):
                rss_pages = int(match.group(5))
                swapents = int(match.group(7))
                name = match.group(9)
                rss_kb = rss_pages * 4  # Convert pages to kB
                swap_kb = swapents * 4  # Convert pages to kB
                event_usage[event][name]['rss_kb'] += rss_kb
                event_usage[event][name]['swap_kb'] += swap_kb
                event_usage[event][name]['count'] += 1

    return event_usage

def display_usage(event_usage, include_swap):
    """
    Displays the RSS and optionally swap usage information in GB only.
    Args:
        event_usage (defaultdict): RSS and swap usage data.
        include_swap (bool): Whether to display swap usage.
    """
    for event, usage in event_usage.items():
        sorted_usage = sorted(usage.items(), key=lambda x: x[1]['rss_kb'], reverse=True)
        total_rss_kb = sum(data['rss_kb'] for data in usage.values())
        total_rss_gb = total_rss_kb / 1024 / 1024  # Convert kB to GB
        if include_swap:
            total_swap_kb = sum(data['swap_kb'] for data in usage.values())
            total_swap_gb = total_swap_kb / 1024 / 1024  # Convert kB to GB
        print(f"\nEvent: {event}")
        if include_swap:
            print(f"{'RSS (GB)':>10} {'Swap (GB)':>12} {'Count':>10} {'Name':<20}")
        else:
            print(f"{'RSS (GB)':>10} {'Count':>10} {'Name':<20}")
        for name, data in sorted_usage[:10]:  # Show only the top 10 items
            rss_gb = data['rss_kb'] / 1024 / 1024  # Convert kB to GB
            count = data['count']
            if include_swap:
                swap_gb = data['swap_kb'] / 1024 / 1024  # Convert kB to GB
                print(f"{rss_gb:>10.2f} {swap_gb:>12.2f} {count:>10} {name:<20}")
            else:
                print(f"{rss_gb:>10.2f} {count:>10} {name:<20}")
        print('-' * 50)
        if include_swap:
            print(f"{total_rss_gb:>10.2f} {total_swap_gb:>12.2f} {'RSS Total':>15}")
        else:
            print(f"{total_rss_gb:>10.2f} {'RSS Total':>15}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse OOM log and display RSS and optional swap usage.")
    parser.add_argument('log_file', help="Path to the OOM log file")
    parser.add_argument('-s', '--swap', action='store_true', help="Include swap usage in the output")
    args = parser.parse_args()

    oom_events = parse_oom_log(args.log_file)
    event_usage = extract_rss_and_swap_usage(oom_events, args.swap)
    display_usage(event_usage, args.swap)
