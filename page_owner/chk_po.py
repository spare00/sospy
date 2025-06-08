#!/usr/bin/env python3

import argparse
import re
import hashlib
from collections import defaultdict

def parse_page_owner(filename, debug=False):
    process_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    module_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    slab_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    process_module_pages = defaultdict(lambda: {'pages': 0, 'allocs': 0})
    calltrace_data = defaultdict(lambda: {'count': 0, 'pages': 0})
    calltrace_index = {}
    skipped_allocations = {'missing_match': 0, 'incomplete_trace': 0, 'invalid_order': 0}

    allocations = []
    current_allocation = {}
    current_calltrace = []
    in_trace = False
    total_allocs = 0
    valid_allocation_detected = False

    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith("Page allocated"):
                valid_allocation_detected = True
                match = re.search(r"order (\d+), mask .*?, pid (\d+), tgid (\d+) \((.*?)\), ts (\d+)", line)
                if match:
                    order = int(match.group(1))
                    current_allocation = {
                        'order': order,
                        'pid': int(match.group(2)),
                        'tgid': int(match.group(3)),
                        'process': match.group(4),
                        'ts': int(match.group(5)),
                    }
                    in_trace = True
                    current_calltrace = []
                    total_allocs += 1
                else:
                    match_type1 = re.search(r"order (\d+), mask", line)
                    if match_type1:
                        try:
                            order = int(match_type1.group(1))
                        except ValueError:
                            skipped_allocations['invalid_order'] += 1
                            in_trace = False
                            continue
                        current_allocation = {
                            'order': order,
                            'pid': -1,
                            'tgid': -1,
                            'process': 'Unknown',
                            'ts': -1,
                        }
                        in_trace = True
                        current_calltrace = []
                        total_allocs += 1
                    else:
                        skipped_allocations['missing_match'] += 1
                        in_trace = False
            elif line.startswith("PFN"):
                pass
            elif in_trace and line:
                current_calltrace.append(line)
            elif in_trace and not line:
                if 'order' not in current_allocation:
                    skipped_allocations['invalid_order'] += 1
                    in_trace = False
                    continue

                pages = 1 << current_allocation.get('order', 0)
                process_name = current_allocation.get('process', 'Unknown')
                process_data[process_name]['allocs'] += 1
                process_data[process_name]['pages'] += pages

                unique_modules = set()
                for trace_line in current_calltrace:
                    mod_match = re.search(r'\[([^\]]+)\]', trace_line)
                    if mod_match:
                        module = mod_match.group(1)
                        unique_modules.add(module)

                    if re.search(r'kmalloc|slab|cache|kfree', trace_line, re.IGNORECASE):
                        func = trace_line.strip().split('+')[0]
                        slab_data[func]['allocs'] += 1
                        slab_data[func]['pages'] += pages

                for module in unique_modules:
                    module_data[module]['allocs'] += 1
                    module_data[module]['pages'] += pages
                    process_module_pages[(process_name, module)]['pages'] += pages
                    process_module_pages[(process_name, module)]['allocs'] += 1

                trace_str = "\n".join(current_calltrace)
                trace_key = hashlib.sha256(trace_str.encode()).hexdigest()
                current_allocation['trace_key'] = trace_key
                current_allocation['pages'] = pages

                if trace_key not in calltrace_index:
                    calltrace_index[trace_key] = current_calltrace.copy()

                calltrace_data[trace_key]['count'] += 1
                calltrace_data[trace_key]['pages'] += pages

                allocations.append({"process": process_name, "trace_key": trace_key, "pages": pages})

                in_trace = False
            elif not line:
                if in_trace:
                    skipped_allocations['incomplete_trace'] += 1
                in_trace = False

    return process_data, module_data, slab_data, calltrace_data, calltrace_index, process_module_pages, total_allocs, skipped_allocations, valid_allocation_detected, allocations

def convert_pages(pages, unit):
    kb = pages * 4
    if unit == 'K':
        return kb, 'kB'
    elif unit == 'M':
        return kb / 1024, 'MB'
    elif unit == 'G':
        return kb / 1024 / 1024, 'GB'
    else:
        return kb, 'kB'

def show_top(data, label, unit, key='pages', top_n=10):
    print(f"Top {top_n} {label}:")
    print("=" * 50)
    sorted_items = sorted(data.items(), key=lambda x: x[1][key], reverse=True)[:top_n]
    total_pages = 0
    total_allocs = 0
    for name, stats in sorted_items:
        mem, unit_label = convert_pages(stats['pages'], unit)
        total_pages += stats['pages']
        total_allocs += stats['allocs']
        print(f"{name:<25}{stats['allocs']:>15}{mem:>15.2f} {unit_label}")
    total_mem, total_unit = convert_pages(total_pages, unit)
    print("-" * 50)
    print(f"{'Total':<25}{total_allocs:>15}{total_mem:>15.2f} {total_unit}")
    print("=" * 50)

def show_calltraces(calltrace_data, calltrace_index, unit, top_n=5, filter_by_process=None, process_to_traces=None, allocations=None):
    print(f"Top {top_n} Call Traces:")
    print("=" * 50)

    if filter_by_process and process_to_traces and allocations:
        allowed_keys = process_to_traces.get(filter_by_process, set())

        # Recalculate count/pages for just this process
        filtered_stats = defaultdict(lambda: {'count': 0, 'pages': 0})
        for alloc in allocations:
            if alloc['process'] == filter_by_process and alloc['trace_key'] in allowed_keys:
                filtered_stats[alloc['trace_key']]['count'] += 1
                filtered_stats[alloc['trace_key']]['pages'] += alloc['pages']
    else:
        # Use the full dataset
        filtered_stats = calltrace_data

    if not filtered_stats:
        print(f"No call traces found for process '{filter_by_process}'")
        return

    sorted_traces = sorted(filtered_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:top_n]
    for i, (key, data) in enumerate(sorted_traces, 1):
        mem, unit_label = convert_pages(data['pages'], unit)
        print(f"#{i}: Seen {data['count']} times, {mem:.2f} {unit_label}")
        print("\n".join(calltrace_index[key]))
        print("-" * 50)

def show_processes_for_module(process_module_pages, module_name, unit, top_n=10):
    aggregated = defaultdict(lambda: {'pages': 0, 'allocs': 0})
    for (proc, mod), stats in process_module_pages.items():
        if mod == module_name:
            aggregated[proc]['pages'] += stats['pages']
            aggregated[proc]['allocs'] += stats['allocs']
    if not aggregated:
        print(f"No allocations found for module '{module_name}'.")
        return

    print(f"Top {top_n} Processes using module '{module_name}':")
    print("=" * 50)
    sorted_items = sorted(aggregated.items(), key=lambda x: x[1]['pages'], reverse=True)[:top_n]
    total_pages = 0
    total_allocs = 0
    for proc, stats in sorted_items:
        mem, unit_label = convert_pages(stats['pages'], unit)
        total_pages += stats['pages']
        total_allocs += stats['allocs']
        print(f"{proc:<25}{stats['allocs']:>15}{mem:>15.2f} {unit_label}")
    total_mem, unit_label = convert_pages(total_pages, unit)
    print("-" * 50)
    print(f"{'Total':<25}{total_allocs:>15}{total_mem:>15.2f} {unit_label}")
    print("=" * 50)

def show_skipped(skipped_allocations, verbose=False):
    if not verbose:
        return
    skipped_total = sum(skipped_allocations.values())
    print(f"Total skipped: {skipped_total}")
    for reason, count in skipped_allocations.items():
        print(f" - {reason.replace('_', ' ').capitalize()}: {count}")

def main():
    parser = argparse.ArgumentParser(description="Analyze large page_owner file.")
    parser.add_argument("file", help="Path to the page_owner file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug output")
    parser.add_argument("-M", dest="unit", action="store_const", const='M', help="Show in MB")
    parser.add_argument("-K", dest="unit", action="store_const", const='K', help="Show in KB")
    parser.add_argument("-G", dest="unit", action="store_const", const='G', help="Show in GB")
    parser.add_argument("-p", "--processes", action="store_true", help="Show top memory-using processes")
    parser.add_argument("-m", "--modules", action="store_true", help="Show top memory-using modules")
    parser.add_argument("-s", "--slabs", action="store_true", help="Show top memory-using slab allocators")
    parser.add_argument("-c", "--calltraces", action="store_true", help="Show top 5 call trace patterns")
    parser.add_argument("--calltrace-process", type=str, help="Show call traces only for this process")
    parser.add_argument("--filter-module", type=str, help="Show top processes using this module")
    args = parser.parse_args()
    unit = args.unit or 'G'

    if args.calltrace_process and not args.calltraces:
        print("Error: '--calltrace-process' requires '-c' or '--calltraces' to be specified.")
        return

    if args.filter_module and not args.processes:
        print("Error: '--filter_module' requires '-p' or '--processes' to be specified.")
        return

    if args.verbose:
        print(f"Analyzing {args.file} with unit {unit}")

    process_data, module_data, slab_data, calltrace_data, calltrace_index, process_module_pages, total_allocs, skipped_allocations, valid_allocation_detected, allocations = parse_page_owner(args.file, args.debug)

    if args.processes:
        if args.filter_module:
            show_processes_for_module(process_module_pages, args.filter_module, unit)
        else:
            if not valid_allocation_detected:
                print("Process-level allocation data not found in this dump format.")
                return
            show_top(process_data, "Processes", unit)
    if args.modules:
        show_top(module_data, "Modules", unit)
    if args.slabs:
        show_top(slab_data, "Slab Functions", unit)
    if args.calltraces:
        process_to_traces = defaultdict(set)
        for alloc in allocations:
            if not args.calltrace_process or alloc['process'] == args.calltrace_process:
                process_to_traces[alloc['process']].add(alloc['trace_key'])

        show_calltraces(calltrace_data, calltrace_index, unit, filter_by_process=args.calltrace_process, process_to_traces=process_to_traces, allocations=allocations)

    show_skipped(skipped_allocations, args.verbose)

if __name__ == "__main__":
    main()

