#!/usr/bin/env python3

import re
import sys
from collections import defaultdict
import argparse
import logging

# Setup logging for warnings and debugging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

# Expanded list of functions related to slab allocation
SLAB_FUNCTIONS = [
    "kmem_cache_alloc",
    "kmem_cache_alloc_node",
    "allocate_slab",
    "___slab_alloc",
    "kmem_cache_alloc_lru",
    "slab_alloc",
    "__slab_alloc",
    "__kmalloc"
]

def debug_print(debug, *args):
    """Print debug messages only if debugging is enabled."""
    if debug:
        print("DEBUG:", *args)

def parse_page_owner(file_path, per_application=False, debug=False):
    """Parses page_owner data and calculates slab usage."""
    slab_usage = defaultdict(int)  # {order: total_pages}
    non_slab_usage = defaultdict(int)  # {order: total_pages}
    app_slab_usage = defaultdict(int)  # {app_name: total_pages}
    app_non_slab_usage = defaultdict(int)

    current_allocation = None  # To hold the details of the current allocation
    is_slab_allocation = False
    in_stack_trace = False

    with open(file_path, "r") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()

        # Match "Page allocated" lines (type 1 or type 2)
        if line.startswith("Page allocated"):
            # Reset allocation details for each new allocation
            current_allocation = {
                'order': None,
                'mask': None,
                'pid': None,
                'tgid': None,
                'process': None,
                'timestamp': None,
            }

            # Match type 1 and type 2 formats
            match = re.search(
                r"order (\d+), mask (0x[0-9a-fA-F]+(?:\([^\)]*\))?)"
                r"(?:, pid (\d+), tgid (\d+) \((.+?)\), ts (\d+) ns)?",
                line
            )

            if not match:
                logging.warning(f"Failed to match allocation line: {line}")
                continue

            # Extract allocation details
            current_allocation['order'] = int(match.group(1))
            current_allocation['mask'] = match.group(2)
            current_allocation['pid'] = int(match.group(3)) if match.group(3) else None
            current_allocation['tgid'] = int(match.group(4)) if match.group(4) else None
            current_allocation['process'] = match.group(5) if match.group(5) else None
            current_allocation['timestamp'] = int(match.group(6)) if match.group(6) else None

            debug_print(debug, "Parsed allocation:", current_allocation)
            in_stack_trace = True
            is_slab_allocation = False
            continue

        if in_stack_trace:
            if any(func in line for func in SLAB_FUNCTIONS):
                is_slab_allocation = True
                debug_print(debug, "Matched slab-related function in stack trace:", line)

            if not line or re.match(r"PFN \d+", line):  # Empty line or new allocation block
                in_stack_trace = False
                debug_print(debug, "Stack trace ended.")

                if current_allocation:
                    order = current_allocation['order']
                    process_name = current_allocation['process']

                    # If it's a slab allocation, add usage
                    if is_slab_allocation:
                        # Increment slab usage by order
                        slab_usage[order] += 1 << order

                        # Increment application usage if `per_application` is enabled
                        if per_application and process_name:
                            app_slab_usage[process_name] += 1 << order
                            debug_print(debug, f"Added to application '{process_name}': {1 << order} pages")

                    else:
                        # Increment non-slab usage by order
                        non_slab_usage[order] += 1 << order

                        # Increment application usage if `per_application` is enabled
                        if per_application and process_name:
                            app_non_slab_usage[process_name] += 1 << order
                            debug_print(debug, f"Added to application '{process_name}': {1 << order} pages")

                continue

    if per_application:
        return app_slab_usage, app_non_slab_usage
    return slab_usage, non_slab_usage

def calculate_total_slab_pages(usage):
    """Calculate total pages used for slabs from a usage dictionary."""
    return sum(pages for pages in usage.values())

def format_usage_in_gb(pages):
    """Convert pages to GB and format the output."""
    gb = pages * 4 / 1024 / 1024  # 1 page = 4 KB; Convert to GB
    return f"{gb:.2f} GB"

def print_usage_by_order(slab_usage, non_slab_usage):
    """Print slab and non-slab usage grouped by order."""
    print(f"{'Order':<10}{'Slabs (GB)':<20}{'Non Slabs (GB)':<20}{'Total (GB)':<20}")
    print("-" * 70)

    # Sort orders and print usage
    orders = sorted(set(slab_usage.keys()).union(non_slab_usage.keys()))
    for order in orders:
        slab_pages = slab_usage.get(order, 0)
        non_slab_pages = non_slab_usage.get(order, 0)
        total_pages = slab_pages + non_slab_pages

        slab_gb = format_usage_in_gb(slab_pages)
        non_slab_gb = format_usage_in_gb(non_slab_pages)
        total_gb = format_usage_in_gb(total_pages)
        print(f"{order:<10}{slab_gb:<20}{non_slab_gb:<20}{total_gb:<20}")

    total_slab_pages = calculate_total_slab_pages(slab_usage)
    total_non_slab_pages = calculate_total_slab_pages(non_slab_usage)
    total_pages = total_slab_pages + total_non_slab_pages
    print("-" * 70)
    print(f"{'Total':<10}{format_usage_in_gb(total_slab_pages):<20}{format_usage_in_gb(total_non_slab_pages):<20}{format_usage_in_gb(total_pages):<20}")

def print_slab_usage_by_application(app_slab_usage, app_non_slab_usage, top_n=10):
    """Print slab and non-slab usage grouped by application, sorted by combined usage."""
    combined_usage = {
        app_name: (slab_pages, app_non_slab_usage.get(app_name, 0))
        for app_name, slab_pages in app_slab_usage.items()
    }

    # Ensure all non-slab applications are included
    for app_name, non_slab_pages in app_non_slab_usage.items():
        if app_name not in combined_usage:
            combined_usage[app_name] = (0, non_slab_pages)

    print(f"{'Application':<20}{'Slabs (GB)':<20}{'Non Slabs (GB)':<20}{'Total (GB)':<20}")
    print("-" * 80)

    # Sort by total usage (slab + non-slab) and get top N
    sorted_usage = sorted(combined_usage.items(),
                          key=lambda x: x[1][0] + x[1][1],
                          reverse=True)[:top_n]

    for app_name, (slab_pages, non_slab_pages) in sorted_usage:
        slab_gb = format_usage_in_gb(slab_pages)
        non_slab_gb = format_usage_in_gb(non_slab_pages)
        total_gb = format_usage_in_gb(slab_pages + non_slab_pages)
        print(f"{app_name:<20}{slab_gb:<20}{non_slab_gb:<20}{total_gb:<20}")

    # Calculate and display totals
    total_slab_pages = calculate_total_slab_pages(app_slab_usage)
    total_non_slab_pages = calculate_total_slab_pages(app_non_slab_usage)
    total_pages = total_slab_pages + total_non_slab_pages
    print("-" * 80)
    print(f"{'Total':<20}{format_usage_in_gb(total_slab_pages):<20}{format_usage_in_gb(total_non_slab_pages):<20}{format_usage_in_gb(total_pages):<20}")

def main():
    # Setup argument parser
    parser = argparse.ArgumentParser(description="Analyze slab usage from page_owner data.")
    parser.add_argument("file", help="Path to the page_owner log file")
    parser.add_argument("-p", "--per-application", nargs="?", const=10, type=int,
                        help="Show slab usage per application, sorted by usage (default: top 10)")
    parser.add_argument("-o", "--order", action="store_true",
                        help="Show slab usage grouped by order")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Enable debugging output")

    args = parser.parse_args()

    try:
        if args.order:
            slab_usage, non_slab_usage = parse_page_owner(args.file, debug=args.debug)
            print_usage_by_order(slab_usage, non_slab_usage)
        elif args.per_application is not None:
            # Parse page_owner data for per-application usage
            app_slab_usage, app_non_slab_usage = parse_page_owner(args.file, per_application=True, debug=args.debug)
            print_slab_usage_by_application(app_slab_usage, app_non_slab_usage, top_n=args.per_application)
        else:
            app_slab_usage, app_non_slab_usage = parse_page_owner(args.file, per_application=True, debug=args.debug)
            print_slab_usage_by_application(app_slab_usage, app_non_slab_usage, top_n=10)

    except FileNotFoundError:
        print(f"Error: File '{args.file}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
