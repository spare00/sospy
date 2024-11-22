#!/usr/bin/env python3

import sys
import argparse

def parse_slabinfo(file_path, show_top=False):
    try:
        with open(file_path, 'r') as file:
            lines = file.readlines()

        # Skip the first two lines of /proc/slabinfo, which are headers
        slab_data = []
        total_memory_mib = 0

        for line in lines[2:]:  # Skip the headers
            fields = line.split()
            if len(fields) < 7:  # Ensure there are enough columns
                continue

            name = fields[0]  # Slab name
            pagesperslab = int(fields[5])  # $6: Pages per slab
            num_slabs = int(fields[-2])  # $(NF-1): Number of slabs in use

            # Calculate memory usage in MiB
            memory_usage_mib = pagesperslab * num_slabs * 4 / 1024  # Convert to MiB
            slab_data.append((memory_usage_mib, name))
            total_memory_mib += memory_usage_mib

        if show_top:
            # Sort by size in descending order and take the top 10
            slab_data = sorted(slab_data, key=lambda x: x[0], reverse=True)[:10]

        return slab_data, total_memory_mib

    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error processing the file: {e}", file=sys.stderr)
        sys.exit(1)

def format_slab_data(slab_data, total_memory_mib):
    result = []
    header = f"{'Memory (MiB)':>15} | {'Slab Name':<30}"
    separator = "-" * len(header)
    result.append(header)
    result.append(separator)

    for size_mib, name in slab_data:
        result.append(f"{size_mib:15.1f} | {name:<30}")

    # Add total memory usage at the bottom
    total_memory_gb = total_memory_mib / 1024  # Convert total memory to GB
    result.append(separator)
    result.append(f"{'Total':>15} | {total_memory_mib:10.1f} MiB ({total_memory_gb:.2f} GB)")

    return result

def main():
    parser = argparse.ArgumentParser(description="Analyze and display slab memory usage.")
    parser.add_argument("file", nargs="?", default="/proc/slabinfo", help="Path to the slabinfo file (default: /proc/slabinfo).")
    parser.add_argument("-s", "--top-slabs", action="store_true", help="Show the top 10 slabs by memory usage.")
    args = parser.parse_args()

    # Parse the slabinfo file
    slab_data, total_memory_mib = parse_slabinfo(args.file, show_top=args.top_slabs)

    # Format and print the results
    formatted_data = format_slab_data(slab_data, total_memory_mib)
    for line in formatted_data:
        print(line)

if __name__ == "__main__":
    main()
