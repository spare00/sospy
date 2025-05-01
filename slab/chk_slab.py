#!/usr/bin/env python3

import sys
import argparse

DEFAULT_TOP_N = 10
PAGE_SIZE_KB = 4  # Assuming 4 KB pages

def parse_slabinfo(file_path):
    try:
        with open(file_path, 'r') as file:
            lines = file.readlines()

        slab_data = []
        total_memory_mib = 0

        for line in lines[2:]:  # Skip headers
            fields = line.split()
            if len(fields) < 7:
                continue

            name = fields[0]
            try:
                pagesperslab = int(fields[5])
                num_slabs = int(fields[-2])
            except ValueError:
                continue

            memory_usage_mib = pagesperslab * num_slabs * PAGE_SIZE_KB / 1024
            slab_data.append((memory_usage_mib, name))
            total_memory_mib += memory_usage_mib

        # Always return full data, selection is handled separately
        return slab_data, total_memory_mib

    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error processing the file: {e}", file=sys.stderr)
        sys.exit(1)

def format_slab_data(slab_data, total_memory_mib):
    result = []
    header = f"{'Memory (MiB)':>15} | {'Slab Name':<20}"
    separator = "-" * len(header)
    result.append(header)
    result.append(separator)

    for size_mib, name in slab_data:
        result.append(f"{size_mib:15.1f} | {name:<30}")

    total_memory_gb = total_memory_mib / 1024
    result.append(separator)
    result.append(f"{'Total':>15} | {total_memory_mib:.1f} MiB ({total_memory_gb:.2f} GB)")

    return result

def main():
    parser = argparse.ArgumentParser(description="Analyze and display slab memory usage.")
    parser.add_argument("file", nargs="?", default="proc/slabinfo", help="Path to the slabinfo file (default: /proc/slabinfo).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-a", "--all", action="store_true", help="Show all slabs.")
    group.add_argument("-l", "--top", type=int, nargs="?", const=DEFAULT_TOP_N,
                       help=f"Show top N slabs by memory usage (default: {DEFAULT_TOP_N}).")

    args = parser.parse_args()

    # Get slabinfo data
    slab_data, total_memory_mib = parse_slabinfo(args.file)

    # Determine how many entries to show
    if args.all:
        display_data = sorted(slab_data, key=lambda x: x[0], reverse=True)
    else:
        top_n = args.top if args.top is not None else DEFAULT_TOP_N
        display_data = sorted(slab_data, key=lambda x: x[0], reverse=True)[:top_n]

    # Print formatted result
    formatted_data = format_slab_data(display_data, total_memory_mib)
    for line in formatted_data:
        print(line)

if __name__ == "__main__":
    main()
