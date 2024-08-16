#!/usr/bin/env python3

import sys
import os

# Default path to meminfo
DEFAULT_MEMINFO = "proc/meminfo"

# Check if '-v' is passed as an argument
verbose = '-v' in sys.argv

# Remove '-v' from arguments if present
args = [arg for arg in sys.argv[1:] if arg != '-v']

# Get the filename from the command-line arguments or use the default
filename = args[0] if len(args) > 0 else DEFAULT_MEMINFO

# Check if the file exists
if not os.path.isfile(filename):
    print(f"File not found: {filename}")
    sys.exit(1)

# List of memory fields to look for
fields = ["MemTotal", "MemFree", "Buffers", "Cached", "Slab", "KernelStack",
          "PageTables", "Percpu", "Hugetlb", "AnonPages"]

meminfo = {}

# Read and parse the meminfo file
with open(filename, 'r') as f:
    for line in f:
        parts = line.split()
        key = parts[0].rstrip(':')
        if key in fields:
            meminfo[key] = int(parts[1])

# Check if MemTotal is in the parsed data
if "MemTotal" not in meminfo:
    print("Error: MemTotal not found")
    sys.exit(1)

# Calculate total used memory and unaccounted memory
total_memory = meminfo["MemTotal"]
accounted_memory = sum(meminfo[field] for field in fields if field in meminfo and field != "MemTotal")
unaccounted_memory = total_memory - accounted_memory

# Print the formula and values if verbose mode is enabled
if verbose:
    formula = f"Unaccounted Memory = MemTotal - ({' + '.join([f'{key}' for key in fields if key in meminfo and key != 'MemTotal'])})"
    print("Formula used for calculation:")
    print(formula)
    print(f"Unaccounted Memory = {total_memory} - ({accounted_memory})")
    print()

# Print the memory information
for key in fields:
    if key in meminfo:
        print(f"{key: <12} {meminfo[key]: >12} kB")

# Print the unaccounted memory
print("="*40)
print(f"Unaccounted: {unaccounted_memory: >12} kB ({unaccounted_memory / (1024 * 1024):.2f} GB)")
