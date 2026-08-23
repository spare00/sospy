#!/usr/bin/env python3

import sys
import re
import argparse
from collections import defaultdict

# === Common Utilities ===

DEFAULT_PAGE_SIZE_BYTES = 4096

def scale_value(value, from_unit="P", to_unit="G", pagesize_bytes=DEFAULT_PAGE_SIZE_BYTES):
    if from_unit == 'P':
        value_kb = value * pagesize_bytes / 1024
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
        return value_kb * 1024 / pagesize_bytes
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


# === Unreclaimable Slab Parser ===

def parse_memory_to_kb(value, unit):
    """Convert a numeric size with optional unit suffix to KiB."""
    unit = (unit or 'KB').upper()
    value = float(value)
    if unit in ('B',):
        return value / 1024
    if unit in ('KB', 'K'):
        return value
    if unit in ('MB', 'M'):
        return value * 1024
    if unit in ('GB', 'G'):
        return value * 1024 * 1024
    raise ValueError(f'Unsupported memory unit: {unit}')


KERNEL_LOG_PAYLOAD = re.compile(r'\[[\d.]+\]\s*(.*)$')

SLAB_LINE_PATTERN = re.compile(
    r'(?P<name>\S+)\s+'
    r'(?P<used>\d+(?:\.\d+)?)\s*(?P<used_unit>KB|MB|GB|B|kB|mB|gB)\s+'
    r'(?P<total>\d+(?:\.\d+)?)\s*(?P<total_unit>KB|MB|GB|B|kB|mB|gB)'
)


def slab_line_payload(line):
    """Return log text after the kernel [uptime] timestamp, if present."""
    if match := KERNEL_LOG_PAYLOAD.search(line):
        return match.group(1)
    return line


def extract_slab_usage(oom_events):
    """Extract unreclaimable slab usage from each OOM event block."""
    slab_info = defaultdict(dict)

    for event, lines in oom_events.items():
        in_slab_section = False
        for line in lines:
            if 'Unreclaimable slab info:' in line:
                in_slab_section = True
                continue
            if not in_slab_section:
                continue
            if 'Tasks state' in line:
                break
            if 'Name' in line and 'Used' in line and 'Total' in line:
                continue
            if match := SLAB_LINE_PATTERN.search(slab_line_payload(line)):
                name = match.group('name')
                used_kb = parse_memory_to_kb(match.group('used'), match.group('used_unit'))
                total_kb = parse_memory_to_kb(match.group('total'), match.group('total_unit'))
                slab_info[event][name] = {'used_kb': used_kb, 'total_kb': total_kb}

    return slab_info


def display_slab_usage(event_slabs, unit='G', top_n=10, pagesize_bytes=DEFAULT_PAGE_SIZE_BYTES):
    unit_label = {'P': 'Pages', 'K': 'KiB', 'M': 'MB', 'G': 'GB'}.get(unit, 'GB')

    for event, slabs in event_slabs.items():
        if not slabs:
            print(f"\nEvent: {event}")
            print('[!] No unreclaimable slab info found in this event.')
            continue

        sorted_slabs = sorted(slabs.items(), key=lambda x: x[1]['used_kb'], reverse=True)
        total_used = sum(data['used_kb'] for data in slabs.values())
        total_alloc = sum(data['total_kb'] for data in slabs.values())

        print(f"\nEvent: {event}")
        print(
            f"{'Used (' + unit_label + ')':>14} {'Total (' + unit_label + ')':>14} "
            f"{'Slab Name':<30}"
        )
        for name, data in sorted_slabs[:top_n]:
            used = scale_value(data['used_kb'], 'K', unit, pagesize_bytes)
            total = scale_value(data['total_kb'], 'K', unit, pagesize_bytes)
            print(f"{used:>14.2f} {total:>14.2f} {name:<30}")

        sep = '-' * 62
        print(sep)
        print(
            f"{scale_value(total_used, 'K', unit, pagesize_bytes):>14.2f} "
            f"{scale_value(total_alloc, 'K', unit, pagesize_bytes):>14.2f} "
            f"{'Total (all slabs)':>25}"
        )


# === RSS + Swap Usage Parser ===

def extract_rss_and_swap_usage(oom_events, group_by="process"):
    """
    group_by: 'process' keys by (pid, comm); 'command' aggregates by comm name;
    'uid' aggregates by numeric UID from the OOM dump.
    """
    usage_info = defaultdict(lambda: defaultdict(lambda: {'rss': 0, 'swap': 0, 'count': 0}))
    usage_pattern = re.compile(
        r'\[\s*(\d+)]\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+([^\s]+)'
    )

    for event, lines in oom_events.items():
        for line in lines:
            if match := usage_pattern.search(line):
                pid = int(match.group(1))
                uid = int(match.group(2))
                comm = match.group(9)
                if group_by == "command":
                    key = comm
                elif group_by == "uid":
                    key = uid
                else:
                    key = (pid, comm)
                    entry = usage_info[event][key]
                    entry['uid'] = uid
                    entry['tgid'] = int(match.group(3))
                usage_info[event][key]['rss'] += int(match.group(5))
                usage_info[event][key]['swap'] += int(match.group(7))
                usage_info[event][key]['count'] += 1

    return usage_info

def display_usage(event_usage, group_by="process", include_swap=False, unit="G", pagesize_bytes=DEFAULT_PAGE_SIZE_BYTES):
    unit_label = {'P': 'Pages', 'K': 'KiB', 'M': 'MB', 'G': 'GB'}.get(unit, 'GB')

    for event, usage in event_usage.items():
        sorted_usage = sorted(usage.items(), key=lambda x: x[1]['rss'], reverse=True)
        total_rss = scale_value(sum(data['rss'] for data in usage.values()), 'P', unit, pagesize_bytes)
        print(f"\nEvent: {event}")
        if group_by == "process":
            if include_swap:
                total_swap = scale_value(sum(data['swap'] for data in usage.values()), 'P', unit, pagesize_bytes)
                print(
                    f"{'RSS (' + unit_label + ')':>12} {'Swap (' + unit_label + ')':>12} "
                    f"{'UID':>7} {'TGID':>7} {'PID':>8} {'Comm':<20}"
                )
            else:
                print(
                    f"{'RSS (' + unit_label + ')':>10} {'UID':>7} {'TGID':>7} "
                    f"{'PID':>8} {'Comm':<20}"
                )
        elif group_by == "uid":
            if include_swap:
                total_swap = scale_value(sum(data['swap'] for data in usage.values()), 'P', unit, pagesize_bytes)
                print(
                    f"{'RSS (' + unit_label + ')':>12} {'Swap (' + unit_label + ')':>12} "
                    f"{'Processes':>10} {'UID':>7}"
                )
            else:
                print(f"{'RSS (' + unit_label + ')':>10} {'Processes':>10} {'UID':>7}")
        elif include_swap:
            total_swap = scale_value(sum(data['swap'] for data in usage.values()), 'P', unit, pagesize_bytes)
            print(f"{'RSS (' + unit_label + ')':>12} {'Swap (' + unit_label + ')':>12} {'Processes':>10} {'Comm':<20}")
        else:
            print(f"{'RSS (' + unit_label + ')':>10} {'Processes':>10} {'Comm':<20}")

        for key, data in sorted_usage[:10]:
            rss = scale_value(data['rss'], 'P', unit, pagesize_bytes)
            count = data['count']
            if group_by == "process":
                pid, comm = key
                uid = data['uid']
                tgid = data['tgid']
                if include_swap:
                    swap = scale_value(data['swap'], 'P', unit, pagesize_bytes)
                    print(
                        f"{rss:>10.2f} {swap:>12.2f} {uid:>7} {tgid:>7} "
                        f"{pid:>8} {comm:<20}"
                    )
                else:
                    print(f"{rss:>10.2f} {uid:>7} {tgid:>7} {pid:>8} {comm:<20}")
            elif group_by == "uid":
                if include_swap:
                    swap = scale_value(data['swap'], 'P', unit, pagesize_bytes)
                    print(f"{rss:>10.2f} {swap:>12.2f} {count:>10} {key:>7}")
                else:
                    print(f"{rss:>10.2f} {count:>10} {key:>7}")
            elif include_swap:
                swap = scale_value(data['swap'], 'P', unit, pagesize_bytes)
                print(f"{rss:>10.2f} {swap:>12.2f} {count:>10} {key:<20}")
            else:
                print(f"{rss:>10.2f} {count:>10} {key:<20}")

        sep = '-' * (68 if group_by == "process" else 60)
        print(sep)
        if include_swap:
            print(f"{total_rss:>10.2f} {total_swap:>12.2f} {'RSS Total':>25}")
        else:
            print(f"{total_rss:>10.2f} {'RSS Total':>25}")


# === Memory Summary Placeholder ===

def memory_summary_not_available(*args, **kwargs):
    print("\n[!] Memory summary functionality not available. This feature requires access to the config module and memory patterns.\n")
    return


# === Main CLI Interface ===

def main():
    parser = argparse.ArgumentParser(description="Parse OOM logs and display memory summaries and process usage.")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('-i', '--meminfo', action='store_true', help='Show memory usage summary (Mem-Info)')
    group.add_argument('-c', '--commands', action='store_true', help='Aggregate RSS/swap by command name (comm)')
    group.add_argument('-p', '--processes', action='store_true', help='List RSS/swap per process (PID + comm from OOM dump)')
    group.add_argument('-u', '--uids', action='store_true', help='Aggregate RSS/swap by UID from the OOM dump')
    group.add_argument('--slab', action='store_true', help='Show top unreclaimable slab consumers from OOM dump')

    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument('-K', action='store_const', const='K', dest='unit', help='Display memory in KiB')
    unit_group.add_argument('-M', action='store_const', const='M', dest='unit', help='Display memory in MiB')
    unit_group.add_argument('-G', action='store_const', const='G', dest='unit', help='Display memory in GiB')
    unit_group.add_argument('-P', action='store_const', const='P', dest='unit', help='Display memory in pages')
    parser.set_defaults(unit='G')

    parser.add_argument(
        '--pagesize',
        type=int,
        default=DEFAULT_PAGE_SIZE_BYTES,
        metavar='BYTES',
        help='Machine page size in bytes for page and memory unit conversions (default: %(default)s, x86_64). '
        'Example: RHEL ppc64le often uses 65536.',
    )
    parser.add_argument('-s', '--swap', action='store_true', help='Include swap usage (for -c / -p / -u)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('log_file', type=str, help='Path to OOM log file')
    args = parser.parse_args()

    if args.pagesize <= 0:
        print('Error: --pagesize must be a positive integer (bytes).', file=sys.stderr)
        sys.exit(1)

    # Default to per-process (-p) if no dump mode or meminfo is set
    if not args.meminfo and not args.commands and not args.processes and not args.uids and not args.slab:
        args.processes = True

    if args.slab:
        events = parse_oom_log(args.log_file)
        slab_usage = extract_slab_usage(events)
        display_slab_usage(
            slab_usage,
            unit=args.unit,
            pagesize_bytes=args.pagesize,
        )

    elif args.commands or args.processes or args.uids:
        events = parse_oom_log(args.log_file)
        if args.commands:
            group_by = "command"
        elif args.uids:
            group_by = "uid"
        else:
            group_by = "process"
        usage = extract_rss_and_swap_usage(events, group_by=group_by)
        display_usage(
            usage,
            group_by=group_by,
            include_swap=args.swap,
            unit=args.unit,
            pagesize_bytes=args.pagesize,
        )

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
                    pagesize_bytes=args.pagesize, verbose=args.verbose
                )
                print_summary(
                    mem_summary, total_pages, unaccounted, timestamp,
                    unit=args.unit, pagesize_bytes=args.pagesize,
                    show_unaccounted=True, verbose=args.verbose
                )

        except ImportError:
            memory_summary_not_available()

if __name__ == "__main__":
    main()
