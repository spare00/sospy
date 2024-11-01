#!/usr/bin/env python3

import re
import argparse
import sys

# Set up argument parser
parser = argparse.ArgumentParser(description="Calculate memory regions (System RAM, Reserved, Device Regions) from a specified file.")
parser.add_argument("filename", type=str, help="Path to the file to parse (e.g., /proc/iomem)")

# Parse arguments
args = parser.parse_args()

# Define categories
system_ram_total = 0
device_regions_total = 0
reserved_ranges = []  # Store all reserved ranges as tuples of (start, end)

# Try to open and read the file
try:
    with open(args.filename, 'r') as f:
        for line in f:
            # Match memory range and description, allowing for leading whitespace (indented lines)
            match = re.match(r'\s*([0-9a-fA-F]+)-([0-9a-fA-F]+) : (.+)', line)
            if match:
                start_addr = int(match.group(1), 16)
                end_addr = int(match.group(2), 16)
                size = end_addr - start_addr + 1  # Calculate size in bytes

                # Categorize based on description, capturing indented "Reserved" as well
                description = match.group(3).strip().lower()
                if "system ram" in description:
                    system_ram_total += size
                elif "reserved" in description:
                    reserved_ranges.append((start_addr, end_addr))  # Collect all reserved ranges
                else:
                    device_regions_total += size
except FileNotFoundError:
    print(f"Error: File '{args.filename}' not found.")
    sys.exit(1)
except PermissionError:
    print(f"Error: Permission denied to read '{args.filename}'.")
    sys.exit(1)

# Function to consolidate overlapping and adjacent Reserved ranges
def consolidate_ranges(ranges):
    # Sort ranges by starting address
    ranges.sort()
    consolidated = []

    for start, end in ranges:
        if consolidated and start <= consolidated[-1][1] + 1:
            # Merge overlapping or adjacent ranges
            consolidated[-1] = (consolidated[-1][0], max(consolidated[-1][1], end))
        else:
            # Add new range to consolidated list
            consolidated.append((start, end))

    return consolidated

# Consolidate Reserved ranges and calculate total
consolidated_reserved_ranges = consolidate_ranges(reserved_ranges)
reserved_total = sum(end - start + 1 for start, end in consolidated_reserved_ranges)

# Convert bytes to megabytes
system_ram_mb = system_ram_total / (1024 * 1024)
reserved_mb = reserved_total / (1024 * 1024)
device_regions_mb = device_regions_total / (1024 * 1024)

# Print results
print(f"System RAM: {system_ram_mb:.2f} MB")
print(f"Reserved Memory: {reserved_mb:.2f} MB")
print(f"Device Regions: {device_regions_mb:.2f} MB")
