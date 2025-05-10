#!/usr/bin/env python3

import re
import sys
import argparse
from collections import defaultdict

def scale_value(value, from_unit="P", to_unit="M", pagesize_kb=4):
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

def extract_rss_and_swap_usage(oom_events):
    """
    Extract RSS and swap usage from OOM event blocks.
    Accepts a dict of {event_start_line: [log_lines]}.
    Returns a list of (pid, name, rss_pages, swap_pages).
    """
    usage_info = defaultdict(lambda: defaultdict(lambda: {'rss': 0, 'swap': 0, 'count': 0}))
    # Pattern to correctly capture the PID, UID, TGID, total_vm, rss, pgtables_bytes, swapents, oom_score_adj, and name
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

def display_usage(event_usage, include_swap=False, unit="M", pagesize_kb=4):
    """
    Displays the RSS and optionally swap usage information in GB only.
    Args:
        event_usage (defaultdict): RSS and swap usage data.
        include_swap (bool): Whether to display swap usage.
    """

    unit_label = {'P': 'Pages', 'K': 'KiB', 'M': 'MB', 'G': 'GB'}.get(unit, 'MB')

    for event, usage in event_usage.items():
        sorted_usage = sorted(usage.items(), key=lambda x: x[1]['rss'], reverse=True)
        total_rss = scale_value(sum(data['rss'] for data in usage.values()), 'P', unit, pagesize_kb)
        print(f"\nEvent: {event}")
        if include_swap:
            total_swap = scale_value(sum(data['swap'] for data in usage.values()), 'P', unit, pagesize_kb)
            print(f"{'RSS (' + unit_label + ')':>12} {'Swap (' + unit_label + ')':>12} {'Count':>8} {'Name':<20}")
        else:
            print(f"{'RSS (' + unit_label + ')':>10} {'Count':>10} {'Name':<20}")

        for name, data in sorted_usage[:10]:  # Show only the top 10 items
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

def main():
    parser = argparse.ArgumentParser(description="Parse OOM log and display RSS and optional swap usage.")

    # Unit options
    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument('-K', action='store_const', const='K', dest='unit', help='Display memory in KiB')
    unit_group.add_argument('-M', action='store_const', const='M', dest='unit', help='Display memory in MiB')
    unit_group.add_argument('-G', action='store_const', const='G', dest='unit', help='Display memory in GiB')
    unit_group.add_argument('-P', action='store_const', const='P', dest='unit', help='Display memory in pages')
    parser.set_defaults(unit='M')

    parser.add_argument('log_file', help="Path to the OOM log file")
    parser.add_argument('-s', '--swap', action='store_true', help="Include swap usage in the output")
    parser.add_argument('--pagesize', type=int, default=4, help="Page size in KB (default: 4)")

    args = parser.parse_args()

    # Process the log
    oom_events = parse_oom_log(args.log_file)
    usage_info = extract_rss_and_swap_usage(oom_events)  # return in pages

    display_usage(usage_info, args.swap, unit=args.unit, pagesize_kb=args.pagesize)

if __name__ == "__main__":
    main()
