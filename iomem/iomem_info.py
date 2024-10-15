#!/usr/bin/env python3

import re
import argparse

# Function to parse memory addresses and calculate the size
def parse_iomem(file_path, reserved_only):
    hierarchy = []  # Stack to keep track of the hierarchy
    printed_hierarchy = []  # To track if we printed part of the hierarchy
    size = None  # Declare size here to avoid UnboundLocalError

    with open(file_path, 'r') as file:
        for line in file:
            # Use regex to find the memory range (e.g., '00000000-0009ffff')
            match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
            if match:
                start = int(match.group(1), 16)  # Start address (hex to int)
                end = int(match.group(2), 16)  # End address (hex to int)
                size = (end - start + 1)  # Calculate the size

            # Track hierarchy
            if not re.match(r'^\s', line):  # Top-level entry, no indent
                hierarchy = [line]  # Start a new hierarchy
            else:  # Indented lines, part of the current hierarchy
                hierarchy.append(line)

            # If the line contains "Reserved", print its hierarchy only once
            if "Reserved" in line:
                if hierarchy not in printed_hierarchy:
                    # Print the entire hierarchy leading to this line
                    for parent in hierarchy[:-1]:  # Avoid printing the reserved line itself twice
                        print(f"{parent.rstrip()}")
                    printed_hierarchy.append(hierarchy.copy())  # Mark this hierarchy as printed

                # Print the current Reserved line
                print(f"{line.rstrip()}\t {size} ({size / (2**20):.2f} MB)")

# Function to handle other options
def parse_full_iomem(file_path):
    total_size = 0  # Variable to keep track of total memory size
    size = None  # Declare size here to avoid UnboundLocalError

    with open(file_path, 'r') as file:
        for line in file:
            # Use regex to find the memory range (e.g., '00000000-0009ffff')
            match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
            if match:
                start = int(match.group(1), 16)  # Start address (hex to int)
                end = int(match.group(2), 16)  # End address (hex to int)
                size = (end - start + 1)  # Calculate the size
                total_size += size  # Add to total size

                # Print the current line with calculated size
                print(f"{line.rstrip()}\t {size} ({size / (2**20):.2f} MB)")

    # Print the total memory size at the end
    print(f"\nTotal memory: {total_size} bytes ({total_size / (2**30):.2f} GB)")

# Main function to handle command-line arguments
def main():
    parser = argparse.ArgumentParser(description="Parse /proc/iomem-like file")
    parser.add_argument('file', type=str, help='The file to parse (e.g., /proc/iomem)')
    parser.add_argument('-d', action='store_true', help='Show only lines starting with no indentation')
    parser.add_argument('-t', action='store_true', help='Show the total memory size of the output')
    parser.add_argument('-s', type=str, help='Show only lines matching the provided regex')
    parser.add_argument('-r', action='store_true', help='Show Reserved lines with their hierarchy')
    args = parser.parse_args()

    # Call the appropriate function based on the options
    if args.r:
        parse_iomem(args.file, args.r)
    else:
        parse_full_iomem(args.file)

if __name__ == "__main__":
    main()
