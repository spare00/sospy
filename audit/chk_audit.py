#!/usr/bin/env python3

import os
import re
import sys
import argparse
from datetime import datetime
from collections import defaultdict, Counter
from datetime import timezone, timedelta

LOCAL_TZ = timezone(timedelta(hours=10))  # AEST
UTC_TZ   = timezone.utc

def parse_cli_time(ts):
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

# ============================================================
# Regex patterns
# ============================================================

AUDIT_MSG_RE = re.compile(
    r'msg=audit\((?P<time>[^:]+):(?P<serial>\d+)\)'
)

TYPE_RE = re.compile(r'type=(\w+)')
COMM_RE = re.compile(r'comm="([^"]+)"')
ARGV_RE = re.compile(r'a(\d+)=(".*?"|\S+)')

# ============================================================
# Time helpers
# ============================================================

def parse_audit_timestamp(ts):
    # epoch
    if re.fullmatch(r"\d+(\.\d+)?", ts):
        return datetime.fromtimestamp(float(ts))  # tzinfo 없음

    for fmt in (
        "%m/%d/%Y %H:%M:%S.%f",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
    ):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None

def parse_iso_time(ts):
    """
    Parse CLI time: YYYY-MM-DD HH:MM:SS
    """
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

# ============================================================
# Audit log parsing
# ============================================================

def parse_audit_log(filepath, start_dt=None, end_dt=None, debug=False):
    """
    Read audit.log and group records by event serial
    """
    events = defaultdict(list)

    with open(filepath, encoding="utf-8", errors="ignore") as f:
        for lineno, line in enumerate(f, 1):
            m = AUDIT_MSG_RE.search(line)
            if not m:
                continue

            ts = parse_audit_timestamp(m.group("time"))
            if not ts:
                continue

            if start_dt and ts < start_dt:
                continue
            if end_dt and ts > end_dt:
                continue

            serial = m.group("serial")
            events[serial].append((ts, line.rstrip()))

    if debug:
        print(f"[DEBUG] Parsed {len(events)} audit events")

    return events

# ============================================================
# Event analysis
# ============================================================

def analyze_events(events):
    event_type_counter = Counter()
    command_counter = Counter()
    comm_counter = Counter()

    command_timestamps = defaultdict(list)
    comm_timestamps = defaultdict(list)
    timestamps = []

    for serial, records in events.items():
        argv = {}
        is_execve = False
        comm_val = None
        event_time = records[0][0]
        timestamps.append(event_time)

        for _, line in records:
            m = TYPE_RE.search(line)
            if m:
                event_type_counter[m.group(1)] += 1

            if line.startswith("type=EXECVE"):
                is_execve = True

            if not comm_val:
                m = COMM_RE.search(line)
                if m:
                    comm_val = m.group(1)

            for idx, val in ARGV_RE.findall(line):
                argv[int(idx)] = val.strip('"')

        if is_execve and argv:
            cmd = " ".join(argv[i] for i in sorted(argv))
            command_counter[cmd] += 1
            command_timestamps[cmd].append(event_time)

        if comm_val:
            comm_counter[comm_val] += 1
            comm_timestamps[comm_val].append(event_time)

    return {
        "start_time": min(timestamps).isoformat() if timestamps else None,
        "end_time": max(timestamps).isoformat() if timestamps else None,
        "event_counts": dict(event_type_counter),
        "total_events": sum(event_type_counter.values()),
        "top_commands": command_counter.most_common(10),
        "command_timestamps": command_timestamps,
        "top_comm": comm_counter.most_common(10),
        "comm_timestamps": comm_timestamps,
    }

# ============================================================
# Performance diagnostics (messages log)
# ============================================================

SYSLOG_TS_RE = re.compile(
    r'^(?P<ts>\w{3} +\d{1,2} \d{2}:\d{2}:\d{2})\s+\S+'
)

from datetime import timezone

def extract_syslog_timestamp(line, year):
    m = SYSLOG_TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(
            f"{year} {m.group('ts')}",
            "%Y %b %d %H:%M:%S"
        )
    except ValueError:
        return None

def check_audit_performance(log_path, start_dt=None, end_dt=None, max_recent=5):
    suppressed_total = 0
    suppressed_times = []
    suppressed_lines = []

    lost_event_count = 0
    lost_event_times = []
    lost_event_lines = []

    if not os.path.isfile(log_path):
        print(f"[WARN] {log_path} not found, skipping performance check")
        return

    year = datetime.now().year

    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            ts = extract_syslog_timestamp(line, year)

            if ts:
                if start_dt and ts < start_dt:
                    continue
                if end_dt and ts > end_dt:
                    continue

            # Suppressed messages (journal)
            if "Suppressed" in line and "auditd.service" in line:
                suppressed_lines.append(line.rstrip())
                if ts:
                    suppressed_times.append(ts)

                m = re.search(r'Suppressed (\d+) messages', line)
                if m:
                    suppressed_total += int(m.group(1))

            # Lost audit events
            elif "dispatch err" in line and "event lost" in line:
                lost_event_lines.append(line.rstrip())
                lost_event_count += 1
                if ts:
                    lost_event_times.append(ts)

    print("\n=== Audit Performance Diagnostics ===")

    # ---- Suppressed section ----
    if suppressed_total:
        print(f"Total Suppressed Messages (journal): {suppressed_total}")
        if suppressed_times:
            print(f"During : {min(suppressed_times)} → {max(suppressed_times)}")

        print("Recent Suppression Events:")
        for line in suppressed_lines[-max_recent:]:
            print(f"  {line}")
    else:
        print("No suppressed messages found")

    # ---- Lost events section ----
    if lost_event_count:
        print(f"\nLost Events Reported by auditd: {lost_event_count}")
        if lost_event_times:
            print(f"During : {min(lost_event_times)} → {max(lost_event_times)}")

        print("Recent Lost Events:")
        for line in lost_event_lines[-max_recent:]:
            print(f"  {line}")
    else:
        print("\nNo lost events reported")

# ============================================================
# Output helpers
# ============================================================

def print_summary(summary):
    print("\n=== Audit Log Summary ===")
    print(f"Start Time   : {summary['start_time']}")
    print(f"End Time     : {summary['end_time']}")
    print(f"Total Events : {summary['total_events']}\n")

    print("Event Type Counts:")
    for evt, cnt in sorted(summary["event_counts"].items()):
        print(f"  {evt:<15} {cnt}")

    if summary["top_commands"]:
        print("\nTop Commands (EXECVE):")
        for cmd, cnt in summary["top_commands"]:
            print(f"  {cnt:>5}  {cmd}")

    if summary["top_comm"]:
        print("\nTop Processes (comm=):")
        for comm, cnt in summary["top_comm"]:
            print(f"  {cnt:>5}  comm=\"{comm}\"")


def show_details(summary, query):
    if query in summary["command_timestamps"]:
        times = summary["command_timestamps"][query]
        print(f"\nEXECVE: {query}")
    elif query in summary["comm_timestamps"]:
        times = summary["comm_timestamps"][query]
        print(f"\nCOMM: {query}")
    else:
        print(f"No data found for '{query}'")
        return

    print(f"Occurrences : {len(times)}")
    print(f"First Seen  : {min(times)}")
    print(f"Last Seen   : {max(times)}")

# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Offline audit.log analyzer (ausearch-free)"
    )

    parser.add_argument(
        "audit_log",
        nargs="?",
        default="var/log/audit/audit.log",
        help="Path to audit.log (default: var/log/audit/audit.log)"
    )

    parser.add_argument("--start", help="Start time (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--end", help="End time (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--details", help="Show details for command or comm")
    parser.add_argument("-p", "--check-performance", action="store_true",
                        help="Check audit performance via messages log")
    parser.add_argument("--log-path", default="var/log/messages",
                        help="Path to messages log (default: var/log/messages)")
    parser.add_argument("-d", "--debug", action="store_true")

    args = parser.parse_args()

    if not os.path.isfile(args.audit_log):
        print(f"Error: {args.audit_log} not found", file=sys.stderr)
        sys.exit(1)

    start_dt = parse_cli_time(args.start) if args.start else None
    end_dt   = parse_cli_time(args.end) if args.end else None

    events = parse_audit_log(
        args.audit_log, start_dt, end_dt, debug=args.debug
    )

    summary = analyze_events(events)
    print_summary(summary)

    if args.details:
        show_details(summary, args.details)

    if args.check_performance:
        check_audit_performance(
            args.log_path, start_dt, end_dt
        )


if __name__ == "__main__":
    main()

