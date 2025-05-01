#!/usr/bin/env python3

import sys
import argparse
from pathlib import Path
import re
from collections import defaultdict

# Helper function to convert memory size to GB
def convert_to_gb(size_str):
    size_units = {"K": 1 / (1024**2), "M": 1 / 1024, "G": 1, "T": 1024}
    match = re.match(r"(\d+)([KMGT])", size_str)
    if match:
        size, unit = match.groups()
        return int(size) * size_units[unit]
    return 0  # Return 0 if parsing fails

def parse_file(filename, show_online=False, show_offline=False, show_per_node=False):
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()

        # Skip header line
        lines = lines[1:]

        # Always include these summary lines
        keywords = ['Memory block size:', 'Total online memory', 'Total offline memory']
        filtered_lines = []
        keyword_lines = [line for line in lines if any(keyword in line for keyword in keywords)]

        # Per-node memory tracking
        node_memory = defaultdict(lambda: {"online": 0, "offline": 0})

        for line in lines:
            if any(keyword in line for keyword in keywords):
                continue  # Skip summary lines for now
            
            parts = line.split()
            if len(parts) < 6:
                continue  # Skip malformed lines

            size_str = parts[1]  # SIZE column
            state = parts[2].lower()  # STATE column
            node = parts[5]  # NODE column

            if not node.isdigit():
                continue  # Skip invalid node entries

            size_gb = convert_to_gb(size_str)
            node_memory[int(node)][state] += size_gb

            if show_online and state == "online":
                filtered_lines.append(line)
            elif show_offline and state == "offline":
                filtered_lines.append(line)
            elif not show_online and not show_offline and not show_per_node:
                filtered_lines.append(line)  # Show all if no flags are set

        # Append per-node memory summary
        if show_per_node:
            filtered_lines.append("\nPer-Node Memory Summary:")
            for node, sizes in sorted(node_memory.items()):
                filtered_lines.append(f"Node {node}: Online: {sizes['online']:.2f} GB, Offline: {sizes['offline']:.2f} GB")

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
    parser.add_argument("-n", "--node-summary", action="store_true", help="Show total memory size (online/offline) per node.")

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

    lines = parse_file(filename, show_online=args.online, show_offline=args.offline, show_per_node=args.node_summary)

    # Print the results
    for line in lines:
        print(line.strip())

if __name__ == "__main__":
    main()
