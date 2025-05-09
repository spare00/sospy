#!/usr/bin/env python3

import sys
import os
import argparse

DEFAULT_MEMINFO = "proc/meminfo"

FIELDS = [
    "MemTotal", "MemFree", "Buffers", "Cached", "SwapCached",
    "Active(anon)", "Inactive(anon)", "AnonPages",
    "Unevictable", "Slab", "KernelStack",
    "PageTables", "Percpu",
    "HugePages_Total", "Hugepagesize"
]

def parse_meminfo(path, verbose=False):
    if not os.path.isfile(path):
        print(f"Error: File not found: {path}")
        sys.exit(1)

    meminfo = {}
    with open(path, 'r') as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            key = parts[0].rstrip(':')
            if key in FIELDS:
                try:
                    meminfo[key] = int(parts[1])
                except ValueError:
                    if verbose:
                        print(f"Skipping line due to non-integer value: {line.strip()}")
    return meminfo

def compute_anonpages(meminfo):
    if "AnonPages" in meminfo:
        return True  # show_anonpages = True
    if "Active(anon)" in meminfo and "Inactive(anon)" in meminfo:
        meminfo["AnonPages"] = meminfo["Active(anon)"] + meminfo["Inactive(anon)"]
        return False  # show_anonpages = False
    print("Error: AnonPages is not available and cannot be calculated.")
    sys.exit(1)

def compute_hugepages(meminfo):
    if "HugePages_Total" in meminfo and "Hugepagesize" in meminfo:
        meminfo["HugePages"] = meminfo["HugePages_Total"] * meminfo["Hugepagesize"]
        return
    print("Error: Required HugePages_Total or Hugepagesize not found.")
    sys.exit(1)

def calculate_unaccounted(meminfo, show_anonpages):
    total = meminfo.get("MemTotal")
    if total is None:
        print("Error: MemTotal not found.")
        sys.exit(1)

    accounted = []
    for field in FIELDS:
        if field == "MemTotal":
            continue
        if field in ("Unevictable"):
            continue
        if field == "AnonPages" and not show_anonpages:
            continue
        if field in ("Active(anon)", "Inactive(anon)") and show_anonpages:
            continue
        if field in ("HugePages_Total", "Hugepagesize"):
            continue
        if field in meminfo:
            accounted.append(field)
    if "HugePages" in meminfo:
        accounted.append("HugePages")

    accounted_sum = sum(meminfo[field] for field in accounted)
    return total, accounted_sum, total - accounted_sum, accounted

def print_report(meminfo, total, accounted_fields, accounted_sum, unaccounted, verbose, show_anonpages):
    if verbose:
        formula = " + ".join(f"{field}={meminfo[field]}" for field in accounted_fields)
        print("Formula used for calculation:")
        print(f"  Unaccounted Memory = MemTotal - ({formula})")
        print(f"  Unaccounted Memory = {total} - {accounted_sum}\n")

    header = f"{'Field':<15} {'Size (kB)':>15}   "
    print(header)
    print("=" * len(header))
    print(f"{'MemTotal:':<15} {total:>15,} kB")
    for key in accounted_fields:
        print(f"{key:<15} {meminfo[key]:>15,} kB")
    print("=" * len(header))
    print(f"{'Unaccounted:':<15} {unaccounted:>15,} kB ({unaccounted / (1024 * 1024):.2f} GB)\n")

def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate unaccounted memory from proc/meminfo or a custom file."
    )
    parser.add_argument(
        "filename",
        nargs="?",
        default=DEFAULT_MEMINFO,
        help="Path to the meminfo file (default: proc/meminfo)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Display verbose output with the memory calculation formula"
    )
    parser.add_argument(
        "-u", "--unaccounted",
        dest="unaccounted",
        action="store_true",
        default=True,
        help="Include unaccounted memory calculation (default: enabled)"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    meminfo = parse_meminfo(args.filename, args.verbose)
    show_anonpages = compute_anonpages(meminfo)
    compute_hugepages(meminfo)

    if args.unaccounted:
        total, accounted_sum, unaccounted, accounted_fields = calculate_unaccounted(meminfo, show_anonpages)
        print_report(meminfo, total, accounted_fields, accounted_sum, unaccounted, args.verbose, show_anonpages)

if __name__ == "__main__":
    main()

