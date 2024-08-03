#!/usr/bin/env python3

import sys
import os
import re
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

def extract_module(line):
    match = re.search(r'\[(\w+)\]', line)
    if match:
        return match.group(1)
    return None

def extract_allocations(file_path):
    allocations = defaultdict(lambda: {"pages": 0, "count": 0})
    current_trace = []
    in_trace = False
    order = 0
    module = None

    with open(file_path, 'r') as file:
        for line in file:
            if line.startswith("Page allocated via order"):
                if current_trace:
                    if module:
                        allocations[module]["pages"] += 2 ** order
                        allocations[module]["count"] += 1
                    current_trace = []
                    module = None
                in_trace = True
                current_trace.append(line.strip())
                order = int(line.split("order")[1].split(",")[0].strip())
            elif in_trace and line.strip() and not line.startswith("PFN"):
                current_trace.append(line.strip())
                if not module:
                    module = extract_module(line)
            elif in_trace and not line.strip():
                in_trace = False
                if current_trace and module:
                    allocations[module]["pages"] += 2 ** order
                    allocations[module]["count"] += 1
                    current_trace = []
                    module = None

    return allocations

# Extract allocations from the input file
allocations = extract_allocations(input_file)

# Sort the allocations by memory in descending order and get the top 10
sorted_allocations = sorted(allocations.items(), key=lambda item: item[1]["pages"], reverse=True)[:10]

# Print the memory allocations per module with improved formatting
print(f"{'Module':<20} {'Allocations':>12} {'Memory (GB)':>15}")
print("="*50)
for module, data in sorted_allocations:
    pages = data["pages"]
    memory_gb = pages * 4 / (1024 * 1024)  # Convert KB to GB
    print(f"{module:<20} {data['count']:>12} {memory_gb:>15.2f}")
