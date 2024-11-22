#!/usr/bin/env python3

import sys
import argparse
from pathlib import Path

def parse_file(filename, show_online=False, show_offline=False):
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()

        # Always include lines containing these keywords
        keywords = ['Memory block size:', 'Total online memory', 'Total offline memory']
        filtered_lines = []
        keyword_lines = [line for line in lines if any(keyword in line for keyword in keywords)]

        for line in lines:
            if any(keyword in line for keyword in keywords):
                continue  # Skip these lines for now (we'll add them at the end)
            elif show_online and 'online' in line and 'offline' not in line:
                filtered_lines.append(line)
            elif show_offline and 'offline' in line:
                filtered_lines.append(line)
            elif not show_online and not show_offline:
                filtered_lines.append(line)  # Show all if no flags are set

        # Append keyword lines to the bottom of the output
        return filtered_lines + keyword_lines

    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.", file=sys.stderr)
        sys.exit(1)

def main():
    # Define the default file path
    default_filename = "sos_commands/memory/lsmem_-a_-o_RANGE_SIZE_STATE_REMOVABLE_ZONES_NODE_BLOCK"

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Show memory blocks from the input file.")
    parser.add_argument("filename", nargs="?", default=default_filename, help="Input file containing memory block details (default: sos_commands/memory/lsmem_-a_-o_RANGE_SIZE_STATE_REMOVABLE_ZONES_NODE_BLOCK).")
    parser.add_argument("-o", "--online", action="store_true", help="Show only online memory blocks.")
    parser.add_argument("-f", "--offline", action="store_true", help="Show only offline memory blocks.")
    args = parser.parse_args()

    # Check for mutually exclusive options
    if args.online and args.offline:
        print("Error: Options '-o' and '-f' cannot be used together.", file=sys.stderr)
        sys.exit(1)

    # Process the file
    filename = args.filename
    if not Path(filename).exists():
        print(f"Error: File '{filename}' not found.", file=sys.stderr)
        sys.exit(1)

    lines = parse_file(filename, show_online=args.online, show_offline=args.offline)

    # Print the results
    for line in lines:
        print(line.strip())

if __name__ == "__main__":
    main()
