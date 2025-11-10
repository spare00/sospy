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
        total_memory_kib = 0

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

            memory_usage_kib = pagesperslab * num_slabs * PAGE_SIZE_KB
            slab_data.append((memory_usage_kib, name))
            total_memory_kib += memory_usage_kib

        return slab_data, total_memory_kib

    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error processing the file: {e}", file=sys.stderr)
        sys.exit(1)

def format_slab_data(slab_data, total_memory_kib, unit):
    result = []

    # Conversion factor and label
    if unit == "K":
        factor = 1
        label = "KiB"
        total_label = "KiB"
    elif unit == "M":
        factor = 1 / 1024
        label = "MiB"
        total_label = "MiB"
    else:
        factor = 1 / (1024 * 1024)
        label = "GiB"
        total_label = "GB"

    header = f"{f'Memory ({label})':>15} | {'Slab Name':<20}"
    separator = "-" * len(header)
    result.append(header)
    result.append(separator)

    for size_kib, name in slab_data:
        size = size_kib * factor
        result.append(f"{size:15.1f} | {name:<30}")

    total = total_memory_kib * factor
    result.append(separator)
    result.append(f"{'Total':>15} | {total_memory_kib * factor:.1f} {total_label}")

    return result

def main():
    parser = argparse.ArgumentParser(description="Analyze and display slab memory usage.")
    parser.add_argument("file", nargs="?", default="proc/slabinfo", help="Path to the slabinfo file (default: /proc/slabinfo).")
    
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-a", "--all", action="store_true", help="Show all slabs.")
    group.add_argument("-l", "--top", type=int, nargs="?", const=DEFAULT_TOP_N,
                       help=f"Show top N slabs by memory usage (default: {DEFAULT_TOP_N}).")
    
    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument("-K", "--kib", action="store_true", help="Display memory in KiB.")
    unit_group.add_argument("-M", "--mib", action="store_true", help="Display memory in MiB.")
    unit_group.add_argument("-G", "--gib", action="store_true", help="Display memory in GiB (default).")

    args = parser.parse_args()

    # Determine display unit
    if args.kib:
        unit = "K"
    elif args.mib:
        unit = "M"
    else:
        unit = "G"

    # Get slabinfo data
    slab_data, total_memory_kib = parse_slabinfo(args.file)

    # Determine how many entries to show
    if args.all:
        display_data = sorted(slab_data, key=lambda x: x[0], reverse=True)
    else:
        top_n = args.top if args.top is not None else DEFAULT_TOP_N
        display_data = sorted(slab_data, key=lambda x: x[0], reverse=True)[:top_n]

    # Print formatted result
    formatted_data = format_slab_data(display_data, total_memory_kib, unit)
    for line in formatted_data:
        print(line)

if __name__ == "__main__":
    main()
