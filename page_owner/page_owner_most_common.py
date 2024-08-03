#!/usr/bin/env python3

import sys
import os
from collections import defaultdict

# Check if an argument is provided
if len(sys.argv) != 2:
    print("Usage: {} input_file".format(sys.argv[0]))
    sys.exit(1)

# Define the input file from the first argument
input_file = sys.argv[1]

# Check if the input file exists and is readable
if not os.path.isfile(input_file) or not os.access(input_file, os.R_OK):
    print("Error: File '{}' does not exist or is not readable.".format(input_file))
    sys.exit(1)

def extract_call_traces(file_path):
    call_traces = []
    current_trace = []
    in_trace = False
    order = 0

    with open(file_path, 'r') as file:
        for line in file:
            if line.startswith("Page allocated via order"):
                if current_trace:
                    call_traces.append((tuple(current_trace), order))
                    current_trace = []
                in_trace = True
                current_trace.append(line.strip())
                order = int(line.split("order")[1].split(",")[0].strip())
            elif in_trace and line.strip() and not line.startswith("PFN"):
                current_trace.append(line.strip())
            elif in_trace and not line.strip():
                in_trace = False
                if current_trace:
                    call_traces.append((tuple(current_trace), order))
                    current_trace = []

    if current_trace:
        call_traces.append((tuple(current_trace), order))

    return call_traces

def get_most_common_call_traces(call_traces, top_n=3):
    trace_counts = defaultdict(lambda: {"count": 0, "pages": 0})
    
    for trace, order in call_traces:
        trace_counts[trace]["count"] += 1
        trace_counts[trace]["pages"] += 2 ** order
    
    sorted_traces = sorted(trace_counts.items(), key=lambda item: item[1]["count"], reverse=True)
    return sorted_traces[:top_n]

# Extract call traces from the input file
call_traces = extract_call_traces(input_file)

# Get the three most common call traces
most_common_traces = get_most_common_call_traces(call_traces)

# Print the three most common call traces with their counts and memory allocated
print("Top {} most commonly seen call traces:".format(len(most_common_traces)))
for idx, (trace, data) in enumerate(most_common_traces, start=1):
    pages = data["pages"]
    memory_gb = pages * 4 / (1024 * 1024)  # Convert KB to GB
    print("\nCall Trace #{} (Seen {} times, {} pages, {:.2f} GB):".format(
        idx, data["count"], pages, memory_gb))
    for line in trace:
        print(line)
