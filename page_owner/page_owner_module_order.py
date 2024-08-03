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

# Function to extract module name from a line
def extract_module(line):
    match = re.search(r'\[([a-zA-Z0-9_]+)\]', line)
    if match:
        return match.group(1)
    return ""

# Process the file
def process_file(file_path):
    allocations = defaultdict(lambda: {"count": 0, "pages": 0})
    in_allocation = False
    allocation_counted = False
    num_pages = 0
    order = 0

    with open(file_path, 'r') as file:
        for line in file:
            if "Page allocated" in line:
                in_allocation = True
                match = re.search(r'order ([0-9]+)', line)
                if match:
                    order = int(match.group(1))
                    num_pages = 2 ** order
            
            if re.match(r'^\s*$', line):
                in_allocation = False
                allocation_counted = False
            
            if in_allocation and not allocation_counted:
                module = extract_module(line)
                if module:
                    allocations[(module, order)]["count"] += 1
                    allocations[(module, order)]["pages"] += num_pages
                    allocation_counted = True

    print("{:>10} {:>10} {:>10} {:>10} {:>10}".format("Count", "Pages", "Kbytes", "Order", "Module"))
    sorted_allocations = sorted(allocations.items(), key=lambda x: (x[0][0], x[0][1]))
    for (module, order), data in sorted_allocations:
        print("{:>10} {:>10} {:>10} {:>10} {:<10}".format(data["count"], data["pages"], data["pages"] * 4, order, module))

# Process the input file
process_file(input_file)
