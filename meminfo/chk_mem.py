#!/usr/bin/env python3

import sys
import re
import os
import argparse

DEFAULT_MEMINFO = "proc/meminfo"

FIELDS = [
    "MemTotal", "MemFree", "Buffers", "Cached", "SwapCached",
    "Active(anon)", "Inactive(anon)", "AnonPages",
    "Unevictable", "Slab", "KernelStack",
    "PageTables", "Percpu",
    "HugePages_Total", "Hugepagesize", "Hugetlb"
]

def scale_value(kb, unit):
    if unit == "K": return kb
    if unit == "M": return kb / 1024
    if unit == "G": return kb / (1024 * 1024)

def size_str_to_kb(size_str):
    if not size_str:
        return None
    try:
        if size_str.endswith("G"):
            return int(size_str[:-1]) * 1024 * 1024
        elif size_str.endswith("M"):
            return int(size_str[:-1]) * 1024
        elif size_str.endswith("K"):
            return int(size_str[:-1])
    except:
        pass
    return None

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

def parse_cmdline_hugepages():
    hugepages = None
    hugepagesz = None
    default_hugepagesz = None

    cmdline_path = "proc/cmdline"
    if not os.path.isfile(cmdline_path):
        return None, None

    with open(cmdline_path, "r") as f:
        cmdline = f.read()

    match = re.search(r"hugepages=(\d+)", cmdline)
    if match:
        hugepages = int(match.group(1))

    match = re.search(r"default_hugepagesz=(\S+)", cmdline)
    if match:
        default_hugepagesz = match.group(1)

    match = re.search(r"hugepagesz=(\S+)", cmdline)
    if match:
        hugepagesz = match.group(1)

    # Prefer explicit size
    size = hugepagesz or default_hugepagesz
    return hugepages, size

def compute_hugepages(meminfo, debug=False):
    note = ""
    # Priority 1: Use Hugetlb directly from meminfo (RHEL8+)
    if "Hugetlb" in meminfo:
        meminfo["HugePages"] = meminfo["Hugetlb"]
        if debug:
            print(f"DEBUG: Using 'Hugetlb' from meminfo: {meminfo['HugePages']} KiB")
        return

    # Priority 2: Use kernel cmdline hints
    hugepages, size_str = parse_cmdline_hugepages()
    size_kb = size_str_to_kb(size_str)

    if debug:
        if size_kb and size_kb > 2048:
            print(f"DEBUG: from cmdline: hugepagesz:: \033[91m{size_kb}\033[0m")
        else:
            print(f"DEBUG: from cmdline: hugepagesz:: {size_kb}")
    if hugepages is not None and size_kb is not None:
        meminfo["HugePages"] = hugepages * size_kb
        if debug:
            print(f"DEBUG: Calculated HugePages from cmdline: {hugepages} × {size_kb} KiB = {meminfo['HugePages']} KiB")
        return

    # Priority 3: Fallback to sysfs (only if size known)
    if size_kb:
        sys_path = os.path.join(root, f"sys/kernel/mm/hugepages/hugepages-{size_kb}kB/nr_hugepages")
        if os.path.isfile(sys_path):
            try:
                with open(sys_path) as f:
                    count = int(f.read().strip())
                    meminfo["HugePages"] = count * size_kb
                    if debug:
                        print(f"DEBUG: Fallback sysfs HugePages: {count} × {size_kb} KiB = {meminfo['HugePages']} KiB")
                    return
            except Exception as e:
                print(f"Error reading {sys_path}: {e}")

    # If all fail
    print("Warning: HugePages usage could not be determined.")
    meminfo["HugePages"] = 0

def calculate_unaccounted(meminfo, show_anonpages):
    total = meminfo.get("MemTotal")
    if total is None:
        print("Error: MemTotal not found.")
        sys.exit(1)

    accounted = []
    for field in FIELDS:
        if field in ("MemTotal", "Unevictable", "Hugetlb"):
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

def print_report(meminfo, total, accounted_fields, accounted_sum, unaccounted, verbose, show_anonpages, unit):
    unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}.get(unit, "MiB")

    if verbose:
        # Header line for formula
        formula_fields = " - ".join(accounted_fields)
        values_line = " - ".join(f"{scale_value(meminfo[field], unit):.2f}" for field in accounted_fields)
        print("Formula used for calculation:")
        print(f"  Unaccounted Memory = MemTotal - {formula_fields}")
        print(f"  {scale_value(unaccounted, unit):.2f} = {scale_value(total, unit):.2f} - {values_line}\n")

        # Final summary
        print(f"{'Unaccounted:':<20} {scale_value(unaccounted, unit):>20.2f} ({unit_label})\n")

    header = f"{'Field':<20} {'Size (' + unit_label + ')':>20}"
    print(header)
    print("=" * len(header))
    print(f"{'MemTotal:':<20} {scale_value(total, unit):>20.2f}")
    for key in accounted_fields:
        print(f"{key:<20} {scale_value(meminfo[key], unit):>20.2f}")
    print("=" * len(header))
    print(f"{'Unaccounted:':<20} {scale_value(unaccounted, unit):>20.2f}\n")

def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate unaccounted memory from proc/meminfo or a custom file."
    )
    parser.add_argument(
        "filename", nargs="?", default=DEFAULT_MEMINFO,
        help="Path to the meminfo file (default: proc/meminfo)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "-d", "--debug", action="store_true",
        help="Enable debug output (paths, hugepages, fallback logic)"
    )

    parser.add_argument("-u", "--unaccounted", dest="unaccounted", action="store_true", default=True,
        help="Include unaccounted memory calculation (default: enabled)")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-K", action="store_const", const="K", dest="unit", help="Display memory in KiB")
    group.add_argument("-M", action="store_const", const="M", dest="unit", help="Display memory in MiB (default)")
    group.add_argument("-G", action="store_const", const="G", dest="unit", help="Display memory in GiB")
    parser.set_defaults(unit="M")

    return parser.parse_args()

def main():
    args = parse_args()

    meminfo = parse_meminfo(args.filename, args.verbose)
    show_anonpages = compute_anonpages(meminfo)
    compute_hugepages(meminfo, debug=args.debug)

    if args.unaccounted:
        total, accounted_sum, unaccounted, accounted_fields = calculate_unaccounted(meminfo, show_anonpages)
        print_report(meminfo, total, accounted_fields, accounted_sum, unaccounted, args.verbose, show_anonpages, args.unit)

if __name__ == "__main__":
    main()

