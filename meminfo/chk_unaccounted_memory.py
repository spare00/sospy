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
          "PageTables", "Percpu", "Hugetlb", "AnonPages", "Active(anon)", "Inactive(anon)"]

meminfo = {}

# Read and parse the meminfo file
with open(filename, 'r') as f:
    for line in f:
        parts = line.split()
        key = parts[0].rstrip(':')
        if key in fields:
            meminfo[key] = int(parts[1])

# Determine how to handle AnonPages
if "AnonPages" not in meminfo:
    if "Active(anon)" in meminfo and "Inactive(anon)" in meminfo:
        # Calculate AnonPages as the sum of Active(anon) and Inactive(anon)
        meminfo["AnonPages"] = meminfo["Active(anon)"] + meminfo["Inactive(anon)"]
        show_anonpages = False  # Do not show AnonPages, show Active(anon) and Inactive(anon) instead
    else:
        print("Error: AnonPages is not available and cannot be calculated from Active(anon) and Inactive(anon).")
        sys.exit(1)
else:
    # AnonPages is present, so we won't show Active(anon) and Inactive(anon)
    show_anonpages = True

# Check if MemTotal is in the parsed data
if "MemTotal" not in meminfo:
    print("Error: MemTotal not found")
    sys.exit(1)

# Calculate total used memory and unaccounted memory
total_memory = meminfo["MemTotal"]
accounted_memory_fields = [field for field in fields if field in meminfo and field != "MemTotal"]

# Adjust fields to show based on whether AnonPages or Active(anon) + Inactive(anon) is used
if not show_anonpages:
    accounted_memory_fields.remove("AnonPages")
else:
    accounted_memory_fields.remove("Active(anon)")
    accounted_memory_fields.remove("Inactive(anon)")

accounted_memory = sum(meminfo[field] for field in accounted_memory_fields)
unaccounted_memory = total_memory - accounted_memory

# Print the formula and values if verbose mode is enabled
if verbose:
    formula = f"Unaccounted Memory = MemTotal - ({' + '.join(accounted_memory_fields)})"
    print("Formula used for calculation:")
    print(f"  {formula}")
    print(f"  Unaccounted Memory = {total_memory} - ({accounted_memory})\n")

# Print the memory information with proper alignment and enhanced formatting
header = f"{'Field':<15} {'Size (kB)':>15}"
print(header)
print("=" * len(header))

for key in fields:
    if key in accounted_memory_fields:
        print(f"{key:<15} {meminfo[key]:>15,} kB")

# Print the unaccounted memory with a separator
print("=" * len(header))
print(f"{'Unaccounted:':<15} {unaccounted_memory:>15,} kB ({unaccounted_memory / (1024 * 1024):.2f} GB)\n")
