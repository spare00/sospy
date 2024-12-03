#!/usr/bin/env python3

import re
import argparse

# Function to parse and display memory ranges
def parse_iomem(file_path, reserved_only):

    hierarchy = []  # Stack to keep track of the hierarchy
    total_reserved_size = 0  # Variable to track total reserved memory
    size = None  # Declare size here to avoid UnboundLocalError

    # Read all lines of the file at once
    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Iterate over the lines
    for i, line in enumerate(lines):
        # Use regex to find the memory range (e.g., '00000000-0009ffff')
        match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
        if match:
            start = int(match.group(1), 16)  # Start address (hex to int)
            end = int(match.group(2), 16)  # End address (hex to int)
            size = (end - start + 1)  # Calculate the size

        # Track hierarchy (Reset hierarchy if the line is top-level)
        current_indent = len(line) - len(line.lstrip())

        # Continue normal processing for non-top-level option
        if current_indent == 0:  # Top-level entry, no indent
            hierarchy = [line]  # Start a new hierarchy
        else:  # Indented lines, part of the current hierarchy
            hierarchy.append(line)

        # Check if the line contains 'Reserved' (case-insensitive) and print it along with hierarchy
        if "reserved" in line.lower():
            total_reserved_size += size  # Add reserved size to total

            # Print the entire hierarchy leading to this Reserved entry
            for j, parent in enumerate(hierarchy[:-1]):  # Avoid printing the Reserved line itself twice
                match_parent = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', parent)
                if match_parent:
                    # Check if the previous or next line contains "Reserved" or indentation change
                    parent_indent = len(parent) - len(parent.lstrip()) if j > 0 else 0
                    next_parent_indent = len(hierarchy[j + 1]) - len(hierarchy[j + 1].lstrip()) if j + 1 < len(hierarchy) else 0
                    if (parent_indent == 0) or ( "reserved" in lines[i - 1].lower() or "reserved" in lines[i + 1].lower()) or (parent_indent != next_parent_indent ):
                        # Print only if there's an indentation change or 'Reserved' around
                        start_parent = int(match_parent.group(1), 16)
                        end_parent = int(match_parent.group(2), 16)
                        size_parent = (end_parent - start_parent + 1)
                        print(f"{parent.rstrip()}\t {size_parent} ({size_parent / (2**20):.2f} MiB)")

            # Print the current Reserved line
            print(f"{line.rstrip()}\t {size} ({size / (2**20):.2f} MiB)")

            # Check if text line is a child (if it exists and is indented)
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > current_indent:  # Indented line, child of Reserved
                    match_child = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', next_line)
                    if match_child:
                        start_child = int(match_child.group(1), 16)
                        end_child = int(match_child.group(2), 16)
                        size_child = (end_child - start_child + 1)
                        if "reserved" in next_line.lower():
                            total_reserved_size += size_child  # Add child reserved size to total
                        print(f"{next_line.rstrip()}\t {size_child} ({size_child / (2**20):.2f} MiB)")

        # Exclude lines that don't contain 'Reserved' (case-insensitive) and do not cause an indentation change
        elif "reserved" not in line.lower():
            prev_indent = len(lines[i - 1]) - len(lines[i - 1].lstrip()) if i > 0 else 0
            next_indent = len(lines[i + 1]) - len(lines[i + 1].lstrip()) if i + 1 < len(lines) else 0

            # Skip lines where indentation does not change before or after
            if current_indent == prev_indent == next_indent:
                continue  # Skip this line if it's not changing indentation and doesn't contain 'Reserved'

    # Print the total reserved memory size at the end
    print(f"\nTotal reserved memory: {total_reserved_size} bytes ({total_reserved_size / (2**30):.2f} GiB)")

# Function to handle other options
def parse_full_iomem(file_path, top_level_only, search_keyword=None):
    total_size = 0  # Variable to keep track of total memory size
    size = None  # Declare size here to avoid UnboundLocalError

    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Convert the keyword to lowercase for case-insensitive matching
    search_keyword = search_keyword.lower() if search_keyword else None

    # Iterate over the lines
    for i, line in enumerate(lines):
        # Track hierarchy (Reset hierarchy if the line is top-level)
        current_indent = len(line) - len(line.lstrip())

        # If the -t option is provided, only show top-level lines (no indentation)
        if top_level_only and current_indent != 0:
            continue

        # Handle the -s option (show only lines matching the keyword)
        if search_keyword and search_keyword not in line.lower():
            continue

        # Use regex to find the memory range (e.g., '00000000-0009ffff')
        match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
        if match:
            start = int(match.group(1), 16)  # Start address (hex to int)
            end = int(match.group(2), 16)  # End address (hex to int)
            size = (end - start + 1)  # Calculate the size
            total_size += size  # Add to total size

            # Print the current line with calculated size
            print(f"{line.rstrip()}\t {size} ({size / (2**20):.2f} MiB)")

    # Print the total memory size at the end
    print(f"\nTotal memory: {total_size} bytes ({total_size / (2**30):.2f} GiB)")

# Main function to handle command-line arguments
def main():
    parser = argparse.ArgumentParser(description="Parse /proc/iomem-like file")
    parser.add_argument('file', type=str, help='The file to parse (e.g., /proc/iomem)')
    parser.add_argument('-r', action='store_true', help='Show Reserved lines with their hierarchy')
    parser.add_argument('-t', action='store_true', help='Show only top-level lines (no indentation)')
    parser.add_argument('-s', type=str, help='Show only lines matching the provided keyword')
    args = parser.parse_args()

    # Call the appropriate function based on the options
    if args.r:
        parse_iomem(args.file, args.r)
    else:
        parse_full_iomem(args.file, args.t, args.s)

if __name__ == "__main__":
    main()
