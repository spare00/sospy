#!/usr/bin/env python3

import argparse
import re
import hashlib
import os
import stat
import sys
import time
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

# Subset: slab allocator functions (used to classify slab vs non-slab)
SLAB_ALLOCATOR_FUNC_RE = re.compile(
    r'\b('
    r'kmem_cache_(?:alloc|zalloc)(?:_node)?|'
    r'k(?:m|vz|vm)alloc(?:_node)?|kzalloc(?:_node)?|kmalloc(?:_node)?|'
    r'kmalloc_array|kcalloc|'
    r'__kmalloc(?:_node|_track_caller)?|'
    r'(?:__)?slab_alloc|___slab_alloc|allocate_slab'
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
    r"^Page\s+allocated\s+via\s+order\s+\d+,\s*mask\s+0x[0-9a-fA-F]+.*?,\s*pid\s+\d+,\s*tgid\s+\d+\s*\([^)]+\)\s*,\s*ts\s+\d+(?:\s*ns)?.*"
)
HDR_ANY_RE = re.compile(
    r"^Page\s+allocated\s+via\s+order\s+\d+,\s*mask\s+0x[0-9a-fA-F]"
)

ALLOC_HEADER_ORDER_RE = re.compile(
    r"^Page\s+allocated\s+via\s+order\s+(\d+),\s*mask\s+0x[0-9a-fA-F]+"
)

# -------------------------
# Progress helper (stderr)
# -------------------------

class Progress:
    def __init__(self, label: str, total_bytes=None, interval: float = 0.5, stream=None):
        self.label = label
        self.total = total_bytes
        self.interval = interval
        self.stream = stream or sys.stderr
        self.start = time.time()
        self.last_t = 0.0
        self.last_bytes = 0

    def update(self, bytes_read: int):
        now = time.time()
        if now - self.last_t < self.interval:
            return
        self._print(bytes_read, now)
        self.last_t = now
        self.last_bytes = bytes_read

    def done(self, bytes_read: int):
        self._print(bytes_read, time.time(), final=True)

    def _print(self, bytes_read: int, now: float, final: bool = False):
        elapsed = max(now - self.start, 1e-9)
        rate = bytes_read / (1024 * 1024) / elapsed  # MB/s
        if self.total and self.total > 0:
            pct = min(100.0, (bytes_read / self.total) * 100.0)
            msg = f"{self.label}: {pct:6.2f}%  {bytes_read/1_048_576:.1f}/{self.total/1_048_576:.1f} MB  {rate:.1f} MB/s"
        else:
            msg = f"{self.label}: {bytes_read/1_048_576:.1f} MB read  {rate:.1f} MB/s"
        endc = "\n" if final else "\r"
        print(msg, end=endc, file=self.stream, flush=True)

def _regular_file_size(path):
    try:
        st = os.stat(path)
        if stat.S_ISREG(st.st_mode):
            return st.st_size
    except Exception:
        pass
    return None

# Progress-safe position getter for text files iterated with "for line in f"
def _file_progress_pos(f):
    # Prefer raw buffered position if available (works during iteration)
    try:
        return f.buffer.tell()
    except Exception:
        pass
    # Fallback to text tell() if allowed
    try:
        return f.tell()
    except Exception:
        return None

def _pfn_flags_has_slab(line):
    """Return True when PFN Flags list includes the slab page flag."""
    m = re.search(r'Flags\s+0x[0-9A-Fa-f]+\(([^)]+)\)', line)
    if not m:
        return False
    return 'slab' in (tok.strip() for tok in m.group(1).split('|'))

# -------------------------
# Core functions
# -------------------------

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

def parse_totals_only(path, progress=None, sample_every=1, sample_offset=0, slab_only=False):
    """
    Super-fast path for -t/-o only: count allocations and pages per order.
    When slab_only, reads PFN Flags and keeps only slab-flagged allocations.
    """
    order_stats = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    alloc_idx = 0
    last_pos = 0
    pending_order = None

    # bigger buffer helps for huge files
    with open(path, 'r', encoding='utf-8', errors='replace', buffering=1024*1024) as f:
        for raw in f:
            # cheaper than .strip(); header is at line start
            line = raw.rstrip('\n')
            m = ALLOC_HEADER_ORDER_RE.match(line)
            if m:
                pending_order = None
                # systematic sampling: keep every N-th allocation
                take = (alloc_idx % sample_every == sample_offset)
                alloc_idx += 1
                if not take:
                    if progress:
                        pos = _file_progress_pos(f)
                        if pos is not None:
                            last_pos = pos
                            progress.update(pos)
                    continue
                try:
                    order = int(m.group(1))
                except ValueError:
                    continue
                if slab_only:
                    pending_order = order
                else:
                    w = sample_every  # scale up to estimate full dataset
                    pages = (1 << order) * w
                    order_stats[order]['allocs'] += w
                    order_stats[order]['pages'] += pages
            elif slab_only and pending_order is not None and line.startswith('PFN'):
                if _pfn_flags_has_slab(line):
                    w = sample_every
                    pages = (1 << pending_order) * w
                    order_stats[pending_order]['allocs'] += w
                    order_stats[pending_order]['pages'] += pages
                pending_order = None
            if progress:
                pos = _file_progress_pos(f)
                if pos is not None:
                    last_pos = pos
                    progress.update(pos)
    if progress:
        progress.done(progress.total if progress.total else last_pos)
    return order_stats

def parse_page_owner(filename, debug=False, strict=False, progress=None,
                     collect_calltraces=False, slab_only_calltraces=False,
                     slab_only_scope=False, sample_every=1, sample_offset=0):
    """
    Parse page_owner text file.

    - Sampling: keep every N-th allocation (index % sample_every == sample_offset),
      scale all counts/pages by N for sampled ones. Unsampled allocations are
      fast-skipped (consume lines until blank), avoiding heavy work.

    - collect_calltraces: when True, group identical calltraces (weighted).
      When False, skip hashing/indexing for speed and memory.
    - slab_only_calltraces: with collect_calltraces, keep only allocations
      whose PFN Flags include 'slab'.
    - slab_only_scope: with -p, -m, -t, or -o, keep only slab-flagged allocations in
      process, module, and per-order totals.

    Returns (in this exact order):
      process_data, module_data, slab_data, calltrace_data, calltrace_index,
      process_module_pages, total_allocs, skipped_allocations,
      valid_allocation_detected, has_process_metadata, allocations,
      order_stats, proc_slab_stats
    """
    process_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    module_data = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    slab_data   = defaultdict(lambda: {'allocs': 0, 'pages': 0})
    process_module_pages = defaultdict(lambda: {'pages': 0, 'allocs': 0})

    calltrace_data  = defaultdict(lambda: {'count': 0, 'pages': 0})
    calltrace_index = {}
    allocations     = []

    skipped_allocations = {'missing_match': 0, 'incomplete_trace': 0, 'invalid_order': 0}

    # Per-order totals (for -t and footer summaries)
    order_stats = defaultdict(lambda: {'allocs': 0, 'pages': 0})

    # Per-process slab vs non-slab totals (Type-2 meaningful)
    proc_slab_stats = defaultdict(lambda: {
        'slab_pages': 0, 'non_slab_pages': 0,
        'slab_allocs': 0, 'non_slab_allocs': 0
    })

    current_allocation = {}
    current_calltrace  = []
    in_trace = False
    total_allocs = 0
    valid_allocation_detected = False
    has_process_metadata = False  # becomes True if any type2 header seen
    alloc_idx_seen = 0            # sampling counter across all allocations
    last_pos = 0

    def _is_module_token(tok: str) -> bool:
        t = tok.strip()
        if not t:
            return False
        if '<' in t or '>' in t:
            return False
        if re.fullmatch(r'0x[0-9A-Fa-f]+', t):
            return False
        if re.fullmatch(r'[0-9A-Fa-f]+', t):
            return False
        return bool(re.fullmatch(r'[A-Za-z0-9_\-\.]+', t))

    def _parse_frame(line: str):
        """Return (func_name, module_name_or_None)."""
        mod_match = re.findall(r'\[([^\]]+)\]', line)
        module = None
        if mod_match:
            for tok in reversed(mod_match):
                if _is_module_token(tok):
                    module = tok.strip()
                    break
        s = re.sub(r'^\s*\[[^]]+\]\s*', '', line)
        func = s.split('+', 1)[0].strip()
        if not func:
            func = line.strip()
        return func, module

    def finalize_current():
        nonlocal in_trace, current_allocation, current_calltrace, total_allocs, has_process_metadata
        if not in_trace or 'order' not in current_allocation:
            return

        # If this allocation was not sampled, drop it early (normally we never
        # enter in_trace for unsampled blocks thanks to fast-skip below, but keep safe).
        if not current_allocation.get('sampled', False):
            in_trace = False
            current_allocation = {}
            current_calltrace = []
            return

        order = current_allocation.get('order', 0)
        base_pages = 1 << order
        w = int(current_allocation.get('weight', 1)) or 1
        pages = base_pages * w
        process_name = current_allocation.get('process', 'Unknown')
        is_slab_flags = current_allocation.get('flags_slab', False)
        skip_slab_scope = slab_only_scope and not is_slab_flags

        # Process totals
        if not skip_slab_scope:
            process_data[process_name]['allocs'] += w
            process_data[process_name]['pages']  += pages

            # Totals per-order
            order_stats[order]['allocs'] += w
            order_stats[order]['pages']  += pages

        # Parse frames
        frames = [_parse_frame(l) for l in current_calltrace]

        # First allocator frame
        alloc_idx = None
        for i, (func, _mod) in enumerate(frames):
            if ALLOCATOR_FUNC_RE.search(func):
                alloc_idx = i
                break

        # Slab classification: treat as slab if any slab allocator appears
        is_slab_alloc = any(SLAB_ALLOCATOR_FUNC_RE.search(func) for func, _ in frames)

        # Per-process slab/non-slab (meaningful with type2 process names)
        if process_name != 'Unknown' and not skip_slab_scope:
            if is_slab_alloc:
                proc_slab_stats[process_name]['slab_pages']  += pages
                proc_slab_stats[process_name]['slab_allocs'] += w
            else:
                proc_slab_stats[process_name]['non_slab_pages']  += pages
                proc_slab_stats[process_name]['non_slab_allocs'] += w

        # Module attribution
        attributed_module = None
        if alloc_idx is not None:
            if strict:
                func0, mod0 = frames[alloc_idx]
                if mod0 and MODULE_ALLOC_LIKE_RE.search(func0):
                    attributed_module = mod0
                else:
                    for j in range(alloc_idx + 1, len(frames)):
                        funcj, modj = frames[j]
                        if modj and MODULE_ALLOC_LIKE_RE.search(funcj):
                            attributed_module = modj
                            break
            else:
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
            if not strict:
                for _func, m in frames:
                    if m:
                        attributed_module = m
                        break

        # (Optional) slab-ish functions record
        for func, _ in frames:
            if re.search(r'kmalloc|slab|cache|kfree', func, re.IGNORECASE):
                slab_data[func]['allocs'] += w
                slab_data[func]['pages']  += pages

        # Apply module attribution
        if attributed_module and not skip_slab_scope:
            module_data[attributed_module]['allocs'] += w
            module_data[attributed_module]['pages']  += pages
            process_module_pages[(process_name, attributed_module)]['pages']  += pages
            process_module_pages[(process_name, attributed_module)]['allocs'] += w

        # Call trace grouping (only when requested)
        if collect_calltraces:
            if slab_only_calltraces and not is_slab_flags:
                pass
            else:
                trace_str = "\n".join(current_calltrace)
                h = hashlib.blake2s(trace_str.encode(), digest_size=16).hexdigest()
                trace_key = h
                if trace_key not in calltrace_index:
                    calltrace_index[trace_key] = current_calltrace.copy()
                calltrace_data[trace_key]['count'] += w
                calltrace_data[trace_key]['pages'] += pages
                allocations.append({
                    "process": process_name,
                    "trace_key": trace_key,
                    "pages": pages,
                    "weight": w
                })

        if not skip_slab_scope:
            total_allocs += w
        in_trace = False
        current_allocation = {}
        current_calltrace = []

    # Larger buffer helps reduce syscalls on huge files
    with open(filename, 'r', encoding='utf-8', errors='replace', buffering=4*1024*1024) as f:
        for raw_line in f:
            line = raw_line.rstrip('\n')

            if line.startswith("Page allocated"):
                # If we somehow were in a trace, finalize the previous one
                if in_trace:
                    finalize_current()

                valid_allocation_detected = True

                # Decide sampling for this allocation
                take_this = (alloc_idx_seen % sample_every == sample_offset)
                alloc_idx_seen += 1

                # Try fast parse for type-2 headers
                m2 = None
                if " pid " in line and " tgid " in line and " ts " in line:
                    try:
                        oi = line.find("order ")
                        ci = line.find(",", oi)
                        order = int(line[oi+6:ci].strip())

                        pi = line.find("pid ", ci) + 4
                        ci2 = line.find(",", pi)
                        pid = int(line[pi:ci2].strip())

                        ti = line.find("tgid ", ci2) + 5
                        si = line.find(" (", ti)
                        tgid = int(line[ti:si].strip())

                        ei = line.find("), ts ", si)
                        comm = line[si+2:ei]

                        ts = int(line[ei+6:].split()[0])
                        m2 = (order, pid, tgid, comm, ts)
                    except Exception:
                        m2 = None

                if not m2:
                    rx2 = re.search(r"order (\d+), mask .*?, pid (\d+), tgid (\d+) \((.*?)\), ts (\d+)(?:\s*ns)?", line)
                    if rx2:
                        try:
                            m2 = (int(rx2.group(1)), int(rx2.group(2)), int(rx2.group(3)), rx2.group(4), int(rx2.group(5)))
                        except Exception:
                            m2 = None

                if m2:
                    # Type-2 allocation
                    if not take_this:
                        # FAST SKIP: consume frames until blank line
                        for raw_line in f:
                            if raw_line == '\n':
                                break
                        if progress:
                            pos = _file_progress_pos(f)
                            if pos is not None:
                                last_pos = pos
                                progress.update(pos)
                        in_trace = False
                        continue

                    order, pid, tgid, comm, ts = m2
                    current_allocation = {
                        'order': order,
                        'pid': pid,
                        'tgid': tgid,
                        'process': comm,
                        'ts': ts,
                        'sampled': True,
                        'weight': sample_every,
                    }
                    has_process_metadata = True
                    in_trace = True
                    current_calltrace = []
                    if progress:
                        pos = _file_progress_pos(f)
                        if pos is not None:
                            last_pos = pos
                            progress.update(pos)
                    continue

                # Type-1 header
                m1 = None
                if "order " in line and ", mask" in line:
                    try:
                        oi = line.find("order ")
                        ci = line.find(",", oi)
                        m1 = int(line[oi+6:ci].strip())
                    except Exception:
                        m1 = None
                if m1 is None:
                    rx1 = re.search(r"order (\d+), mask", line)
                    if rx1:
                        try:
                            m1 = int(rx1.group(1))
                        except Exception:
                            m1 = None

                if m1 is not None:
                    if not take_this:
                        # FAST SKIP: consume frames until blank line
                        for raw_line in f:
                            if raw_line == '\n':
                                break
                        if progress:
                            pos = _file_progress_pos(f)
                            if pos is not None:
                                last_pos = pos
                                progress.update(pos)
                        in_trace = False
                        continue

                    current_allocation = {
                        'order': m1,
                        'pid': -1,
                        'tgid': -1,
                        'process': 'Unknown',
                        'ts': -1,
                        'sampled': True,
                        'weight': sample_every,
                    }
                    in_trace = True
                    current_calltrace = []
                else:
                    skipped_allocations['missing_match'] += 1
                    in_trace = False

                if progress:
                    pos = _file_progress_pos(f)
                    if pos is not None:
                        last_pos = pos
                        progress.update(pos)

            elif line.startswith("PFN"):
                if in_trace:
                    current_allocation['flags_slab'] = _pfn_flags_has_slab(line)
                if progress:
                    pos = _file_progress_pos(f)
                    if pos is not None:
                        last_pos = pos
                        progress.update(pos)
                continue

            elif in_trace and line:
                # We only enter in_trace for sampled allocations
                current_calltrace.append(line)
                if progress:
                    pos = _file_progress_pos(f)
                    if pos is not None:
                        last_pos = pos
                        progress.update(pos)

            elif in_trace and not line:
                # blank line ends allocation block
                finalize_current()
                if progress:
                    pos = _file_progress_pos(f)
                    if pos is not None:
                        last_pos = pos
                        progress.update(pos)

            elif not line:
                # standalone blank line
                if in_trace:
                    skipped_allocations['incomplete_trace'] += 1
                in_trace = False
                if progress:
                    pos = _file_progress_pos(f)
                    if pos is not None:
                        last_pos = pos
                        progress.update(pos)

        # End-of-file finalize
        finalize_current()

    if progress:
        progress.done(progress.total if progress.total else last_pos)

    return (process_data, module_data, slab_data, calltrace_data, calltrace_index,
            process_module_pages, total_allocs, skipped_allocations,
            valid_allocation_detected, has_process_metadata, allocations,
            order_stats, proc_slab_stats)

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

def _sort_rank_rows(rows, sort_by, default_col, top_n):
    """Sort rows by 1-based column index and return top_n. Column 1 is the name field."""
    if not rows:
        return []
    ncols = len(rows[0])
    col = sort_by if sort_by is not None else default_col
    if col < 1 or col > ncols:
        raise ValueError(f"sort-by must be 1..{ncols} for this report")
    key_idx = col - 1

    def sort_key(row):
        val = row[key_idx]
        if isinstance(val, str):
            return val.lower()
        return val

    rows = sorted(rows, key=sort_key, reverse=True)
    return rows[:top_n]

def show_top(data, label, unit, key='pages', top_n=10, sort_by=None, slab_only=False):
    unit_short = _unit_short(unit)
    if label == "Modules":
        title = "Top memory-using modules"
        if slab_only:
            title += " (slab only)"
        print(f"{title}:")
        print(f"{'Module':<25}{'Allocations':>15}{'Memory (' + unit_short + ')':>15}")
        print("=" * 55)
    else:
        title = f"Top {top_n} {label}"
        if slab_only:
            title += " (slab only)"
        print(f"{title}:")
        print(f"{'Application':<25}{'Allocations':>15}{'Memory (' + unit_short + ')':>15}")
        print("=" * 50)

    if label == "Modules":
        sorted_items = sorted(data.items(), key=lambda x: x[1][key], reverse=True)[:top_n]
        total_pages = sum(stats[key] for stats in data.values())
        total_allocs = sum(stats['allocs'] for stats in data.values())
    else:
        proc_rows = [(name, stats['allocs'], stats['pages']) for name, stats in data.items()]
        sorted_rows = _sort_rank_rows(proc_rows, sort_by, default_col=3, top_n=top_n)
        sorted_items = [(name, {'allocs': allocs, 'pages': pages}) for name, allocs, pages in sorted_rows]
        total_pages = sum(stats['pages'] for stats in data.values())
        total_allocs = sum(stats['allocs'] for stats in data.values())

    for name, stats in sorted_items:
        mem, unit_label = convert_pages(stats['pages'], unit)
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

def show_calltraces(calltrace_data, calltrace_index, unit, top_n=5, filter_by_process=None, allocations=None, slab_only=False):
    title = f"Top {top_n} Call Traces"
    if slab_only:
        title += " (slab only)"
    print(f"{title}:")
    print("=" * 50)

    if filter_by_process:
        filtered_stats = defaultdict(lambda: {'count': 0, 'pages': 0})
        for alloc in allocations or ():
            if alloc['process'] == filter_by_process:
                w = alloc.get('weight', 1)
                filtered_stats[alloc['trace_key']]['count'] += w
                filtered_stats[alloc['trace_key']]['pages'] += alloc['pages']
    else:
        filtered_stats = calltrace_data

    if not filtered_stats:
        if filter_by_process:
            if slab_only:
                print(f"No slab call traces found for process '{filter_by_process}'")
            else:
                print(f"No call traces found for process '{filter_by_process}'")
        elif slab_only:
            print("No slab call traces found")
        else:
            print("No call traces found")
        return

    sorted_traces = sorted(filtered_stats.items(), key=lambda x: x[1]['pages'], reverse=True)[:top_n]
    for i, (key, data) in enumerate(sorted_traces, 1):
        mem, unit_label = convert_pages(data['pages'], unit)
        print(f"#{i}: Seen {data['count']} times, {mem:.2f} {unit_label}")
        print("\n".join(calltrace_index[key]))
        print("-" * 50)

def show_processes_for_module(process_module_pages, module_name, unit, top_n=10, sort_by=None, slab_only=False):
    aggregated = defaultdict(lambda: {'pages': 0, 'allocs': 0})
    for (proc, mod), stats in process_module_pages.items():
        if mod == module_name:
            aggregated[proc]['pages'] += stats['pages']
            aggregated[proc]['allocs'] += stats['allocs']
    if not aggregated:
        print(f"No allocations found for module '{module_name}'.")
        return

    unit_short = _unit_short(unit)
    title = f"Top {top_n} Processes using module '{module_name}'"
    if slab_only:
        title += " (slab only)"
    print(f"{title}:")
    print(f"{'Application':<25}{'Allocations':>15}{'Memory (' + unit_short + ')':>15}")
    print("=" * 50)
    proc_rows = [(proc, stats['allocs'], stats['pages']) for proc, stats in aggregated.items()]
    sorted_rows = _sort_rank_rows(proc_rows, sort_by, default_col=3, top_n=top_n)
    total_pages = sum(stats['pages'] for stats in aggregated.values())
    total_allocs = sum(stats['allocs'] for stats in aggregated.values())
    for proc, allocs, pages in sorted_rows:
        mem, unit_label = convert_pages(pages, unit)
        print(f"{proc:<25}{allocs:>15}{mem:>15.2f} {unit_label}")
    total_mem, unit_label = convert_pages(total_pages, unit)
    print("-" * 50)
    print(f"{'Total':<25}{total_allocs:>15}{total_mem:>15.2f} {unit_label}")
    print("=" * 50)

def show_modules_breakdown(process_data, process_module_pages, unit, top_n=10, sort_by=None, slab_only=False):
    """-p -m: show per-process module vs non-module memory usage."""
    unit_short = _unit_short(unit)
    proc_rows = []
    total_mod_pages = total_non_pages = 0

    for proc, st in process_data.items():
        total_pages = st['pages']
        # Sum module-attributed pages for this process
        mod_pages = sum(stats['pages'] for (p, _), stats in process_module_pages.items() if p == proc)
        non_pages = total_pages - mod_pages

        mod_mem, _ = convert_pages(mod_pages, unit)
        non_mem, _ = convert_pages(non_pages, unit)
        tot_mem, _ = convert_pages(total_pages, unit)

        proc_rows.append((proc, mod_mem, non_mem, tot_mem))
        total_mod_pages += mod_pages
        total_non_pages += non_pages

    # Sort and take top_n (default: column 4 = Total)
    top_rows = _sort_rank_rows(proc_rows, sort_by, default_col=4, top_n=top_n)

    # Print
    title = f"Top {top_n} Processes"
    if slab_only:
        title += " (slab only)"
    print(f"{title}:")
    print(f"{'Application':<20}{'Modules (' + unit_short + ')':>18}{'Non Modules (' + unit_short + ')':>20}{'Total (' + unit_short + ')':>16}")
    print("=" * 80)
    for proc, mod_mem, non_mem, tot_mem in top_rows:
        print(f"{proc:<20}{mod_mem:>18.2f}{non_mem:>20.2f}{tot_mem:>16.2f}")
    print("-" * 80)
    total_mod_mem, _ = convert_pages(total_mod_pages, unit)
    total_non_mem, _ = convert_pages(total_non_pages, unit)
    total_mem, _ = convert_pages(total_mod_pages + total_non_pages, unit)
    print(f"{'Total':<20}{total_mod_mem:>18.2f}{total_non_mem:>20.2f}{total_mem:>16.2f}")

def show_skipped(skipped_allocations, verbose=False):
    if not verbose:
        return
    skipped_total = sum(skipped_allocations.values())
    print(f"Total skipped: {skipped_total}")
    for reason, count in skipped_allocations.items():
        print(f" - {reason.replace('_', ' ').capitalize()}: {count}")

def show_totals(order_stats, unit, slab_only=False):
    unit_short = _unit_short(unit)
    total_allocs = sum(v['allocs'] for v in order_stats.values())
    total_pages = sum(v['pages'] for v in order_stats.values())
    total_mem, total_unit = convert_pages(total_pages, unit)
    label = "Summary (slab only)" if slab_only else "Summary"
    print(f"{label:<25}{'Allocations':>15}{'Memory (' + unit_short + ')':>15}")
    print("=" * 55)
    print(f"{'Total':<25}{total_allocs:>15}{total_mem:>15.2f} {total_unit}")

def show_totals_per_order(order_stats, unit, slab_only=False):
    unit_short = _unit_short(unit)
    label = "Order (slab only)" if slab_only else "Order"
    print(f"{label:<25}{'Allocations':>15}{'Memory (' + unit_short + ')':>15}")
    print("=" * 55)
    for order in sorted(order_stats.keys()):
        allocs = order_stats[order]['allocs']
        pages = order_stats[order]['pages']
        mem, _ = convert_pages(pages, unit)
        print(f"{order:<25}{allocs:>15}{mem:>15.2f}")
    print("=" * 55)
    total_allocs = sum(v['allocs'] for v in order_stats.values())
    total_pages = sum(v['pages'] for v in order_stats.values())
    total_mem, total_unit = convert_pages(total_pages, unit)
    print(f"{'Total':<25}{total_allocs:>15}{total_mem:>15.2f} {total_unit}")

def show_slab_by_process(proc_slab_stats, unit, top_n=10, slab_only=False):
    """-s (Type-2 only): slab-only usage per process, top N by slab memory."""
    unit_short = _unit_short(unit)
    rows = [(proc, stats) for proc, stats in proc_slab_stats.items() if stats['slab_pages'] > 0]
    rows.sort(key=lambda x: x[1]['slab_pages'], reverse=True)

    # Totals over all processes
    total_allocs = sum(st['slab_allocs'] for _, st in rows)
    total_pages = sum(st['slab_pages'] for _, st in rows)

    # Header
    title = f"Top {top_n} Processes"
    if slab_only:
        title += " (slab only)"
    print(f"{title}:")
    print(f"{'Application':<20}{'Allocations':>15}{'Memory (' + unit_short + ')':>15}")
    print("=" * 50)

    # Top N only
    for proc, st in rows[:top_n]:
        mem, unit_label = convert_pages(st['slab_pages'], unit)
        print(f"{proc:<20}{st['slab_allocs']:>15}{mem:>15.2f} {unit_label}")

    # Footer total
    total_mem, unit_label = convert_pages(total_pages, unit)
    print("-" * 50)
    print(f"{'Total':<20}{total_allocs:>15}{total_mem:>15.2f} {unit_label}")

def show_slab_breakdown(proc_slab_stats, unit, top_n=10, sort_by=None, slab_only=False):
    """-s -p (Type-2 only): slab vs non-slab per process, top N by total."""
    unit_short = _unit_short(unit)
    # Build rows
    rows = []
    total_slab_pages = total_non_pages = 0
    for proc, st in proc_slab_stats.items():
        total_pages = st['slab_pages'] + st['non_slab_pages']
        slab_mem, _ = convert_pages(st['slab_pages'], unit)
        non_mem, _ = convert_pages(st['non_slab_pages'], unit)
        total_mem, _ = convert_pages(total_pages, unit)
        rows.append((proc, slab_mem, non_mem, total_mem))
        total_slab_pages += st['slab_pages']
        total_non_pages += st['non_slab_pages']

    # Sort and take top_n (default: column 4 = Total)
    top_rows = _sort_rank_rows(rows, sort_by, default_col=4, top_n=top_n)

    # Print header exactly as expected
    title = f"Top {top_n} Processes"
    if slab_only:
        title += " (slab only)"
    print(f"{title}:")
    print(f"{'Application':<20}{'Slabs (' + unit_short + ')':>18}{'Non Slabs (' + unit_short + ')':>20}{'Total (' + unit_short + ')':>16}")
    print("=" * 80)
    for proc, slab_mem, non_mem, total_mem in top_rows:
        print(f"{proc:<20}{slab_mem:>18.2f}{non_mem:>20.2f}{total_mem:>16.2f}")
    print("-" * 80)
    total_slab_mem, _ = convert_pages(total_slab_pages, unit)
    total_non_mem, _ = convert_pages(total_non_pages, unit)
    total_mem, _ = convert_pages(total_slab_pages + total_non_pages, unit)
    print(f"{'Total':<20}{total_slab_mem:>18.2f}{total_non_mem:>20.2f}{total_mem:>16.2f}")

def main():
    parser = argparse.ArgumentParser(description="Analyze large page_owner file.")
    parser.add_argument("file", help="Path to the page_owner file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose status output (analysis info, skipped allocations)")
    parser.add_argument("-o", "--per-order", action="store_true",
                        help="Show per-order allocation and memory breakdown (with -t or by default)")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug output")
    parser.add_argument("-M", dest="unit", action="store_const", const='M', help="Show in MB")
    parser.add_argument("-K", dest="unit", action="store_const", const='K', help="Show in KB")
    parser.add_argument("-G", dest="unit", action="store_const", const='G', help="Show in GB")
    parser.add_argument("-p", "--processes", action="store_true", help="Process report (varies by mode)")
    parser.add_argument("--sort-by", type=int, metavar="COL",
                        help="With -p: sort by column number (1=Application, last column=default)")
    parser.add_argument("-m", "--modules", action="store_true", help="Show top memory-using modules")
    parser.add_argument("-s", "--slabs", action="store_true", help="Show slab usage by process (Type-2 only). With -p, show slab vs non-slab breakdown")
    parser.add_argument("-c", "--calltraces", action="store_true", help="Show top call trace patterns (see -N)")
    parser.add_argument("--slab-only", action="store_true",
                        help="With -c, -m, -p, -t, or -o: include only allocations whose PFN Flags list includes slab")
    parser.add_argument("-N", type=int, default=10, metavar="N",
                        help="Number of top entries to show in ranked reports (default: 10)")
    parser.add_argument("-t", "--total", action="store_true", help="Show total allocations/memory summary")
    parser.add_argument("--calltrace-process", type=str, help="Show call traces only for this process")
    parser.add_argument("--filter-module", type=str, help="Show top processes using this module")
    parser.add_argument("--strict", action="store_true", help="Attribute only when a module-tagged frame at/under the first allocator looks allocation-like (e.g., vx_alloc, getblk, new_*)")
    parser.add_argument("--detect-lines", type=int, default=5000, help="Max lines to scan for dump kind detection before full parse (default: 5000)")
    # Sampling options
    parser.add_argument("--sample-every", type=int, default=1,
                        help="Systematic sampling: keep every N-th allocation and scale results by N (default: 1 = no sampling)")
    parser.add_argument("--sample-offset", type=int, default=0,
                        help="With --sample-every N, keep allocations where index %% N == offset (default: 0)")

    parser.add_argument("--progress-interval", type=float, default=0.5,
                        help="Seconds between parse progress updates on stderr (default: 0.5)")

    args = parser.parse_args()
    if args.N < 1:
        parser.error("-N must be >= 1")
    unit = args.unit or 'G'

    # Default to totals if no report option is set
    if not (args.processes or args.modules or args.slabs or args.calltraces or args.total):
        args.total = True
        if args.verbose:
            print("No report option specified; defaulting to totals (-t).")

    if args.calltrace_process and not args.calltraces:
        print("Error: '--calltrace-process' requires '-c' or '--calltraces' to be specified.")
        return

    if args.slab_only and not (args.calltraces or args.modules or args.processes
                               or args.per_order or args.total):
        print("Error: '--slab-only' requires '-c', '-m', '-p', '-t', or '-o'.")
        return

    if args.filter_module and not args.processes:
        print("Error: '--filter-module' requires '-p' or '--processes' to be specified.")
        return

    if args.sort_by is not None and not args.processes:
        parser.error("--sort-by requires -p")

    if args.sort_by is not None and args.processes:
        if args.slabs:
            max_col = 4
        elif args.modules:
            max_col = 4
        else:
            max_col = 3
        if args.sort_by < 1 or args.sort_by > max_col:
            parser.error(f"--sort-by must be 1..{max_col} for this process report")

    # Fast pre-scan to detect dump kind to gate -p and -s (both require Type-2)
    dump_kind = quick_detect_dump_kind(args.file, max_lines=args.detect_lines)

    # Helper: do we have any non-process reports requested?
    non_process_reports = (
        args.total or
        (args.modules and not args.processes) or
        args.calltraces
    )

    # If -p is present, enforce type gating first to avoid long waits on Type-1
    if args.processes and dump_kind == 'type1':
        process_dependent_only = (
            args.processes and
            not non_process_reports
        )
        if process_dependent_only:
            print("Process views (-p) require a Type-2 dump with process metadata. Skipping full parse.")
            return
        else:
            print("Process views (-p) require Type-2; will skip process-based outputs.")
            args.processes = False
            if args.slabs:
                print("Slab view (-s) requires Type-2; skipping slab outputs.")
                args.slabs = False

    if args.slabs and not args.processes and dump_kind == 'type1':
        print("Slab view (-s): Requires Type-2 dump with process metadata. Skipping.")
        args.slabs = False

    # Fast totals-only path stays the same (can short-circuit before full parse)
    # Normalize sampling args
    if args.sample_every < 1:
        args.sample_every = 1
    if args.sample_offset < 0 or (args.sample_every > 1 and args.sample_offset >= args.sample_every):
        print(f"Warning: clamping --sample-offset to range [0,{max(0,args.sample_every-1)}]")
        args.sample_offset = 0

    only_totals = args.total and not (args.processes or args.modules or args.slabs or args.calltraces)
    if only_totals:
        if args.verbose:
            kind_msg = f"Detected dump kind: {dump_kind}" if dump_kind != 'unknown' else "Dump kind: unknown"
            print(f"Analyzing {args.file} (totals only). {kind_msg}")
        total_bytes = _regular_file_size(args.file)
        prog = Progress("Totals-only parse", total_bytes=total_bytes, interval=args.progress_interval)
        order_stats = parse_totals_only(
            args.file,
            progress=prog,
            sample_every=args.sample_every,
            sample_offset=args.sample_offset,
            slab_only=args.slab_only,
        )
        if args.per_order:
            show_totals_per_order(order_stats, unit, slab_only=args.slab_only)
        else:
            show_totals(order_stats, unit, slab_only=args.slab_only)
        return

    if args.verbose:
        kind_msg = f"Detected dump kind: {dump_kind}" if dump_kind != 'unknown' else "Dump kind: unknown"
        print(f"Analyzing {args.file} with unit {unit}{' (strict mode)' if args.strict else ''}. {kind_msg}")

    total_bytes = _regular_file_size(args.file)
    prog = Progress("Full parse", total_bytes=total_bytes, interval=args.progress_interval)

    slab_only_calltraces = args.slab_only and args.calltraces
    slab_only_scope = args.slab_only and (
        args.processes or args.modules or args.per_order or args.total
    )

    (process_data, module_data, slab_data, calltrace_data, calltrace_index,
     process_module_pages, total_allocs, skipped_allocations,
     valid_allocation_detected, has_process_metadata, allocations,
     order_stats, proc_slab_stats) = parse_page_owner(
        args.file, args.debug, strict=args.strict, progress=prog,
        collect_calltraces=args.calltraces,
        slab_only_calltraces=slab_only_calltraces,
        slab_only_scope=slab_only_scope,
        sample_every=args.sample_every,
        sample_offset=args.sample_offset,
    )

    # --- Modules report (only when -m is used without -p or -s)
    if args.modules and not args.processes and not args.slabs:
        show_top(module_data, "Modules", unit, top_n=args.N, slab_only=slab_only_scope)

    # Slabs report (Type-2 only behavior)
    if args.slabs and not args.processes:
        if not has_process_metadata:
            print("Slab view (-s): Requires Type-2 dump with process metadata.")
        else:
            show_slab_by_process(proc_slab_stats, unit, top_n=args.N, slab_only=slab_only_scope)

    # --- Processes report
    if args.processes:
        if args.slabs:
            # -p -s: slab vs non-slab per process
            if not has_process_metadata:
                print("Slab view (-s): Requires Type-2 dump with process metadata.")
            else:
                show_slab_breakdown(proc_slab_stats, unit, top_n=args.N, sort_by=args.sort_by, slab_only=slab_only_scope)
        elif args.modules:
            # -p -m: modules vs non-modules per process
            if not has_process_metadata:
                print("Process metadata (pid/tgid/comm) not present in this dump; 'Unknown' will be shown as process.")
            show_modules_breakdown(process_data, process_module_pages, unit, top_n=args.N, sort_by=args.sort_by, slab_only=slab_only_scope)
        else:
            # plain -p
            if args.filter_module:
                show_processes_for_module(process_module_pages, args.filter_module, unit, top_n=args.N, sort_by=args.sort_by, slab_only=slab_only_scope)
            else:
                if not has_process_metadata:
                    print("Process metadata (pid/tgid/comm) not present in this dump; 'Unknown' will be shown as process.")
                show_top(process_data, "Processes", unit, top_n=args.N, sort_by=args.sort_by, slab_only=slab_only_scope)

    # Call traces
    if args.calltraces:
        show_calltraces(
            calltrace_data, calltrace_index, unit, top_n=args.N,
            filter_by_process=args.calltrace_process,
            allocations=allocations,
            slab_only=slab_only_calltraces,
        )

    if args.per_order:
        show_totals_per_order(order_stats, unit, slab_only=args.slab_only)

    show_skipped(skipped_allocations, args.verbose)

if __name__ == "__main__":
    main()
