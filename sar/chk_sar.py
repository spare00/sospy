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
    "frmpg/s",
    "rtps",
    "kbmemfree",
    "kbswpfree",
    "kbhugfree",
    "dentunusd",
    "runq-sz",
    "TTY",
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

    def limit_items(items):
        if tail_lines is None:
            return items
        if tail_lines <= 0:
            return []
        return items[-tail_lines:]

    # CPU-like section state
    in_cpu = False
    cpu_header = ""
    cpu_data = []
    cpu_avg_all = ""

    # Normal section state
    in_normal = False
    section_header = ""
    section_key = None
    samples = deque()
    current_sample_time = None
    current_sample_rows = []
    average_lines = []

    def flush_cpu():
        nonlocal in_cpu, cpu_header, cpu_data, cpu_avg_all
        if not cpu_header:
            return

        shown = limit_items(cpu_data)
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
        nonlocal in_normal, section_header, section_key, samples
        nonlocal current_sample_time, current_sample_rows, average_lines

        if current_sample_rows:
            samples.append(list(current_sample_rows))

        if not samples and not average_lines:
            return

        shown_samples = limit_items(list(samples))
        shown = [row for sample in shown_samples for row in sample]
        block = [section_header] + shown
        if average_lines:
            block.extend(average_lines)

        all_blocks.append(block)

        in_normal = False
        section_header = ""
        section_key = None
        samples.clear()
        current_sample_time = None
        current_sample_rows = []
        average_lines = []

    for raw in lines:
        line = raw.rstrip()

        # ---- CPU-like header ----
        if is_cpu_like_header(line):
            # sar repeats the CPU header periodically within the same CPU section.
            # Do NOT flush/split the section on repeated headers; just ignore them.
            if in_cpu:
                continue

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
            current_key = tuple(normalize_tokens(line)[1:])
            if in_normal and current_key == section_key:
                section_header = line
                continue

            flush_normal()
            section_header = line
            section_key = current_key
            in_normal = True
            samples.clear()
            current_sample_time = None
            current_sample_rows = []
            average_lines = []
            continue

        if not in_normal:
            continue

        # ---- Normal Average ----
        # Some sections (e.g., DEV) may have multiple "Average:" lines (one per device).
        # Collect them all and flush when the section ends (next header / RESTART / EOF).
        if line.startswith("Average:"):
            average_lines.append(line)
            continue

        # ---- Normal data ----
        if TIME_RE.match(line):
            sample_time = normalize_tokens(line)[0]
            if current_sample_time is None or sample_time != current_sample_time:
                if current_sample_rows:
                    samples.append(list(current_sample_rows))
                current_sample_time = sample_time
                current_sample_rows = []
            current_sample_rows.append(line)

    # EOF flush
    if in_cpu:
        flush_cpu()
    flush_normal()

    return all_blocks


def parse_sar_sections(filepath, tail_lines=None, debug=False, verbose=False):
    try:
        with open(filepath, "r") as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        print(f"SAR file not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"Failed to read SAR file {filepath}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading SAR file: {filepath}\n")

    segments = []
    current = []

    for line in raw_lines:
        if "RESTART" in line:
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

    if args.N is not None and args.N < 0:
        parser.error("-N must be 0 or greater")

    if args.t:
        sar_file = get_sar_file_from_date()
    elif args.sarfile:
        sar_file = args.sarfile
    else:
        print("Usage: chk_sar.py <sar_file> [-N num] [-t]")
        sys.exit(1)

    parse_sar_sections(sar_file, tail_lines=args.N, debug=args.d, verbose=args.v)
