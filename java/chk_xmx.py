#!/usr/bin/env python3

import re
import sys


HEAP_RE = re.compile(r"^-Xm([sx])([0-9]+)([mMgG])$")


def to_mb(flag):
    match = HEAP_RE.match(flag)
    if not match:
        return 0

    value = int(match.group(2))
    unit = match.group(3).lower()
    if unit == "g":
        return value * 1024
    return value


def parse_stream(lines):
    rows = []
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

        cmd = fields[10]
        cmd = re.sub(r"-X.*", "", cmd)

        xms_all = []
        xmx_all = []

        for field in fields[10:]:
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

        if xms_all or xmx_all:
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
                    cmd,
                    ",".join(xms_all),
                    ",".join(xmx_all),
                )
            )

    return rows, total_xms_mb, total_xmx_mb


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        with open(sys.argv[1], "r", encoding="utf-8") as handle:
            rows, total_xms_mb, total_xmx_mb = parse_stream(handle)
    else:
        rows, total_xms_mb, total_xmx_mb = parse_stream(sys.stdin)

    header = "{:<8} {:<7} {:<5} {:<5} {:<10} {:<10} {:<6} {:<10} {:<35} {:<20} {:<20}".format(
        "USER", "PID", "%CPU", "%MEM", "VSZ", "RSS", "START", "TIME", "COMMAND", "Xms(all)", "Xmx(all)"
    )
    print(header)

    for row in rows:
        print("{:<8} {:<7} {:<5} {:<5} {:<10} {:<10} {:<6} {:<10} {:<35} {:<20} {:<20}".format(*row))

    print()
    print(f"Total Java initial heap memory (Xms, last match used): {total_xms_mb} MB")
    print(f"Total Java max heap memory (Xmx, last match used): {total_xmx_mb} MB")


if __name__ == "__main__":
    main()
