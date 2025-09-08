#!/usr/bin/env python3

import argparse
import re
import hashlib
from collections import defaultdict

# Heuristics: function names that indicate allocation sites (generic/top of stack)
ALLOCATOR_FUNC_RE = re.compile(
    r'\b('
    # page allocator family
    r'__alloc_pages(?:_nodemask|_slowpath)?|alloc_pages(?:_current|_mpol)?|alloc_page_interleave|'
    r'__get_free_pages|'
    # page cache / folio family
    r'__page_cache_alloc|page_cache_alloc|pagecache_get_page|filemap_alloc_folio|'
    r'folio_alloc(?:_node|_nocma|_noprof)?|'
    # slab / kmalloc family
    r'kmem_cache_(?:alloc|zalloc)(?:_node)?|'
    r'k(?:m|vz|vm)alloc(?:_node)?|kzalloc(?:_node)?|kmalloc(?:_node)?|kvzalloc|kvmalloc|'
    # vmalloc
    r'__vmalloc|vmalloc|vzalloc|vmap|'
    # DMA / networking
    r'dma_alloc_[a-z_]*|'
    r'__alloc_skb|alloc_skb|__netdev_alloc_skb|napi_alloc_skb|netdev_alloc_skb|'
    r'page_frag_alloc|skb_page_frag_refill'
    r')\b',
    re.IGNORECASE
)

# Module-local function names that look like an allocation by that module.
# Lookarounds so underscores count as boundaries (Python \b treats '_' as word char).
MODULE_ALLOC_LIKE_RE = re.compile(
    r'(?<![A-Za-z0-9])('
    r'alloc|getblk|new(?:_|$)|buf(?:_|$)|reserve|grow|page(?:s)?_get|vm_alloc'
    r')(?![A-Za-z0-9])',
    re.IGNORECASE
)

# Header regexes for quick detection
HDR_WITH_PID_RE = re.compile(
    r"^Page\s+allocated\s+via\s+order\s+\d+,\s*mask\s+0x[0-9a-fA-F]+.*?,\s*pid\s+\d+,\s*tgid\s+\d+\s*\([^)]+\)\s*,\s*ts\s+\d+(?:\s*ns)?\s*$"
)
HDR_ANY_RE = re.compile(
    r"^Page\s+allocated\s+via\s+order\s+\d+,\s*mask\s+0x[0-9a-fA-F]"
)

def quick_detect_dump_kind(path, max_lines=5000):
    """
    Quickly determine if dump is type2 (pid/tgid on header) or type1.
    Reads up to max_lines for speed.
    Returns: 'type2', 'type1', or 'unknown'
    """
    saw_any = False
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for i, raw in enumerate(f, 1):
                line = raw.strip()
                if HDR_WITH_PID_RE.search(line):
                    return 'type2'
                if HDR_ANY_RE.search(line):
                    saw_any = True
                if i >= max_lines:
                    break
    except Exception:
        return 'unknown'
    return 'type1' if saw_any else 'unknown'

def parse_page_owner(filename, debug=False, strict=False):
    process_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    module_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})  # exactly one module per allocation (or none)
    slab_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    process_module_pages = defaultdict(lambda: {'pages': 0, 'allocs': 0})
    calltrace_data = defaultdict(lambda: {'count': 0, 'pages': 0})
    calltrace_index = {}
    skipped_allocations = {'missing_match': 0, 'incomplete_trace': 0, 'invalid_order': 0}

    # New: per-order stats for -t
    order_stats = defaultdict(lambda: {'allocs': 0, 'pages': 0})

    allocations = []
    current_allocation = {}
    current_calltrace = []
    in_trace = False
    total_allocs = 0
    valid_allocation_detected = False
    has_process_metadata = False

    def _is_module_token(tok: str) -> bool:
        t = tok.strip()
        if not t:
            return False
        # Ignore address-like tokens like [<ffffffff...>]
        if '<' in t or '>' in t:
            return False
        if re.fullmatch(r'0x[0-9A-Fa-f]+', t):
            return False
        if re.fullmatch(r'[0-9A-Fa-f]+', t):
            return False
        # Typical module names
        return bool(re.fullmatch(r'[A-Za-z0-9_\-\.]+', t))

    def _parse_frame(line: str):
        """Return (func_name, module_name_or_None)."""
        # Extract module at end in [module]
        mod_match = re.findall(r'\[([^\]]+)\]', line)
        module = None
        if mod_match:
            for tok in reversed(mod_match):
                if _is_module_token(tok):
                    module = tok.strip()
                    break
        # Remove leading [<addr>] and get function before '+'
        s = re.sub(r'^\s*\[[^]]+\]\s*', '', line)
        func = s.split('+', 1)[0].strip()
        if not func:
            func = line.strip()
        return func, module

    def finalize_current():
        nonlocal in_trace, current_allocation, current_calltrace, total_allocs, has_process_metadata
        if not in_trace or 'order' not in current_allocation:
            return

        order = current_allocation.get('order', 0)
        pages = 1 << order
        process_name = current_allocation.get('process', 'Unknown')

        # Process totals use the real size
        process_data[process_name]['allocs'] += 1
        process_data[process_name]['pages'] += pages

        # Track per-order stats for -t
        order_stats[order]['allocs'] += 1
        order_stats[order]['pages'] += pages

        # Parse frames
        frames = [_parse_frame(l) for l in current_calltrace]

        # Find the first generic allocator frame (closest to top)
        alloc_idx = None
        for i, (func, _mod) in enumerate(frames):
            if ALLOCATOR_FUNC_RE.search(func):
                alloc_idx = i
                break

        attributed_module = None

        if alloc_idx is not None:
            if strict:
                # STRICT: attribute only if a module-tagged frame at/under allocator looks alloc-like
                func0, mod0 = frames[alloc_idx]
                if mod0 and MODULE_ALLOC_LIKE_RE.search(func0):
                    attributed_module = mod0
                else:
                    for j in range(alloc_idx + 1, len(frames)):
                        funcj, modj = frames[j]
                        if modj and MODULE_ALLOC_LIKE_RE.search(funcj):
                            attributed_module = modj
                            break
                # If not found, leave unattributed in strict mode
            else:
                # NON-STRICT: nearest module at/under allocator
                _func0, mod0 = frames[alloc_idx]
                if mod0:
                    attributed_module = mod0
                else:
                    for j in range(alloc_idx + 1, len(frames)):
                        _funcj, modj = frames[j]
                        if modj:
                            attributed_module = modj
                            break
        else:
            # No allocator found: only non-strict falls back
            if not strict:
                for _func, m in frames:
                    if m:
                        attributed_module = m
                        break

        # Record slab-ish call sites (independent of module attribution)
        for func, _ in frames:
            if re.search(r'kmalloc|slab|cache|kfree', func, re.IGNORECASE):
                slab_data[func]['allocs'] += 1
                slab_data[func]['pages'] += pages

        # Attribute full allocation to exactly one module (if found)
        if attributed_module:
            module_data[attributed_module]['allocs'] += 1
            module_data[attributed_module]['pages'] += pages
            process_module_pages[(process_name, attributed_module)]['pages'] += pages
            process_module_pages[(process_name, attributed_module)]['allocs'] += 1

        # Group by call trace hash
        trace_str = "\n".join(current_calltrace)
        trace_key = hashlib.sha256(trace_str.encode()).hexdigest()
        current_allocation['trace_key'] = trace_key
        current_allocation['pages'] = pages

        if trace_key not in calltrace_index:
            calltrace_index[trace_key] = current_calltrace.copy()

        calltrace_data[trace_key]['count'] += 1
        calltrace_data[trace_key]['pages'] += pages

        allocations.append({"process": process_name, "trace_key": trace_key, "pages": pages})

        total_allocs += 1
        in_trace = False
        current_allocation = {}
        current_calltrace = []

    with open(filename, 'r', encoding='utf-8', errors='replace') as f:
        for raw_line in f:
            line = raw_line.strip()

            if line.startswith("Page allocated"):
                if in_trace:
                    finalize_current()

                valid_allocation_detected = True
                # type2: includes pid/tgid/comm/ts (ts may end in ' ns')
                m2 = re.search(r"order (\d+), mask .*?, pid (\d+), tgid (\d+) \((.*?)\), ts (\d+)(?:\s*ns)?", line)
                if m2:
                    try:
                        order = int(m2.group(1))
                    except ValueError:
                        skipped_allocations['invalid_order'] += 1
                        in_trace = False
                        continue
                    current_allocation = {
                        'order': order,
                        'pid': int(m2.group(2)),
                        'tgid': int(m2.group(3)),
                        'process': m2.group(4),
                        'ts': int(m2.group(5)),
                    }
                    has_process_metadata = True
                    in_trace = True
                    current_calltrace = []
                else:
                    # type1: without pid/tgid/comm/ts
                    m1 = re.search(r"order (\d+), mask", line)
                    if m1:
                        try:
                            order = int(m1.group(1))
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
                    else:
                        skipped_allocations['missing_match'] += 1
                        in_trace = False

            elif line.startswith("PFN"):
                # PFN/Flags line ignored for these summaries
                pass

            elif in_trace and line:
                current_calltrace.append(line)

            elif in_trace and not line:
                finalize_current()

            elif not line:
                if in_trace:
                    skipped_allocations['incomplete_trace'] += 1
                in_trace = False

        # EOF finalization
        finalize_current()

    return (process_data, module_data, slab_data, calltrace_data, calltrace_index,
            process_module_pages, total_allocs, skipped_allocations,
            valid_allocation_detected, has_process_metadata, allocations, order_stats)

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

def _unit_short(unit):
    return {'K': 'K', 'M': 'M', 'G': 'G'}.get(unit, 'kB')

def show_top(data, label, unit, key='pages', top_n=10):
    unit_short = _unit_short(unit)
    if label == "Modules":
        print(f"{'Module':<25}{'Allocations':>15}{'Memory (' + unit_short + ')':>15}")
        print("=" * 55)
    else:
        print(f"Top {top_n} {label}:")
        print("=" * 50)

    sorted_items = sorted(data.items(), key=lambda x: x[1][key], reverse=True)[:top_n]
    total_pages = 0
    total_allocs = 0
    for name, stats in sorted_items:
        mem, unit_label = convert_pages(stats['pages'], unit)
        total_pages += stats['pages']
        total_allocs += stats['allocs']
        if label == "Modules":
            print(f"{name:<25}{stats['allocs']:>15}{mem:>15.2f}")
        else:
            print(f"{name:<25}{stats['allocs']:>15}{mem:>15.2f} {unit_label}")

    total_mem, total_unit = convert_pages(total_pages, unit)
    if label == "Modules":
        print("=" * 55)
        print(f"{'Total':<25}{total_allocs:>15}{total_mem:>15.2f} {total_unit}")
    else:
        print("-" * 50)
        print(f"{'Total':<25}{total_allocs:>15}{total_mem:>15.2f} {total_unit}")
        print("=" * 50)

def show_calltraces(calltrace_data, calltrace_index, unit, top_n=5, filter_by_process=None, process_to_traces=None, allocations=None):
    print(f"Top {top_n} Call Traces:")
    print("=" * 50)

    if filter_by_process and process_to_traces and allocations:
        allowed_keys = process_to_traces.get(filter_by_process, set())
        filtered_stats = defaultdict(lambda: {'count': 0, 'pages': 0})
        for alloc in allocations:
            if alloc['process'] == filter_by_process and alloc['trace_key'] in allowed_keys:
                filtered_stats[alloc['trace_key']]['count'] += 1
                filtered_stats[alloc['trace_key']]['pages'] += alloc['pages']
    else:
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

def show_totals(order_stats):
    """Print the -t summary (and -v per-order breakdown handled in main)."""
    total_allocs = sum(v['allocs'] for v in order_stats.values())
    total_pages = sum(v['pages'] for v in order_stats.values())
    total_gb = (total_pages * 4) / (1024 * 1024)
    print("Summary:")
    print("====================")
    print(f"Total Allocations: {total_allocs}")
    print(f"Total Memory (GB): {total_gb:.2f}")

def show_totals_verbose(order_stats):
    """Print the verbose per-order breakdown for -t -v."""
    print("Summary:")
    print("====================")
    print(f"{'Order':<13}{'Allocations':>15}{'Memory (G)':>16}")
    print("========================================")
    for order in sorted(order_stats.keys()):
        allocs = order_stats[order]['allocs']
        pages = order_stats[order]['pages']
        gb = (pages * 4) / (1024 * 1024)
        print(f"{order:<13}{allocs:>15}{gb:>14.2f} GB")
    print("====================")
    total_allocs = sum(v['allocs'] for v in order_stats.values())
    total_pages = sum(v['pages'] for v in order_stats.values())
    total_gb = (total_pages * 4) / (1024 * 1024)
    print(f"Total Allocations: {total_allocs}")
    print(f"Total Memory (GB): {total_gb:.2f}")

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
    parser.add_argument("-t", "--total", action="store_true", help="Show only total allocations/memory (with -v, also per-order breakdown)")
    parser.add_argument("--calltrace-process", type=str, help="Show call traces only for this process")
    parser.add_argument("--filter-module", type=str, help="Show top processes using this module")
    parser.add_argument("--strict", action="store_true", help="Attribute only when a module-tagged frame at/under the first allocator looks allocation-like (e.g., vx_alloc, getblk, new_*)")
    parser.add_argument("--detect-lines", type=int, default=5000, help="Max lines to scan for dump kind detection before full parse (default: 5000)")
    args = parser.parse_args()
    unit = args.unit or 'G'

    # Default to total if *no* report option is set
    if not (args.processes or args.modules or args.slabs or args.calltraces or args.modules):
        args.total = True

    if args.calltrace_process and not args.calltraces:
        print("Error: '--calltrace-process' requires '-c' or '--calltraces' to be specified.")
        return

    if args.filter_module and not args.processes:
        print("Error: '--filter-module' requires '-p' or '--processes' to be specified.")
        return

    # Fast pre-scan to detect dump kind and avoid full parse if only -p is requested and file is type1
    only_process_view = args.processes and not (args.modules or args.slabs or args.calltraces or args.total)
    dump_kind = quick_detect_dump_kind(args.file, max_lines=args.detect_lines)

    if only_process_view:
        if dump_kind == 'type1':
            print("Process view (-p): No process metadata found in this dump (Type-1). Skipping full parse.")
            return
        elif dump_kind == 'unknown':
            if args.verbose:
                print(f"Dump kind detection is inconclusive after {args.detect_lines} lines; proceeding to parse.")

    if args.verbose:
        kind_msg = f"Detected dump kind: {dump_kind}" if dump_kind != 'unknown' else "Dump kind: unknown"
        print(f"Analyzing {args.file} with unit {unit}{' (strict mode)' if args.strict else ''}. {kind_msg}")

    (process_data, module_data, slab_data, calltrace_data, calltrace_index,
     process_module_pages, total_allocs, skipped_allocations,
     valid_allocation_detected, has_process_metadata, allocations, order_stats) = parse_page_owner(
        args.file, args.debug, strict=args.strict
    )

    # -t / --total: only totals summary (with -v, per-order breakdown)
    if args.total:
        if args.verbose:
            show_totals_verbose(order_stats)
        else:
            show_totals(order_stats)
        return

    if args.processes:
        if args.filter_module:
            show_processes_for_module(process_module_pages, args.filter_module, unit)
        else:
            if not has_process_metadata:
                print("Process metadata (pid/tgid/comm) not present in this dump; 'Unknown' will be shown as process.")
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

        show_calltraces(
            calltrace_data, calltrace_index, unit,
            filter_by_process=args.calltrace_process,
            process_to_traces=process_to_traces,
            allocations=allocations
        )

    show_skipped(skipped_allocations, args.verbose)

if __name__ == "__main__":
    main()
