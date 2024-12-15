#!/usr/bin/env python3

import argparse
from collections import defaultdict
import re
import logging

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

def parse_page_owner_file(filename):
    """Parses the page_owner file and extracts relevant data."""
    with open(filename, 'r') as f:
        lines = f.readlines()

    # Process data to store allocations and memory usage per process
    process_data = defaultdict(lambda: {'allocations': 0, 'memory_kb': 0})

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

    """Parses page_owner data and calculates slab usage."""
    slab_usage = defaultdict(int)  # {order: total_pages}
    non_slab_usage = defaultdict(int)  # {order: total_pages}
    app_slab_usage = defaultdict(int)  # {app_name: total_pages}
    app_non_slab_usage = defaultdict(int)

    is_slab_allocation = False

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
                logging.warning(f"Failed to match allocation line: {line}")
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

            # Calculate memory usage for the current allocation
            pages = 1 << current_allocation['order']  # Number of pages based on the order
            memory_kb = pages * 4  # Memory in KB (4 KB per page)

            # Aggregate allocations and memory usage per process
            process_name = current_allocation.get('process', 'Unknown')
            process_data[process_name]['allocations'] += 1
            process_data[process_name]['memory_kb'] += memory_kb

            #logging.debug(
                #f"Allocation for process {process_name}: Order {current_allocation['order']}, "
                #f"Pages {pages}, Memory {memory_kb / 1024:.2f} MB"
            #)

            trace_active = True
            is_slab_allocation = False
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
                if is_slab_allocation is False and any(func in line for func in SLAB_FUNCTIONS):
                    is_slab_allocation = True

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
                calltraces[trace_key]['process'] = current_allocation.get('process') or 'Unknown'
                calltraces[trace_key]['timestamp'] = current_allocation.get('timestamp')

                # Update allocations for each unique module in this trace
                for module in modules_in_trace:
                    allocations_by_module[module]['allocations'][current_allocation['order']] += 1

                order = current_allocation['order']
                process_name = current_allocation['process']

                # If it's a slab allocation, add usage
                if is_slab_allocation:

                    # Increment slab usage by order
                    slab_usage[order] += 1 << order
                    logging.debug(f"process name: {process_name}")

                    # Increment application usage
                    if process_name:
                        app_slab_usage[process_name] += 1 << order
                        logging.debug(f"Added to application '{process_name}': {1 << order} pages")
                else:
                    non_slab_usage[order] += 1 << order
                    if process_name:
                        app_non_slab_usage[process_name] += 1 << order

                current_calltrace = []
                modules_in_trace.clear()
                trace_active = False
 
    return process_data, allocations_by_module, allocations_by_order, calltraces, slab_usage, non_slab_usage, app_slab_usage, app_non_slab_usage

def convert_memory(memory_kb, unit):
    """Convert memory from KB to the specified unit."""
    if unit == 'K':
        return memory_kb, 'kB'
    elif unit == 'M':
        return memory_kb / 1024, 'MB'
    elif unit == 'G':
        return memory_kb / (1024 ** 2), 'GB'
    else:
        raise ValueError(f"Invalid unit: {unit}. Use 'K', 'M', or 'G'.")

def show_allocations_by_process(process_data, top_n=10, unit='G', verbose=False):
    print(f"{'Process':<20}{'Allocations':>12}{f'Memory ({unit})':>15}")
    print("=" * 47)

    # Sort processes by memory usage and take the top N
    sorted_processes = sorted(process_data.items(), key=lambda x: x[1]['memory_kb'], reverse=True)

    total_allocations = sum(data['allocations'] for data in process_data.values())
    total_memory_kb = sum(data['memory_kb'] for data in process_data.values())

    # Display only the top N processes
    for process, data in sorted_processes[:top_n]:
        memory, unit_label = convert_memory(data['memory_kb'], unit)
        print(f"{process:<20}{data['allocations']:>12}{memory:>15.2f} {unit_label}")

    # Print totals for all processes
    total_memory, unit_label = convert_memory(total_memory_kb, unit)
    print("=" * 47)
    print(f"{'Total (All Processes)':<20}{total_allocations:>12}{total_memory:>15.2f} {unit_label}")

    if verbose:
        print(f"\nProcessed {len(process_data)} processes. Displayed top {top_n}.")

def show_allocations_by_module_and_order(allocations, unit='G', verbose=False):
    print(f"{'Module':<20}{'Order':>10}{'Allocations':>15}{f'Memory ({unit})':>15}")
    print("=" * 60)

    total_allocations = 0
    total_memory_kb = 0

    # Loop through each module and its data
    for module, data in allocations.items():
        module_total_allocations = 0
        module_total_memory_kb = 0

        # Loop through orders within the module
        for order, count in sorted(data['allocations'].items()):
            memory_kb = count * (4 * (2 ** order))  # Memory in KB
            memory, unit_label = convert_memory(memory_kb, unit)

            # Print per-order details
            print(f"{module:<20}{order:>10}{count:>15}{memory:>15.2f} {unit_label}")

            module_total_allocations += count
            module_total_memory_kb += memory_kb

        # Print module totals if verbose mode is enabled
        if verbose:
            module_memory, unit_label = convert_memory(module_total_memory_kb, unit)
            print(f"{module:<20}{'Total':>10}{module_total_allocations:>15}{module_memory:>15.2f} {unit_label}")
            print("-" * 60)

        total_allocations += module_total_allocations
        total_memory_kb += module_total_memory_kb

    # Print overall totals
    total_memory, unit_label = convert_memory(total_memory_kb, unit)
    print("=" * 60)
    print(f"{'Total':<20}{'':>10}{total_allocations:>15}{total_memory:>15.2f} {unit_label}")

def show_allocations_by_module(allocations, unit='G', verbose=False):
    print(f"{'Module':<20}{'Allocations':>12}{f'Memory ({unit})':>15}")
    print("=" * 47)

    total_allocations = 0
    total_memory_kb = 0

    # Calculate memory usage for each module
    module_data = []
    for module, data in allocations.items():
        module_allocations = sum(data['allocations'].values())
        module_memory_kb = sum(
            count * (4 * (2 ** order)) for order, count in data['allocations'].items()
        )
        memory, unit_label = convert_memory(module_memory_kb, unit)
        module_data.append((module, module_allocations, memory))

    # Sort by memory usage in descending order and pick top 10
    top_modules = sorted(module_data, key=lambda x: x[2], reverse=True)[:10]

    # Display the top 10 modules
    for module, allocations_count, memory in top_modules:
        total_allocations += allocations_count
        total_memory_kb += memory * 1024 * 1024 if unit == 'G' else (memory * 1024 if unit == 'M' else memory)
        print(f"{module:<20}{allocations_count:>12}{memory:>15.2f}")

    # Display the totals
    total_memory, unit_label = convert_memory(total_memory_kb, unit)
    print("=" * 47)
    print(f"{'Total':<20}{total_allocations:>12}{total_memory:>15.2f} {unit_label}")

def show_allocations_by_order(allocations, unit='G', verbose=False):
    total_allocations = 0
    total_memory_kb = 0

    print(f"{'Order':<10}{'Allocations':>15}{f'Memory ({unit})':>15}")
    print("=" * 40)
    for order, allocations_count in sorted(allocations.items()):
        memory_kb = allocations_count * (4 * (2 ** order))  # Memory in KB
        memory, unit_label = convert_memory(memory_kb, unit)

        total_allocations += allocations_count
        total_memory_kb += memory_kb
        print(f"{order:<10}{allocations_count:>15}{memory:>15.2f} {unit_label}")

    total_memory, unit_label = convert_memory(total_memory_kb, unit)
    print("=" * 40)
    print(f"{'Total':<10}{total_allocations:>15}{total_memory:>15.2f} {unit_label}")

def show_summary(allocations_by_order, unit='G', verbose=False):
    """Display a summary of total memory utilization and allocations."""
    total_allocations = sum(allocations_by_order.values())
    total_memory_kb = sum(
        allocations_count * (4 * (2 ** order))
        for order, allocations_count in allocations_by_order.items()
    )
    total_memory, unit_label = convert_memory(total_memory_kb, unit)

    print("Summary:")
    print("=" * 20)
    print(f"Total Allocations: {total_allocations}")
    print(f"Total Memory ({unit_label}): {total_memory:.2f}")

def show_top_call_traces(calltraces, top_n=3, filter_process=None, verbose=False):
    """Show the top N most commonly seen call traces, optionally filtered by process."""
    # Filter call traces by process if a filter is specified
    filtered_traces = {
        trace: data for trace, data in calltraces.items()
        if filter_process is None or data.get('process') == filter_process
    }

    # Sort the filtered call traces by their count (frequency)
    sorted_traces = sorted(filtered_traces.items(), key=lambda x: x[1]['count'], reverse=True)[:top_n]

    print(f"Top {top_n} most commonly seen call traces{f' for process {filter_process}' if filter_process else ''}:\n")

    if not sorted_traces:
        print("No call traces found for the specified criteria.")
        return

    for i, (trace, data) in enumerate(sorted_traces, 1):
        pages = data['pages']
        memory_gb = pages * 4 / (1024 ** 2)  # Convert pages to GB
        print(f"Call Trace #{i} (Seen {data['count']} times, {pages} pages, {memory_gb:.2f} GB):")
        if 'process' in data and data['process']:
            print(f"  Process: {data['process']} (pid={data.get('pid')}, tgid={data.get('tgid')}, timestamp={data.get('timestamp')})")
        print(trace)
        print()

def calculate_total_slab_pages(usage):
    """Calculate total pages used for slabs from a usage dictionary."""
    return sum(pages for pages in usage.values())

def format_usage_in_gb(pages):
    """Convert pages to GB and format the output."""
    gb = pages * 4 / 1024 / 1024  # 1 page = 4 KB; Convert to GB
    return f"{gb:.2f} GB"

def show_slab_usage_by_order(slab_usage, non_slab_usage, unit='G', verbose=False):
    """Print slab and non-slab usage grouped by order."""
    print(f"{'Order':<10}{f'Slabs ({unit})':<20}{f'Non Slabs ({unit})':<20}{f'Total ({unit})':<20}")
    print("-" * 70)

    # Sort orders and print usage
    orders = sorted(set(slab_usage.keys()).union(non_slab_usage.keys()))
    for order in orders:
        slab_pages = slab_usage.get(order, 0)
        non_slab_pages = non_slab_usage.get(order, 0)
        total_pages = slab_pages + non_slab_pages

        slab_memory, slab_unit = convert_memory(slab_pages * 4, unit)
        non_slab_memory, non_slab_unit = convert_memory(non_slab_pages * 4, unit)
        total_memory, total_unit = convert_memory(total_pages * 4, unit)

        print(f"{order:<10}{slab_memory:<20.2f}{non_slab_memory:<20.2f}{total_memory:<20.2f}")

    # Summarize totals
    total_slab_pages = calculate_total_slab_pages(slab_usage)
    total_non_slab_pages = calculate_total_slab_pages(non_slab_usage)
    total_pages = total_slab_pages + total_non_slab_pages

    total_slab_memory, slab_unit = convert_memory(total_slab_pages * 4, unit)
    total_non_slab_memory, non_slab_unit = convert_memory(total_non_slab_pages * 4, unit)
    total_memory, total_unit = convert_memory(total_pages * 4, unit)

    print("-" * 70)
    print(f"{'Total':<10}{total_slab_memory:<20.2f}{total_non_slab_memory:<20.2f}{total_memory:<20.2f}")

    if verbose:
        print(f"\nVerbose: Total slab pages: {total_slab_pages}, Total non-slab pages: {total_non_slab_pages}.")

def show_slab_usage_by_application(app_slab_usage, app_non_slab_usage, top_n=10, unit='G', verbose=False):
    """Print slab and non-slab usage grouped by application."""
    combined_usage = {
        app_name: (slab_pages, app_non_slab_usage.get(app_name, 0))
        for app_name, slab_pages in app_slab_usage.items()
    }

    # Include applications with non-slab usage only
    for app_name, non_slab_pages in app_non_slab_usage.items():
        if app_name not in combined_usage:
            combined_usage[app_name] = (0, non_slab_pages)

    print(f"{'Application':<20}{f'Slabs ({unit})':<20}{f'Non Slabs ({unit})':<20}{f'Total ({unit})':<20}")
    print("-" * 80)

    sorted_usage = sorted(combined_usage.items(), key=lambda x: x[1][0] + x[1][1], reverse=True)[:top_n]

    for app_name, (slab_pages, non_slab_pages) in sorted_usage:
        slab_memory, slab_unit = convert_memory(slab_pages * 4, unit)
        non_slab_memory, non_slab_unit = convert_memory(non_slab_pages * 4, unit)
        total_memory, total_unit = convert_memory((slab_pages + non_slab_pages) * 4, unit)

        print(f"{app_name:<20}{slab_memory:<20.2f}{non_slab_memory:<20.2f}{total_memory:<20.2f}")

    total_slab_pages = calculate_total_slab_pages(app_slab_usage)
    total_non_slab_pages = calculate_total_slab_pages(app_non_slab_usage)
    total_pages = total_slab_pages + total_non_slab_pages

    total_slab_memory, slab_unit = convert_memory(total_slab_pages * 4, unit)
    total_non_slab_memory, non_slab_unit = convert_memory(total_non_slab_pages * 4, unit)
    total_memory, total_unit = convert_memory(total_pages * 4, unit)

    print("-" * 80)
    print(f"{'Total':<20}{total_slab_memory:<20.2f}{total_non_slab_memory:<20.2f}{total_memory:<20.2f}")

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
        "-p", "--processes", nargs="?", type=int, const=10, help="Show memory usage grouped by process name."
    )
    parser.add_argument(
        "-t", "--total", action="store_true", help="Show total memory utilization and total number of allocations."
    )
    parser.add_argument(
        "-c", "--call-traces", nargs="?", type=int, const=3,
        help="Show the top N most commonly seen call traces."
    )
    parser.add_argument(
        "-f", "--filter-process", type=str,
        help="Filter call traces by a specific process name.(Only works with -c"
    )
    parser.add_argument(
        "-s", "--slabs", action="store_true",
        help="Show slab usage. (Only works with -p or -o)"
    )
    parser.add_argument(
        "-u", "--unit", choices=['K', 'M', 'G'], default='G',
        help="Specify the unit for memory display: K (kB), M (MB), G (GB)."
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output for more details."
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug output."
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s: %(message)s",
        force=True
    )

    # Ensure at least one action is specified
    if not any([args.modules, args.orders, args.processes is not None, args.total, args.call_traces is not None]):
        logging.warning("No valid actions specified. Use --help for usage information.")
        parser.print_help()
        return

    process_data, allocations_by_module, allocations_by_order, calltraces, slab_usage, non_slab_usage, app_slab_usage, app_non_slab_usage = parse_page_owner_file(args.file)

    # Execute actions based on options
    if args.processes is not None and args.slabs:
        logging.info("Displaying slab usage grouped by process name.")
        show_slab_usage_by_application(app_slab_usage, app_non_slab_usage, unit=args.unit, verbose=args.verbose)
    elif args.orders and args.slabs:
        logging.info("Displaying slab usage grouped by order.")
        show_slab_usage_by_order(slab_usage, non_slab_usage, unit=args.unit, verbose=args.verbose)
    elif args.processes is not None:
        show_allocations_by_process(process_data, args.processes, unit=args.unit, verbose=args.verbose)

    elif args.call_traces is not None:
        show_top_call_traces(calltraces, args.call_traces, filter_process=args.filter_process, verbose=args.verbose)
    elif args.modules and args.orders:
        show_allocations_by_module_and_order(allocations_by_module, unit=args.unit, verbose=args.verbose)
    elif args.modules:
        show_allocations_by_module(allocations_by_module, unit=args.unit, verbose=args.verbose)
    elif args.orders:
        show_allocations_by_order(allocations_by_order, unit=args.unit, verbose=args.verbose)
    elif args.total:
        show_summary(allocations_by_order, unit=args.unit)

if __name__ == "__main__":
    main()
