#!/usr/bin/env python3

import os
import re
import sys


HEAP_RE = re.compile(r"^-Xm([sx])([0-9]+)([kKmMgG]?)$")
JAVA_RE = re.compile(r"^(java|java\.bin)$", re.IGNORECASE)


def to_mb(flag):
    match = HEAP_RE.match(flag)
    if not match:
        return 0

    value = int(match.group(2))
    unit = match.group(3).lower()
    if unit == "":
        return value / (1024 * 1024)
    if unit == "k":
        return value / 1024
    if unit == "g":
        return value * 1024
    return value


def kb_to_mb(raw_value):
    try:
        return int(raw_value) / 1024.0
    except ValueError:
        return 0.0


def is_java_process(command_fields):
    if not command_fields:
        return False
    executable = os.path.basename(command_fields[0])
    return bool(JAVA_RE.match(executable))


def parse_stream(lines):
    rows = []
    java_process_count = 0
    total_rss_mb = 0.0
    total_vsz_mb = 0.0
    total_xms_mb = 0
    total_xmx_mb = 0

    for line_number, raw_line in enumerate(lines):
        if line_number == 0:
            continue

        line = raw_line.rstrip("\n")
        if not line.strip():
            continue

        fields = line.split()
        if len(fields) < 11:
            continue

        command_fields = fields[10:]
        if not is_java_process(command_fields):
            continue

        java_process_count += 1

        rss_mb = kb_to_mb(fields[5])
        vsz_mb = kb_to_mb(fields[4])
        total_rss_mb += rss_mb
        total_vsz_mb += vsz_mb

        xms_all = []
        xmx_all = []
        for field in command_fields:
            match = HEAP_RE.match(field)
            if not match:
                continue

            kind = match.group(1)
            if kind == "s":
                xms_all.append(field)
            else:
                xmx_all.append(field)

        xms_last = xms_all[-1] if xms_all else ""
        xmx_last = xmx_all[-1] if xmx_all else ""

        if xms_last:
            total_xms_mb += to_mb(xms_last)

        if xmx_last:
            total_xmx_mb += to_mb(xmx_last)

        rows.append(
            (
                fields[0],
                fields[1],
                fields[2],
                fields[3],
                fields[4],
                fields[5],
                fields[8],
                fields[9],
                f"{rss_mb:.1f}",
                f"{vsz_mb:.1f}",
                command_fields[0],
                ",".join(xms_all) if xms_all else "-",
                ",".join(xmx_all) if xmx_all else "-",
            )
        )

    return rows, java_process_count, total_rss_mb, total_vsz_mb, total_xms_mb, total_xmx_mb


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        with open(sys.argv[1], "r", encoding="utf-8") as handle:
            result = parse_stream(handle)
    else:
        result = parse_stream(sys.stdin)

    rows, java_process_count, total_rss_mb, total_vsz_mb, total_xms_mb, total_xmx_mb = result

    header = "{:<8} {:<7} {:<5} {:<5} {:<10} {:<10} {:<6} {:<10} {:<9} {:<9} {:<24} {:<24} {:<24}".format(
        "USER",
        "PID",
        "%CPU",
        "%MEM",
        "VSZ",
        "RSS",
        "START",
        "TIME",
        "RSS_MB",
        "VSZ_MB",
        "COMMAND",
        "Xms",
        "Xmx",
    )
    print(header)

    for row in rows:
        print("{:<8} {:<7} {:<5} {:<5} {:<10} {:<10} {:<6} {:<10} {:<9} {:<9} {:<24} {:<24} {:<24}".format(*row))

    print()
    print(f"Java process count: {java_process_count}")
    print(f"Total Java resident memory in use (RSS): {total_rss_mb:.1f} MB")
    print(f"Total Java virtual memory size (VSZ): {total_vsz_mb:.1f} MB")
    print(f"Total Java initial heap memory (Xms, last match used): {total_xms_mb} MB")
    print(f"Total Java max heap memory (Xmx, last match used): {total_xmx_mb} MB")


if __name__ == "__main__":
    main()
