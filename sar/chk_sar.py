#!/usr/bin/env python3

import sys
import re
import subprocess
from collections import deque

# Define the unique tokens that mark the start of each sar section
SECTION_HEADERS = [
    "usr",        # CPU section
    "proc/s",     # Context switches
    "pswpin/s",   # Swap
    "pgpgin/s",   # Paging
    "kbmemfree",  # Memory
    "kbswpfree",  # Swap summary
    "kbhugfree",  # Hugepages
    "dentunusd",  # File/inode
    "runq-sz",    # Load average / scheduler
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
    return line.startswith("00:00:00") and any(marker in line for marker in SECTION_HEADERS)

def process_segment(lines, tail_lines=None):
    all_blocks = []
    current_section = None
    section_header = ""
    buffer = deque()
    average_line = ""

    def flush_section():
        nonlocal buffer, section_header, average_line, current_section
        if not buffer:
            return
        shown = list(buffer)[-tail_lines:] if tail_lines else list(buffer)
        block = [section_header] + shown
        if average_line:
            block.append(average_line)
        all_blocks.append(block)
        buffer.clear()
        average_line = ""
        section_header = ""
        current_section = None

    for line in lines:
        line = line.rstrip()

        if is_section_header(line):
            flush_section()
            section_header = line
            current_section = True
            continue

        if current_section:
            if line.startswith("Average:"):
                average_line = line
            elif re.match(r"^\d{2}:\d{2}:\d{2}", line):
                # Apply 'all' filter only for CPU section
                if "usr" in section_header:
                    if " all " in line:
                        buffer.append(line)
                else:
                    buffer.append(line)

    flush_section()
    return all_blocks

def parse_sar_sections(filepath, tail_lines=None):
    with open(filepath, "r") as f:
        raw_lines = f.readlines()

    print(filepath)

    # Split by RESTART
    segments = []
    current = []
    for line in raw_lines:
        if "LINUX RESTART" in line:
            if current:
                segments.append(current)
            segments.append(["RESTART"])
            current = []
        else:
            current.append(line)
    if current:
        segments.append(current)

    # Process each segment
    for segment in segments:
        if segment == ["RESTART"]:
            print("RESTART")
            continue
        blocks = process_segment(segment, tail_lines)
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

    args = parser.parse_args()

    if args.t:
        sar_file = get_sar_file_from_date()
    elif args.sarfile:
        sar_file = args.sarfile
    else:
        print("Usage: chk_sar.py <sar_file> [-N num] [-t]")
        sys.exit(1)

    parse_sar_sections(sar_file, tail_lines=args.N)

