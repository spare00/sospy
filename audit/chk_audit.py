#!/usr/bin/env python3

import os
import re
import argparse
import subprocess
from datetime import datetime
from collections import defaultdict
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

    for block in blocks:
        lines = block.strip().splitlines()
        event_type = None
        event_time = None

        for line in lines:
            if line.startswith("type=") and not event_type:
                match = re.search(r"type=(\w+)", line)
                if match:
                    event_type = match.group(1)
            elif line.startswith("time->") and not event_time:
                time_str = line.split("->", 1)[1].strip()
                try:
                    event_time = datetime.strptime(time_str, "%a %b %d %H:%M:%S %Y")
                    timestamps.append(event_time)
                except ValueError:
                    continue

        if event_type:
            event_counts[event_type] += 1
        else:
            event_counts["UNKNOWN"] += 1

    summary = {
        "start_time": min(timestamps).isoformat() if timestamps else None,
        "end_time": max(timestamps).isoformat() if timestamps else None,
        "event_counts": dict(event_counts),
        "total_events": sum(event_counts.values())
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

def main():
    parser = argparse.ArgumentParser(description="Audit log analyzer using ausearch")
    parser.add_argument("audit_log_file", nargs="?", default="var/log/audit/audit.log",
                        help="Path to audit.log file (default: var/log/audit/audit.log)")
    parser.add_argument("--start", help="Start time (e.g. '2024-05-21 00:00:00')")
    parser.add_argument("--end", help="End time (e.g. '2024-05-21 23:59:59')")
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

    try:
        output = run_ausearch(audit_log, args.start, args.end, debug=args.debug)
        summary = parse_ausearch_output(output)
        print_summary(summary)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
