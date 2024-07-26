#!/usr/bin/env python3

import sys
import os

# Default path to meminfo
DEFAULT_MEMINFO = "/proc/meminfo"

# Get the filename from the command-line arguments or use the default
filename = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MEMINFO

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
        if parts[0].rstrip(':') in fields:
            meminfo[parts[0]] = int(parts[1])

# Check if MemTotal is in the parsed data
if "MemTotal:" not in meminfo:
    print("Error: MemTotal not found")
    sys.exit(1)

# Calculate total used memory and unaccounted memory
total_memory = meminfo["MemTotal:"]
sum_memory = sum(meminfo.values())
unaccounted_memory = total_memory - (sum_memory - total_memory)

# Print the memory information
for key in fields:
    if key + ":" in meminfo:
        print(f"{key: <12} {meminfo[key + ':']: >12} kB")

# Print the unaccounted memory
print(f"Unaccounted: {unaccounted_memory: >12} kB ({unaccounted_memory / (1024 * 1024):.2f} GB)")
