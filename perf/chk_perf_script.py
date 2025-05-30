#!/usr/bin/env python3

import sys
import argparse
from collections import Counter
import re

def normalize_function_line(line, ignore_addr=False):
    """
    Optionally removes memory offsets and symbol addresses for better call trace grouping.
    Example:
        If ignore_addr=True:
            "ffffffff9d2f002e __stop_notes+0x122c82 ([kernel.kallsyms])"
            → "__stop_notes ([kernel.kallsyms])"
        If ignore_addr=False (default):
            "ffffffff9d2f002e __stop_notes+0x122c82 ([kernel.kallsyms])"
            → "ffffffff9d2f002e __stop_notes+0x122c82 ([kernel.kallsyms])"
    """
    if ignore_addr:
        # Remove addresses (hex values at the start of the line)
        line = re.sub(r'^[a-fA-F0-9]+ ', '', line)

        # Remove memory offsets in function names (e.g., `+0x123abc`)
        line = re.sub(r'\+0x[a-fA-F0-9]+', '', line)

    return line.strip()

def parse_perf_script(file_path, target_pid=None, target_cmd=None, ignore_addr=False):
    """
    Parses the perf script output and extracts full stack traces.
    If a PID or command name is specified, filters call traces accordingly.
    """
    with open(file_path, "r") as f:
        lines = f.readlines()

    traces = []
    current_trace = []
    header = None  # Stores the process name only
    include_trace = target_pid is None and target_cmd is None  # If no filter, include all

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect new stack trace start (process line format: sipMgr 242277 [XXX] TIMESTAMP: cycles:)
        parts = line.split()
        if len(parts) >= 4 and parts[0].isalpha() and parts[1].isdigit() and "[" in parts[2]:  
            cmd = parts[0]  # Command name (e.g., sipMgr)
            pid = parts[1]  # Process ID

            if target_pid and pid == target_pid:
                include_trace = True
            elif target_cmd and cmd == target_cmd:
                include_trace = True
            elif target_pid is None and target_cmd is None:
                include_trace = True  # No filter, include all
            else:
                include_trace = False

            if include_trace:
                if current_trace:
                    traces.append((header, tuple(current_trace)))  # Store previous trace
                
                # Simplify header to only include process name
                header = f"{cmd}:"

                current_trace = []

        elif include_trace:
            current_trace.append(normalize_function_line(line, ignore_addr))  # Apply optional address normalization

    if include_trace and current_trace:
        traces.append((header, tuple(current_trace)))  # Store last trace

    return traces

def find_top_call_traces(file_path, target_pid=None, target_cmd=None, n_top=5, ignore_addr=False):
    """
    Finds the top N most common full call trace patterns.
    """
    traces = parse_perf_script(file_path, target_pid, target_cmd, ignore_addr)

    # Count occurrences of call stacks (grouped by function calls only, with minimal header)
    trace_counter = Counter(trace for _, trace in traces)

    if not trace_counter:
        print(f"No matching call traces found for {'PID ' + target_pid if target_pid else 'command ' + target_cmd if target_cmd else 'all processes'}.\n")
        return

    filter_msg = f"for PID {target_pid}" if target_pid else (f"for command {target_cmd}" if target_cmd else "for all processes")
    if ignore_addr:
        filter_msg += " (Ignoring Symbol Addresses & Offsets)"
    print(f"Top {n_top} most common call trace patterns {filter_msg}:\n")

    for i, (trace, count) in enumerate(trace_counter.most_common(n_top), 1):
        # Find the first occurrence of this stack to retrieve its original process header
        header = next(h for h, t in traces if t == trace)

        print(f"#{i}: Occurrences: {count}")
        print(header)  # Print the simplified process line (e.g., sipMgr:)
        for func in trace:
            print(f"    {func}")  # Preserve original formatting with indentation
        print("-" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze perf script output and find common call trace patterns.")
    parser.add_argument("file", help="Path to the perf script output file")
    parser.add_argument("-p", "--pid", help="Process ID (PID) to filter call traces (optional)")
    parser.add_argument("-c", "--cmd", help="Command name to filter call traces (optional)")
    parser.add_argument("-n", "--n_top", type=int, default=5, help="Number of top call traces to display (default: 5)")
    parser.add_argument("-g", "--ignore-addr", action="store_true", help="Ignore symbol addresses and memory offsets for better grouping")

    args = parser.parse_args()

    # Ensure -p and -c are not both used
    if args.pid and args.cmd:
        print("Error: -p (PID) and -c (command) options cannot be used together.")
        sys.exit(1)

    find_top_call_traces(args.file, args.pid, args.cmd, args.n_top, args.ignore_addr)
