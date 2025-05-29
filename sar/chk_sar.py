#!/usr/bin/env python3

import sys
import re
import subprocess
from collections import deque

# Define the unique tokens that mark the start of each sar section
SECTION_HEADERS = [
    "%usr",       # CPU section
    "proc/s",     # Context switches
    "pswpin/s",   # Swap
    "pgpgin/s",   # Paging
    "rtps",       # I/O request to a physical device
    "kbmemfree",  # Memory
    "kbswpfree",  # Swap summary
    "kbhugfree",  # Hugepages
    "dentunusd",  # File/inode
    "runq-sz",    # Load average / scheduler
    "DEV",        # Block device
    "rxpck/s",    # statistics from the network devices
    "rxerr/s",    # statistics from the network failures
    "call/s",     # NFS client activity
    "scall/s",    # NFS server activity
    "totsck",     # Statistics on sockets
    "total/s",    # Software-based network processing

]

def get_sar_file_from_date():
    try:
        with open("date", "r") as f:
            date_line = f.readline().strip()
        result = subprocess.run(
            ["~/git/sospy/date/extract_date.py"],
            input=date_line,
            capture_output=True,
            text=True,
            shell=True,
            check=True
        )
        date_suffix = result.stdout.strip()
        return f"sos_commands/sar/sar{date_suffix}"
    except Exception as e:
        print(f"Failed to determine SAR file from date: {e}")
        sys.exit(1)

def is_section_header(line):
    # Header line: timestamp + known section name (e.g. "CPU" or "proc/s")
    if not re.match(r"^\d{2}:\d{2}:\d{2}", line):
        return False
    for marker in SECTION_HEADERS:
        tokens = line.split()
        if marker in tokens:
            return True
    return False

def process_segment(lines, tail_lines=None, debug=False):
    all_blocks = []
    section_header = ""
    buffer = deque()
    average_line = ""
    is_multicore_section = False
    is_block_section = False
    section_key = None
    current_section = False
    device_set = set()

    def flush_section():
        nonlocal buffer, section_header, average_line, section_key
        nonlocal is_multicore_section, is_block_section, current_section, device_set

        if not buffer:
            return

        if is_block_section and tail_lines:
            device_count = len(device_set) or 1
            shown = list(buffer)[-tail_lines * device_count:]
        else:
            shown = list(buffer)[-tail_lines:] if tail_lines else list(buffer)

        block = [section_header] + shown
        if average_line:
            block.append(average_line)

        all_blocks.append(block)
        buffer.clear()
        average_line = ""
        section_header = ""
        section_key = None
        is_multicore_section = False
        is_block_section = False
        current_section = False
        device_set = set()

    for line in lines:
        line = line.rstrip()

        # Detect section header
        if is_section_header(line):
            tokens = line.split()
            key_fields = tuple(tokens[1:])  # skip timestamp

            if debug:
                print(f"[DEBUG] Section header detected: {line}")

            if section_key is not None and key_fields != section_key:
                flush_section()
            elif section_key is not None and key_fields == section_key:
                if debug:
                    print(f"[DEBUG] Duplicate header (skipped): {line}")
                continue

            flush_section()
            section_header = line
            section_key = key_fields
            is_multicore_section = "CPU" in key_fields
            is_block_section = any(x in key_fields for x in ("DEV", "IFACE"))  # ✅ updated here
            current_section = True
            device_set = set()
            continue

        if current_section:
            if line.startswith("Average:"):
                if is_multicore_section:
                    if " all" in line:
                        average_line = line
                else:
                    average_line = line
                flush_section()
            elif re.match(r"^\d{2}:\d{2}:\d{2}", line):
                if is_multicore_section:
                    if " all " in line:
                        buffer.append(line)
                else:
                    if is_block_section:
                        parts = line.split()
                        if len(parts) > 1:
                            device_set.add(parts[1])
                    buffer.append(line)

    flush_section()
    return all_blocks

def parse_sar_sections(filepath, tail_lines=None, debug=False, verbose=False):
    with open(filepath, "r") as f:
        raw_lines = f.readlines()

    if verbose:
        print(f"Reading SAR file: {filepath}")

    segments = []
    current_segment = []

    for idx, line in enumerate(raw_lines):
        if "LINUX RESTART" in line:
            if current_segment:
                segments.append(current_segment)
                if debug:
                    print(f"[DEBUG] Segment added before RESTART at line {idx}")
            segments.append("RESTART")
            current_segment = []
        else:
            current_segment.append(line)

    if current_segment:
        segments.append(current_segment)
        if debug:
            print(f"[DEBUG] Final segment added with {len(current_segment)} lines")

    segment_count = 0
    for segment in segments:
        if segment == "RESTART":
            print("RESTART\n")
            continue

        segment_count += 1
        if verbose:
            print(f"[INFO] Processing segment #{segment_count} with {len(segment)} lines")

        blocks = process_segment(segment, tail_lines, debug=debug)
        for block in blocks:
            for line in block:
                print(line)
            print()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Display selected sar data with aligned output.")
    parser.add_argument("sarfile", nargs="?", help="Path to sar file")
    parser.add_argument("-N", type=int, help="Show only last N lines per section")
    parser.add_argument("-t", action="store_true", help="Use SAR file from sosreport date")
    parser.add_argument("-d", action="store_true", help="Enable debug output")
    parser.add_argument("-v", action="store_true", help="Enable verbose messages")

    args = parser.parse_args()

    if args.t:
        sar_file = get_sar_file_from_date()
    elif args.sarfile:
        sar_file = args.sarfile
    else:
        print("Usage: chk_sar.py <sar_file> [-N num] [-t]")
        sys.exit(1)

    parse_sar_sections(sar_file, tail_lines=args.N, debug=args.d, verbose=args.v)

