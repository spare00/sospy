#!/usr/bin/env python3

import argparse
import re
from collections import defaultdict

################################################################################
# TIMESTAMP PATTERNS (3 forms)
################################################################################

TS_PATTERNS = [
    re.compile(r"^\d{1,2}:\d{2}:\d{2}$"),                        # 12:44:49
    re.compile(r"^\d{1,2}:\d{2}:\d{2}\s+(AM|PM)$"),              # 12:44:49 AM
    re.compile(r"^\d{1,2}:\d{2}:\d{2}\s+(AM|PM)\s+[A-Za-z]+$"),  # 12:44:49 AM IST
]


def is_timestamp(s):
    s = s.strip()
    for p in TS_PATTERNS:
        if p.match(s):
            return True
    return False


################################################################################
# Extract timestamp as a SINGLE FIELD
################################################################################

def extract_timestamp_as_single_field(line):
    """
    If the line starts with a timestamp, return (timestamp, rest).
    Timestamp may be 1, 2, or 3 tokens but must be treated as ONE column.
    """
    parts = line.strip().split()
    if not parts:
        return None, line

    # Try 3-token timestamp: HH:MM:SS AM TZ
    if len(parts) >= 3:
        ts = " ".join(parts[:3])
        if is_timestamp(ts):
            return ts, " ".join(parts[3:])

    # Try 2-token timestamp: HH:MM:SS AM
    if len(parts) >= 2:
        ts = " ".join(parts[:2])
        if is_timestamp(ts):
            return ts, " ".join(parts[2:])

    # Try 1-token timestamp: HH:MM:SS
    ts = parts[0]
    if is_timestamp(ts):
        return ts, " ".join(parts[1:])

    return None, line


################################################################################
# HEADER DETECTION
################################################################################

def is_header_line(line):
    """
    A header line is either:
      - starts with "# Time ..."
      - OR contains " PID " or startswith("PID")
      - OR last token = Command
    """
    stripped = line.strip()

    if stripped.startswith("# Time"):
        return True

    # detect PID column
    if " PID " in line or stripped.startswith("PID"):
        return True

    # fallback: ends with Command
    parts = stripped.lstrip("#").strip().split()
    if parts and parts[-1] == "Command":
        return True

    return False


################################################################################
# Split file into samples
################################################################################

def split_samples(lines):
    samples = []
    curr = None

    for line in lines:
        stripped = line.strip()

        # Skip empty lines and Linux banner lines
        if not stripped:
            continue
        if stripped.startswith("Linux "):
            continue

        # START OF NEW SAMPLE
        if stripped.startswith("# Time") or " PID " in f" {stripped} ":
            # Finish previous block
            if curr:
                samples.append(curr)
            curr = [line]
        else:
            # Only add to block if we already started header
            if curr is not None:
                curr.append(line)

    # Add last block
    if curr:
        samples.append(curr)

    return samples

################################################################################
# PARSE HEADER
################################################################################

def parse_header_line(line: str):
    s = line.strip()

    # Case 1: "# Time ..." style
    if s.startswith("#"):
        s = s[1:].strip()
        return re.split(r"\s+", s)

    # Case 2: header starting with timestamp
    ts, rest = extract_timestamp_as_single_field(line)

    if ts is not None:
        # timestamp becomes a single "Time" column
        rest_fields = re.split(r"\s+", rest.strip()) if rest.strip() else []
        return ["Time"] + rest_fields

    # Fallback (should rarely happen)
    return re.split(r"\s+", s)

################################################################################
# Parse Data Line
################################################################################

def parse_data_line(line, header_fields):
    ts, rest = extract_timestamp_as_single_field(line)

    # timestamp 없는 경우 → data line 아님
    if ts is None:
        return (None, None)

    rest = rest.strip()
    if not rest:
        return (None, None)

    # ⭐ timestamp 를 tokens 에 포함
    tokens = [ts] + rest.split()

    # header 필드 수와 맞춰야 함
    try:
        cmd_index = header_fields.index("Command")
    except ValueError:
        return (None, None)

    # 데이터는 최소한 cmd_index 까지 있어야 함
    if len(tokens) < cmd_index + 1:   # Command 포함해야 함
        return (None, None)

    fixed = tokens[:cmd_index]
    command = " ".join(tokens[cmd_index:])

    parsed_fields = fixed + [command]

    # 최종적으로 header_fields 길이와 일치해야 함
    if len(parsed_fields) != len(header_fields):
        return (None, None)

    return (ts, parsed_fields)

################################################################################
# Extract numeric CPU fields
################################################################################

def clean_val(v):
    """Remove trailing % and convert to float."""
    v = v.strip()
    if v.endswith("%"):
        v = v[:-1]
    try:
        return float(v)
    except:
        return 0.0


def extract_keyed_data(ts, fields, indices, group_by):
    try:
        usr = clean_val(fields[indices["usr"]])
        system = clean_val(fields[indices["system"]])
        wait = clean_val(fields[indices["wait"]])
        cpu = clean_val(fields[indices["cpu"]])

        cmd = fields[indices["command"]].split()[0][:40]

        if group_by == "user":
            key = fields[indices["user"]]
        elif group_by == "pid":
            pid = fields[indices["pid"]]
            key = f"{pid} {cmd}"
        else:
            key = cmd

        return key, usr, system, wait, cpu

    except Exception:
        return None


################################################################################
# Field index extraction
################################################################################

def extract_field_indices(header_fields):
    field_map = {
        "user": ["USER", "UID"],
        "pid": ["PID"],
        "usr": ["%usr"],
        "system": ["%system"],
        "wait": ["%wait"],
        "cpu": ["%CPU"],
        "command": ["Command"],
    }

    indices = {}
    for k, candidates in field_map.items():
        for c in candidates:
            if c in header_fields:
                indices[k] = header_fields.index(c)
                break
    return indices


################################################################################
# MAIN CALCULATION
################################################################################

def calculate_usage(filename, group_by, sort_by, debug, sample_spec):
    with open(filename) as f:
        lines = f.readlines()

    blocks = split_samples(lines)
    total_blocks = len(blocks)

    # Parse sample index selection
    sample_indices = []
    for part in sample_spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            sample_indices.extend(range(int(a), int(b) + 1))
        else:
            sample_indices.append(int(part))

    print(f"Analyzing sample {sample_spec} of {total_blocks}\n")

    first = True

    for si in sample_indices:
        if si < 1 or si > len(blocks):
            continue

        block = blocks[si - 1]

        # Find header
        header_line = None
        for line in block:
            if is_header_line(line):
                header_line = line
                break

        if not header_line:
            print(f"Error: No header line found in sample {si}.")
            continue

        header_fields = parse_header_line(header_line)
        indices = extract_field_indices(header_fields)

        usage = defaultdict(lambda: {"usr": 0.0, "system": 0.0, "wait": 0.0,
                                     "cpu": 0.0, "count": 0})
        total = {"usr": 0.0, "system": 0.0, "wait": 0.0, "cpu": 0.0, "count": 0}

        for line in block:
            if is_header_line(line):
                continue
            ts, parsed = parse_data_line(line, header_fields)
            if not parsed:
                continue

            result = extract_keyed_data(ts, parsed, indices, group_by)
            if not result:
                continue

            key, usr, system, wait, cpu = result

            usage[key]["usr"] += usr
            usage[key]["system"] += system
            usage[key]["wait"] += wait
            usage[key]["cpu"] += cpu
            usage[key]["count"] += 1

            total["usr"] += usr
            total["system"] += system
            total["wait"] += wait
            total["cpu"] += cpu
            total["count"] += 1

        # Sort & take top 10
        sorted_usage = sorted(usage.items(),
                              key=lambda x: x[1][sort_by], reverse=True)[:10]

        if not first:
            print()
        first = False

        label = {"user": "User", "command": "Command", "pid": "PID/Command"}[group_by]

        print(f"{'%usr':<10} {'%system':<10} {'%wait':<10} "
              f"{'%CPU':<10} {'Count':<10} {label}")
        print("-" * 100)

        for key, data in sorted_usage:
            print(f"{data['usr']:<10.2f} {data['system']:<10.2f} "
                  f"{data['wait']:<10.2f} {data['cpu']:<10.2f} "
                  f"{data['count']:<10} {key}")

        print("-" * 100)
        print(f"{total['usr']:<10.2f} {total['system']:<10.2f} "
              f"{total['wait']:<10.2f} {total['cpu']:<10.2f} "
              f"{total['count']:<10} Total")


################################################################################
# MAIN
################################################################################

def main():
    p = argparse.ArgumentParser()
    p.add_argument("filename")
    p.add_argument("-u", "--user", action="store_true")
    p.add_argument("-c", "--command", action="store_true")
    p.add_argument("-p", "--pid", action="store_true")
    p.add_argument("--sort", default="cpu",
                  choices=["usr", "system", "wait", "cpu", "count"])
    p.add_argument("--debug", action="store_true")
    p.add_argument("--sample", default="1")
    args = p.parse_args()

    group_by = "command"
    if args.user:
        group_by = "user"
    elif args.pid:
        group_by = "pid"

    calculate_usage(args.filename, group_by,
                    args.sort, args.debug, args.sample)


if __name__ == "__main__":
    main()
