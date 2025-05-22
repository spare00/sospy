#!/usr/bin/env python3

import os
import re
import argparse
import subprocess
from datetime import datetime
from collections import defaultdict, Counter
import sys

def run_ausearch(audit_log_path, start_time=None, end_time=None, debug=False):
    cmd = ["ausearch", "-if", audit_log_path]
    if start_time:
        cmd.extend(["--start", start_time])
    if end_time:
        cmd.extend(["--end", end_time])
    if debug:
        print(f"[DEBUG] Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ausearch failed: {result.stderr.strip()}")
    return result.stdout

def parse_ausearch_output(output):
    blocks = output.split("----\n")
    event_counts = defaultdict(int)
    timestamps = []
    command_counter = Counter()
    command_timestamps = defaultdict(list)
    comm_counter = Counter()
    comm_timestamps = defaultdict(list)

    for block in blocks:
        lines = block.strip().splitlines()
        event_type = None
        event_time = None
        argv = {}
        is_execve = False
        comm_val = None

        for line in lines:
            if line.startswith("type=EXECVE"):
                is_execve = True
            if line.startswith("type=") and not event_type:
                match = re.search(r"type=(\w+)", line)
                if match:
                    event_type = match.group(1)
            if line.startswith("time->") and not event_time:
                try:
                    event_time = datetime.strptime(line.split("->", 1)[1].strip(), "%a %b %d %H:%M:%S %Y")
                    timestamps.append(event_time)
                except ValueError:
                    pass
            if "comm=" in line and not comm_val:
                match = re.search(r'comm="([^"]+)"', line)
                if match:
                    comm_val = match.group(1)
            arg_match = re.findall(r'a(\d+)=(".*?"|\S+)', line)
            for idx, val in arg_match:
                argv[int(idx)] = val.strip('"')

        if event_type:
            event_counts[event_type] += 1
        else:
            event_counts["UNKNOWN"] += 1

        if is_execve and argv:
            command = ' '.join(argv[i] for i in sorted(argv))
            command_counter[command] += 1
            if event_time:
                command_timestamps[command].append(event_time)

        if comm_val:
            comm_counter[comm_val] += 1
            if event_time:
                comm_timestamps[comm_val].append(event_time)

    summary = {
        "start_time": min(timestamps).isoformat() if timestamps else None,
        "end_time": max(timestamps).isoformat() if timestamps else None,
        "event_counts": dict(event_counts),
        "total_events": sum(event_counts.values()),
        "top_commands": command_counter.most_common(10),
        "command_timestamps": command_timestamps,
        "top_comm": comm_counter.most_common(10),
        "comm_timestamps": comm_timestamps
    }

    return summary

def print_summary(summary):
    print("\n=== Audit Log Summary ===")
    print(f"Start Time   : {summary['start_time']}")
    print(f"End Time     : {summary['end_time']}")
    print("\nEvent Type Counts:")
    for evt, count in sorted(summary['event_counts'].items()):
        print(f"  {evt:<15} {count}")
    print(f"\nTotal Events : {summary['total_events']}\n")

    if summary["top_commands"]:
        print("Top 10 Commands (EXECVE):")
        for cmd, count in summary["top_commands"]:
            print(f"  {cmd:<50} {count}")
        print("")

    if summary["top_comm"]:
        print("Top 10 Processes (comm=):")
        for comm, count in summary["top_comm"]:
            print(f"  {count:>5} comm=\"{comm}\"")
        print("")

def show_command_details(summary, query):
    cmd_times = summary['command_timestamps'].get(query)
    comm_times = summary['comm_timestamps'].get(query)

    if cmd_times:
        print(f"\n=== Details for Command (EXECVE): '{query}' ===")
        print(f"Occurrences : {len(cmd_times)}")
        print(f"First Seen  : {min(cmd_times).isoformat()}")
        print(f"Last Seen   : {max(cmd_times).isoformat()}")
    elif comm_times:
        print(f"\n=== Details for Process (comm=): '{query}' ===")
        print(f"Occurrences : {len(comm_times)}")
        print(f"First Seen  : {min(comm_times).isoformat()}")
        print(f"Last Seen   : {max(comm_times).isoformat()}")
    else:
        print(f"No data found for: '{query}'")

def main():
    parser = argparse.ArgumentParser(description="Audit log analyzer using ausearch")
    parser.add_argument("audit_log_file", nargs="?", default="var/log/audit/audit.log",
                        help="Path to audit.log file (default: var/log/audit/audit.log)")
    parser.add_argument("--start", help="Start time (e.g. '2024-05-21 00:00:00')")
    parser.add_argument("--end", help="End time (e.g. '2024-05-21 23:59:59')")
    parser.add_argument("--details", help="Show detailed stats for a command or comm name")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug output")
    args = parser.parse_args()

    audit_log = args.audit_log_file
    if not os.path.isfile(audit_log):
        print(f"Error: audit log not found at {audit_log}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Using audit log: {audit_log}")
        if args.start:
            print(f"Start time filter: {args.start}")
        if args.end:
            print(f"End time filter: {args.end}")
        if args.details:
            print(f"Detail view for: {args.details}")

    try:
        output = run_ausearch(audit_log, args.start, args.end, debug=args.debug)
        summary = parse_ausearch_output(output)
        print_summary(summary)
        if args.details:
            show_command_details(summary, args.details)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
