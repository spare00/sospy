#!/usr/bin/env python3

import sys
import re
import subprocess
from collections import deque

SECTION_HEADERS = [
    "%usr",
    "proc/s",
    "pswpin/s",
    "pgpgin/s",
    "rtps",
    "kbmemfree",
    "kbswpfree",
    "kbhugfree",
    "dentunusd",
    "runq-sz",
    "DEV",
    "rxpck/s",
    "rxerr/s",
    "call/s",
    "scall/s",
    "totsck",
    "total/s",
]

TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(?:\s+(?:AM|PM))?\b")


def normalize_tokens(line):
    tokens = line.split()
    if len(tokens) >= 2 and tokens[1] in ("AM", "PM"):
        return [tokens[0]] + tokens[2:]
    return tokens


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
        return f"sos_commands/sar/sar{result.stdout.strip()}"
    except Exception as e:
        print(f"Failed to determine SAR file from date: {e}")
        sys.exit(1)


def is_section_header(line):
    if not TIME_RE.match(line):
        return False
    tokens = normalize_tokens(line)
    return any(m in tokens for m in SECTION_HEADERS)


def is_cpu_like_header(line):
    if not TIME_RE.match(line):
        return False
    tokens = normalize_tokens(line)
    return "CPU" in tokens


def process_segment(lines, tail_lines=None, debug=False):
    all_blocks = []

    # CPU-like section state
    in_cpu = False
    cpu_header = ""
    cpu_data = []
    cpu_avg_all = ""

    # Normal section state
    in_normal = False
    section_header = ""
    buffer = deque()
    average_line = ""

    def flush_cpu():
        nonlocal in_cpu, cpu_header, cpu_data, cpu_avg_all
        if not cpu_header:
            return

        shown = cpu_data[-tail_lines:] if tail_lines else cpu_data
        block = [cpu_header] + shown
        if cpu_avg_all:
            block.append(cpu_avg_all)

        if len(block) > 1:
            all_blocks.append(block)

        in_cpu = False
        cpu_header = ""
        cpu_data = []
        cpu_avg_all = ""

    def flush_normal():
        nonlocal in_normal, section_header, buffer, average_line
        if not buffer:
            return

        shown = list(buffer)[-tail_lines:] if tail_lines else list(buffer)
        block = [section_header] + shown
        if average_line:
            block.append(average_line)

        all_blocks.append(block)

        in_normal = False
        section_header = ""
        buffer.clear()
        average_line = ""

    for raw in lines:
        line = raw.rstrip()

        # ---- CPU-like header ----
        if is_cpu_like_header(line):
            if in_cpu:
                flush_cpu()
            flush_normal()

            cpu_header = line
            cpu_data = []
            cpu_avg_all = ""
            in_cpu = True
            continue

        # ---- Inside CPU-like section ----
        if in_cpu:
            if line.startswith("Average:"):
                parts = line.split()
                if len(parts) > 1 and parts[1] == "all":
                    cpu_avg_all = line
                continue

            if is_section_header(line):
                flush_cpu()
                # fall through

            elif TIME_RE.match(line):
                parts = normalize_tokens(line)
                if len(parts) > 1 and parts[1] == "all":
                    cpu_data.append(line)
                continue

        # ---- Normal section header ----
        if is_section_header(line):
            flush_normal()
            section_header = line
            in_normal = True
            buffer.clear()
            average_line = ""
            continue

        if not in_normal:
            continue

        # ---- Normal Average ----
        if line.startswith("Average:"):
            average_line = line
            flush_normal()
            continue

        # ---- Normal data ----
        if TIME_RE.match(line):
            buffer.append(line)

    # EOF flush
    if in_cpu:
        flush_cpu()
    flush_normal()

    return all_blocks


def parse_sar_sections(filepath, tail_lines=None, debug=False, verbose=False):
    with open(filepath, "r") as f:
        raw_lines = f.readlines()

    print(f"Reading SAR file: {filepath}\n")

    segments = []
    current = []

    for line in raw_lines:
        if "LINUX RESTART" in line:
            if current:
                segments.append(current)
            segments.append("RESTART")
            current = []
        else:
            current.append(line)

    if current:
        segments.append(current)

    for segment in segments:
        if segment == "RESTART":
            print("RESTART\n")
            continue

        blocks = process_segment(segment, tail_lines, debug=debug)
        for block in blocks:
            for l in block:
                print(l)
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
