#!/usr/bin/env python3

import sys
import os

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

# Function to process the file
def process_file(file_path):
    order_dict = {}
    total_pages = 0

    with open(file_path, 'r') as file:
        for line in file:
            if line.startswith("Page"):
                parts = line.split()
                try:
                    order = int(parts[4].strip(','))
                except ValueError:
                    print(f"Skipping line due to parse error: {line.strip()}")
                    continue

                pages = 2 ** order
                total_pages += pages
                if order in order_dict:
                    order_dict[order] += pages
                else:
                    order_dict[order] = pages

    # Print header
    print(f"{'Order':<10} {'Pages':>10} {'Memory (KB)':>15}")
    print("=" * 35)

    # Print sorted order details
    for order, pages in sorted(order_dict.items()):
        print(f"{order:<10} {pages:>10} {pages * 4:>15}")

    # Print total pages and memory
    print("=" * 35)
    print(f"{'Total':<10} {total_pages:>10} {total_pages * 4:>15}")

# Process the input file
process_file(input_file)
