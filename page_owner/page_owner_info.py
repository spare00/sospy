#!/usr/bin/env python3

import argparse
from collections import defaultdict
import re


def parse_page_owner_file(filename):
    """Parses the page_owner file and extracts relevant data."""
    with open(filename, 'r') as f:
        lines = f.readlines()

    allocations_by_module = defaultdict(lambda: {'allocations': defaultdict(int), 'pages': 0})
    allocations_by_order = defaultdict(int)
    calltraces = defaultdict(
        lambda: {
            'count': 0,
            'pages': 0,
            'calltrace': [],
            'pid': None,
            'tgid': None,
            'process': None,
            'timestamp': None,
        }
    )

    current_allocation = {}
    current_calltrace = []
    modules_in_trace = set()
    trace_active = False

    for line in lines:
        line = line.strip()

        if line.startswith("Page allocated"):
            # Reset current_allocation for each new allocation
            current_allocation = {
                'order': None,
                'mask': None,
                'pid': None,
                'tgid': None,
                'process': None,
                'timestamp': None,
            }
            match = re.search(
                r"order (\d+), mask (0x[0-9a-fA-F]+(?:\([^\)]*\))?)(?:, pid (\d+), tgid (\d+) \((.+?)\), ts (\d+) ns)?",
                line
            )
            if not match:
                print(f"Warning: Failed to match allocation line: {line}")
                continue

            # Update allocation information with matched data
            current_allocation.update({
                'order': int(match.group(1)),
                'mask': match.group(2),
                'pid': int(match.group(3)) if match.group(3) else None,
                'tgid': int(match.group(4)) if match.group(4) else None,
                'process': match.group(5) if match.group(5) else None,
                'timestamp': int(match.group(6)) if match.group(6) else None,
            })
            trace_active = True
            modules_in_trace.clear()  # Reset modules for the new trace

        elif line.startswith("PFN"):
            # Extract node and zone information from the flags (if present)
            flags_match = re.search(r"Flags .*?node=(\d+).*?zone=(\d+)", line)
            if flags_match:
                current_allocation['node'] = int(flags_match.group(1))
                current_allocation['zone'] = int(flags_match.group(2))
            if 'order' in current_allocation:
                order = current_allocation['order']
                allocations_by_order[order] += 1

        elif trace_active:
            # Identify alloc-related functions with module names
            alloc_match = re.search(r"(\S*alloc\S*)\s+\[([a-zA-Z0-9_]+)\]", line)
            if alloc_match:
                module = alloc_match.group(2)
                modules_in_trace.add(module)

            if line:
                current_calltrace.append(line)
            else:
                # End of the call trace
                trace_key = "\n".join(current_calltrace)
                pages = 1 << current_allocation.get('order', 0)

                # Update calltrace data
                calltraces[trace_key]['count'] += 1
                calltraces[trace_key]['pages'] += pages
                calltraces[trace_key]['calltrace'] = current_calltrace
                calltraces[trace_key]['pid'] = current_allocation.get('pid')
                calltraces[trace_key]['tgid'] = current_allocation.get('tgid')
                calltraces[trace_key]['process'] = current_allocation.get('process')
                calltraces[trace_key]['timestamp'] = current_allocation.get('timestamp')

                # Update allocations for each unique module in this trace
                for module in modules_in_trace:
                    allocations_by_module[module]['allocations'][current_allocation['order']] += 1

                current_calltrace = []
                modules_in_trace.clear()
                trace_active = False

    return allocations_by_module, allocations_by_order, calltraces

def show_allocations_by_module(allocations):
    print(f"{'Module':<20}{'Allocations':>12}{'Memory (GB)':>15}")
    print("=" * 47)

    total_allocations = 0
    total_memory_gb = 0

    # Calculate memory usage for each module
    module_data = []
    for module, data in allocations.items():
        module_allocations = sum(data['allocations'].values())
        module_memory_kb = sum(
            count * (4 * (2 ** order)) for order, count in data['allocations'].items()
        )
        module_memory_gb = module_memory_kb / (1024 ** 2)
        module_data.append((module, module_allocations, module_memory_gb))

    # Sort by memory usage in descending order and pick top 10
    top_modules = sorted(module_data, key=lambda x: x[2], reverse=True)[:10]

    # Display the top 10 modules
    for module, allocations_count, memory_gb in top_modules:
        total_allocations += allocations_count
        total_memory_gb += memory_gb
        print(f"{module:<20}{allocations_count:>12}{memory_gb:>15.2f}")

    # Display the totals
    print("=" * 47)
    print(f"{'Total':<20}{total_allocations:>12}{total_memory_gb:>15.2f}")

def show_allocations_by_order(allocations):
    total_allocations = 0
    total_memory_gb = 0

    print(f"{'Order':<10}{'Allocations':>15}{'Memory (GB)':>15}")
    print("=" * 40)
    for order, allocations_count in sorted(allocations.items()):
        memory_kb = allocations_count * (4 * (2 ** order))  # Memory in KB
        memory_gb = memory_kb / (1024 ** 2)  # Convert KB to GB
        total_allocations += allocations_count
        total_memory_gb += memory_gb
        print(f"{order:<10}{allocations_count:>15}{memory_gb:>15.2f}")

    print("=" * 40)
    print(f"{'Total':<10}{total_allocations:>15}{total_memory_gb:>15.2f}")

def show_summary(allocations_by_order):
    """Display a summary of total memory utilization and allocations."""
    total_allocations = sum(allocations_by_order.values())
    total_memory_kb = sum(
        allocations_count * (4 * (2 ** order))
        for order, allocations_count in allocations_by_order.items()
    )
    total_memory_gb = total_memory_kb / (1024 ** 2)  # Convert KB to GB

    print("Summary:")
    print("=" * 20)
    print(f"Total Allocations: {total_allocations}")
    print(f"Total Memory (GB): {total_memory_gb:.2f}")

def show_top_call_traces(calltraces, top_n=3):

    """Show the top N most commonly seen call traces."""
    sorted_traces = sorted(calltraces.items(), key=lambda x: x[1]['count'], reverse=True)[:top_n]
    print(f"Top {top_n} most commonly seen call traces:\n")
    for i, (trace, data) in enumerate(sorted_traces, 1):
        pages = data['pages']
        memory_gb = pages * 4 / (1024 ** 2)  # Convert pages to GB
        print(f"Call Trace #{i} (Seen {data['count']} times, {pages} pages, {memory_gb:.2f} GB):")
        if 'process' in data and data['process']:
            print(f"  Process: {data['process']} (pid={data.get('pid')}, tgid={data.get('tgid')}, timestamp={data.get('timestamp')})")
        print(trace)
        print()

def main():
    parser = argparse.ArgumentParser(
        description="Analyze page_owner output.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("file", help="The input file containing page_owner data.")
    parser.add_argument(
        "-m", "--modules", action="store_true", help="Show number of allocations and memory utilization per module."
    )
    parser.add_argument(
        "-o", "--orders", action="store_true", help="Show number of allocations and memory utilization per order."
    )
    parser.add_argument(
        "-t", "--total", action="store_true", help="Show total memory utilization and total number of allocations."
    )
    parser.add_argument(
        "-c", "--call-traces", nargs="?", type=int, const=3,
        help="Show the top N most commonly seen call traces (default: 3)."
    )

    args = parser.parse_args()

    if not any([args.modules, args.orders, args.total, args.call_traces is not None]):
        parser.print_help()
        return

    allocations_by_module, allocations_by_order, calltraces = parse_page_owner_file(args.file)

    if args.modules:
        show_allocations_by_module(allocations_by_module)
    elif args.orders:
        show_allocations_by_order(allocations_by_order)
    elif args.total:
        show_summary(allocations_by_order)
    elif args.call_traces is not None:
        show_top_call_traces(calltraces, args.call_traces)

if __name__ == "__main__":
    main()
