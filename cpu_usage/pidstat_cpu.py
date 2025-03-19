#!/usr/bin/env python3

import argparse
import re
from collections import defaultdict

# Regex patterns to detect timestamps and headers
TIMESTAMP_REGEX = re.compile(r'^\d{1,2}:\d{2}:\d{2}\s+[APM]{2}\s+\S+\s+')
VERBOSE_HEADER_REGEX = re.compile(r'^\s*#\s*Time\s+USER\s+')

def detect_format(lines):
    """Detect whether the pidstat output is verbose or short based on headers."""
    for line in lines:
        if VERBOSE_HEADER_REGEX.match(line):  # Verbose format detected
            return "verbose"
        if "UID" in line and "PID" in line:  # Short format detected
            return "short"
    return None  # Unrecognized format

def parse_pidstat_line_short(line):
    """Extract relevant CPU metrics from short-format pidstat output."""
    try:
        if not TIMESTAMP_REGEX.match(line):
            return None  # Ignore non-timestamp lines

        # Extract fields after the timestamp
        fields = re.split(r'\s+', line.strip())[3:]  # Ignore timestamp parts
 
        if len(fields) < 8:
            return None

        user = fields[0]  # UID column
        usr, system, wait, cpu = map(lambda x: float(x.strip('%')), (fields[2], fields[3], fields[5], fields[6]))
        command = fields[-1]  # Last column is the command
        return user, usr, system, wait, cpu, command.strip()

    except (ValueError, IndexError):
        print(line)
        return None  # Ignore malformed lines

def parse_pidstat_line_verbose(line):
    """Extract relevant CPU metrics from verbose-format pidstat output."""
    try:
        fields = re.split(r'\s+', line.strip())
        if len(fields) < 17:  # Ensure enough columns exist
            return None

        user = fields[1]  # USER column
        usr, system, wait, cpu = map(lambda x: float(x.strip('%')), (fields[3], fields[4], fields[6], fields[7]))
        command = fields[-1]  # Last column is the command

        return user, usr, system, wait, cpu, command.strip()

    except (ValueError, IndexError):
        return None  # Ignore malformed lines

def calculate_usage(filename, group_by, sort_by, debug=False):
    """Calculate and display CPU usage grouped by user or command."""
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        return

    usage_data = defaultdict(lambda: {'usr': 0.0, 'system': 0.0, 'wait': 0.0, 'cpu': 0.0, 'count': 0})
    total_usr, total_system, total_wait, total_cpu, total_count = 0.0, 0.0, 0.0, 0.0, 0

    format_type = detect_format(lines)
    if not format_type:
        print("Error: Unrecognized pidstat format.")
        return

    for line in lines:
        if "^Linux" in line or "Time" in line or "UID" in line or "USER" in line:  # Ignore headers
            continue
        parsed_data = parse_pidstat_line_short(line) if format_type == "short" else parse_pidstat_line_verbose(line)
        if not parsed_data:
            continue

        user, usr, system, wait, cpu, command = parsed_data
        key = user if group_by == "user" else command

        if debug:
            print(f"DEBUG: User={user}, usr={usr}, system={system}, wait={wait}, cpu={cpu}, Command={command}")

        usage_data[key]['usr'] += usr
        usage_data[key]['system'] += system
        usage_data[key]['wait'] += wait
        usage_data[key]['cpu'] += cpu
        usage_data[key]['count'] += 1

        total_usr += usr
        total_system += system
        total_wait += wait
        total_cpu += cpu
        total_count += 1

    # Sort results
    sorted_usage = sorted(usage_data.items(), key=lambda x: x[1][sort_by], reverse=True)
    if group_by == "command":
        sorted_usage = sorted_usage[:10]  # Limit to top 10 commands

    print(f"{'%usr':<10} {'%system':<10} {'%wait':<10} {'%CPU':<10} {'Count':<8} {'User/Command':<20}")
    print("-" * 70)
    for key, usage in sorted_usage:
        print(f"{usage['usr']:<10.2f} {usage['system']:<10.2f} {usage['wait']:<10.2f} {usage['cpu']:<10.2f} {usage['count']:<8} {key:<20}")
    print("-" * 70)
    print(f"{total_usr:<10.2f} {total_system:<10.2f} {total_wait:<10.2f} {total_cpu:<10.2f} {total_count:<8} {'Total':<20}")

def main():
    parser = argparse.ArgumentParser(
        description="Analyze CPU utilization data from pidstat output.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "filename",
        nargs="?",
        default="sos_commands/process/pidstat_-p_ALL_-rudvwsRU_--human_-h",
        help="Path to the input file containing CPU utilization data."
    )
    parser.add_argument(
        "-u", "--user",
        action="store_true",
        help="Show CPU usage per user, sorted by total %CPU."
    )
    parser.add_argument(
        "-c", "--command",
        action="store_true",
        help="Show CPU usage per command, sorted by total %CPU. Displays only the top 10 commands."
    )
    parser.add_argument(
        "--sort",
        choices=["usr", "system", "wait", "cpu", "count"],
        default="cpu",
        help="Field to sort by. Defaults to 'cpu'."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode to print matched values."
    )
    args = parser.parse_args()

    group_by = "user" if args.user else "command" if args.command else "user"
    calculate_usage(args.filename, group_by, args.sort, args.debug)

if __name__ == "__main__":
    main()
