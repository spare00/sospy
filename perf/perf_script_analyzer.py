#!/usr/bin/env python3

import sys
import argparse
from collections import Counter

def parse_perf_script(file_path, target_pid=None, target_cmd=None):
    """
    Parses the perf script output and extracts full stack traces.
    If a PID or command name is specified, filters call traces accordingly.
    """
    with open(file_path, "r") as f:
        lines = f.readlines()

    traces = []
    current_trace = []
    header = None  # Stores the process line with PID, timestamp, and cycles
    include_trace = target_pid is None and target_cmd is None  # If no filter, include all

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect new stack trace start (process line format: sipMgr 242277 [XXX] timestamp: cycles:)
        parts = line.split()
        if len(parts) >= 4 and parts[0].isalpha() and parts[1].isdigit() and "[" in parts[2]:
            cmd = parts[0]  # Command name (e.g., sipMgr)
            pid = parts[1]  # Process ID

            if (target_pid and pid == target_pid) or (target_cmd and cmd == target_cmd) or (target_pid is None and target_cmd is None):
                include_trace = True
            else:
                include_trace = False

            if include_trace:
                if current_trace:
                    traces.append((header, tuple(current_trace)))  # Store previous trace
                header = line  # Store new header line
                current_trace = []

        elif include_trace:
            current_trace.append(line)  # Collect function calls

    if include_trace and current_trace:
        traces.append((header, tuple(current_trace)))  # Store last trace

    return traces

def find_top_call_traces(file_path, target_pid=None, target_cmd=None, top_n=10):
    """
    Finds the top N most common full call trace patterns.
    """
    traces = parse_perf_script(file_path, target_pid, target_cmd)

    # Count occurrences of call stacks (excluding process headers)
    trace_counter = Counter(trace for _, trace in traces)

    if not trace_counter:
        print(f"No matching call traces found for {'PID ' + target_pid if target_pid else 'command ' + target_cmd if target_cmd else 'all processes'}.\n")
        return

    filter_msg = f"for PID {target_pid}" if target_pid else (f"for command {target_cmd}" if target_cmd else "for all processes")
    print(f"Top {top_n} most common call trace patterns {filter_msg}:\n")

    for i, (trace, count) in enumerate(trace_counter.most_common(top_n), 1):
        # Find the first occurrence of this stack to retrieve its original process header
        header = next(h for h, t in traces if t == trace)

        print(f"#{i}: Occurrences: {count}")
        print(header)  # Print the process line (e.g., sipMgr 242277 [017] ...)
        for func in trace:
            print(f"    {func}")  # Preserve original formatting with indentation
        print("-" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze perf script output and find common call trace patterns.")
    parser.add_argument("file", help="Path to the perf script output file")
    parser.add_argument("-p", "--pid", help="Process ID (PID) to filter call traces (optional)")
    parser.add_argument("-c", "--cmd", help="Command name to filter call traces (optional)")

    args = parser.parse_args()

    # Ensure -p and -c are not both used
    if args.pid and args.cmd:
        print("Error: -p (PID) and -c (command) options cannot be used together.")
        sys.exit(1)

    find_top_call_traces(args.file, args.pid, args.cmd)

