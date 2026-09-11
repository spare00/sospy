#!/usr/bin/env python3
"""
list_cronjobs.py - List all active cron jobs and their respective source files in an extracted sosreport.

Author: Senior Support Delivery Engineer (RHEL Kernel)
Date: September 2026
"""

import os
import sys
import re
import argparse
import json
import csv

def find_sosreport_root(start_path):
    """
    Search upwards from start_path to find the sosreport root directory.
    Checks for the presence of typical files/directories like etc/crontab or version.txt.
    """
    path = os.path.abspath(start_path)
    while True:
        if os.path.exists(os.path.join(path, "etc", "crontab")) or \
           os.path.exists(os.path.join(path, "version.txt")) or \
           os.path.exists(os.path.join(path, "sos_commands")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return os.path.abspath(start_path)

def is_valid_cron_file(filename):
    """Filter out hidden, backup, and RPM temporary files."""
    if filename.startswith('.'):
        return False
    if filename.endswith(('.rpmnew', '.rpmsave', '~', '.bak', '.swp', '.rpmorig')):
        return False
    return True

def parse_crontab_line(line, is_system_crontab=True):
    """
    Parse a single crontab line.
    Returns (schedule, user, command) or None if skipped (comments/empty/env var).
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return None

    # Identify and skip environment assignments (e.g., PATH=/bin, MAILTO=root)
    if '=' in line:
        # Check if the text before '=' is a single word and not a schedule
        first_word = line.split(None, 1)[0]
        if '=' in first_word:
            # Check if it fits an environment assignment syntax
            if re.match(r'^[A-Za-z_][A-Za-z0-9_]*\s*=\s*', first_word):
                return None

    fields = line.split()
    if not fields:
        return None

    # Check for schedule nicknames (@reboot, @daily, etc.)
    if fields[0].startswith('@'):
        if len(fields) < 2:
            return None
        if is_system_crontab:
            # @reboot root /usr/sbin/raid-check
            if len(fields) >= 3:
                schedule = fields[0]
                user = fields[1]
                command = " ".join(fields[2:])
                return schedule, user, command
        else:
            # @reboot /usr/sbin/raid-check (User crontab)
            schedule = fields[0]
            user = None
            command = " ".join(fields[1:])
            return schedule, user, command
    else:
        # Standard cron schedule format (5 schedule fields)
        if is_system_crontab:
            # 0 1 * * Sun root /usr/sbin/raid-check
            if len(fields) >= 7:
                schedule = " ".join(fields[0:5])
                user = fields[5]
                command = " ".join(fields[6:])
                return schedule, user, command
        else:
            # 22 0 * * * sudo docker image prune ...
            if len(fields) >= 6:
                schedule = " ".join(fields[0:5])
                user = None
                command = " ".join(fields[5:])
                return schedule, user, command

    return None

def parse_system_crontab(path, sos_root):
    """Parse /etc/crontab."""
    jobs = []
    if not os.path.exists(path):
        return jobs
    try:
        rel_path = os.path.relpath(path, start=sos_root)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                parsed = parse_crontab_line(line, is_system_crontab=True)
                if parsed:
                    schedule, user, command = parsed
                    jobs.append({
                        "Source File": rel_path,
                        "Category": "System Crontab",
                        "User": user,
                        "Schedule": schedule,
                        "Command": command
                    })
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
    return jobs

def parse_system_cron_dir(dir_path, category, sos_root):
    """Parse directories containing system crontabs (like /etc/cron.d)."""
    jobs = []
    if not os.path.exists(dir_path):
        return jobs
    try:
        for filename in sorted(os.listdir(dir_path)):
            if not is_valid_cron_file(filename):
                continue
            full_path = os.path.join(dir_path, filename)
            if os.path.isfile(full_path):
                rel_path = os.path.relpath(full_path, start=sos_root)
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        parsed = parse_crontab_line(line, is_system_crontab=True)
                        if parsed:
                            schedule, user, command = parsed
                            jobs.append({
                                "Source File": rel_path,
                                "Category": category,
                                "User": user,
                                "Schedule": schedule,
                                "Command": command
                            })
    except Exception as e:
        print(f"Error reading {dir_path}: {e}", file=sys.stderr)
    return jobs

def parse_anacrontab(path, sos_root):
    """Parse /etc/anacrontab."""
    jobs = []
    if not os.path.exists(path):
        return jobs
    try:
        rel_path = os.path.relpath(path, start=sos_root)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    first_word = line.split(None, 1)[0]
                    if '=' in first_word:
                        continue
                fields = line.split()
                if len(fields) >= 4:
                    period = fields[0]
                    delay = fields[1]
                    job_id = fields[2]
                    command = " ".join(fields[3:])
                    schedule = f"Period: {period} day(s), Delay: {delay}m"
                    jobs.append({
                        "Source File": rel_path,
                        "Category": "Anacrontab",
                        "User": "root",
                        "Schedule": schedule,
                        "Command": f"[{job_id}] {command}"
                    })
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
    return jobs

def scan_cron_dir_scripts(dir_path, category, schedule, sos_root):
    """Scan directory containing daily/hourly/weekly/monthly scripts."""
    jobs = []
    if not os.path.exists(dir_path):
        return jobs
    try:
        for filename in sorted(os.listdir(dir_path)):
            if not is_valid_cron_file(filename):
                continue
            full_path = os.path.join(dir_path, filename)
            if os.path.isfile(full_path):
                rel_path = os.path.relpath(full_path, start=sos_root)
                jobs.append({
                    "Source File": rel_path,
                    "Category": category,
                    "User": "root",
                    "Schedule": schedule,
                    "Command": f"Run script: {filename}"
                })
    except Exception as e:
        print(f"Error scanning directory {dir_path}: {e}", file=sys.stderr)
    return jobs

def parse_user_crontabs(dir_path, category, sos_root):
    """Parse user crontabs (like in /var/spool/cron or sos_commands/cron)."""
    jobs = []
    if not os.path.exists(dir_path):
        return jobs
    try:
        for filename in sorted(os.listdir(dir_path)):
            if not is_valid_cron_file(filename):
                continue
            full_path = os.path.join(dir_path, filename)
            if os.path.isfile(full_path):
                # Detect and skip empty or stub outputs like "no crontab for root"
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read().strip()
                    if "no crontab for" in content:
                        continue

                rel_path = os.path.relpath(full_path, start=sos_root)
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        parsed = parse_crontab_line(line, is_system_crontab=False)
                        if parsed:
                            schedule, user, command = parsed
                            # If user crontab filename is root_crontab, username is root.
                            # Otherwise, the username is the file name itself.
                            if not user:
                                user = "root" if filename == "root_crontab" else filename
                            jobs.append({
                                "Source File": rel_path,
                                "Category": category,
                                "User": user,
                                "Schedule": schedule,
                                "Command": command
                            })
    except Exception as e:
        print(f"Error reading user crontabs in {dir_path}: {e}", file=sys.stderr)
    return jobs

def print_table(headers, data):
    """Prints a beautifully formatted text-based ASCII table."""
    if not data:
        print("No cron jobs found.")
        return


    # Initialize max widths
    widths = {h: len(h) for h in headers}
    for row in data:
        for h in headers:
            val = str(row.get(h, ""))
            if len(val) > widths[h]:
                widths[h] = len(val)

    # Format line builder
    border = "+" + "+".join("-" * (widths[h] + 2) for h in headers) + "+"
    print(border)

    header_line = "|" + "|".join(f" {h:<{widths[h]}} " for h in headers) + "|"
    print(header_line)
    print(border.replace('-', '='))

    for row in data:
        row_line = "|" + "|".join(f" {str(row.get(h, '')):<{widths[h]}} " for h in headers) + "|"
        print(row_line)
    print(border)

def main():
    parser = argparse.ArgumentParser(
        description="Scans an extracted sosreport and lists all active cron jobs with schedules, users, and files."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to the extracted sosreport directory (defaults to current directory)"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "-o", "--output",
        help="File path to save the output"
    )
    args = parser.parse_args()

    # Find the proper sosreport root directory
    sos_root = find_sosreport_root(args.path)

    # Verify that we are indeed looking at a directory that contains etc or var or sos_commands
    if not any(os.path.exists(os.path.join(sos_root, d)) for d in ["etc", "var", "sos_commands"]):
        print(f"Warning: '{sos_root}' does not look like a typical extracted sosreport root.", file=sys.stderr)
        print("Proceeding anyway with paths relative to this directory.", file=sys.stderr)

    all_jobs = []

    # 1. /etc/crontab
    etc_crontab_path = os.path.join(sos_root, "etc", "crontab")
    all_jobs.extend(parse_system_crontab(etc_crontab_path, sos_root))

    # 2. /etc/anacrontab
    etc_anacrontab_path = os.path.join(sos_root, "etc", "anacrontab")
    all_jobs.extend(parse_anacrontab(etc_anacrontab_path, sos_root))

    # 3. /etc/cron.d/
    etc_cron_d_path = os.path.join(sos_root, "etc", "cron.d")
    all_jobs.extend(parse_system_cron_dir(etc_cron_d_path, "System cron.d", sos_root))

    # 4. /etc/cron.hourly/, .daily/, .weekly/, .monthly/
    all_jobs.extend(scan_cron_dir_scripts(os.path.join(sos_root, "etc", "cron.hourly"), "Hourly Scripts", "Hourly", sos_root))
    all_jobs.extend(scan_cron_dir_scripts(os.path.join(sos_root, "etc", "cron.daily"), "Daily Scripts", "Daily", sos_root))
    all_jobs.extend(scan_cron_dir_scripts(os.path.join(sos_root, "etc", "cron.weekly"), "Weekly Scripts", "Weekly", sos_root))
    all_jobs.extend(scan_cron_dir_scripts(os.path.join(sos_root, "etc", "cron.monthly"), "Monthly Scripts", "Monthly", sos_root))

    # 5. /var/spool/cron/ (User crontabs)
    var_spool_cron_path = os.path.join(sos_root, "var", "spool", "cron")
    all_jobs.extend(parse_user_crontabs(var_spool_cron_path, "User Crontab", sos_root))

    # 6. /sos_commands/cron/ (sosreport command capture output)
    sos_cron_path = os.path.join(sos_root, "sos_commands", "cron")
    all_jobs.extend(parse_user_crontabs(sos_cron_path, "Captured Crontab", sos_root))

    # Output formatting
    headers = ["Source File", "Category", "User", "Schedule", "Command"]

    # Redirect output if file path specified
    original_stdout = sys.stdout
    out_file = None
    if args.output:
        try:
            out_file = open(args.output, "w", encoding="utf-8")
            sys.stdout = out_file
        except IOError as e:
            print(f"Error opening output file '{args.output}': {e}", file=sys.stderr)
            sys.exit(1)

    try:
        if args.format == "json":
            print(json.dumps(all_jobs, indent=2))
        elif args.format == "csv":
            writer = csv.DictWriter(sys.stdout, fieldnames=headers)
            writer.writeheader()
            for row in all_jobs:
                writer.writerow({h: row.get(h, "") for h in headers})
        else:
            print_table(headers, all_jobs)
    finally:
        if out_file:
            sys.stdout = original_stdout
            out_file.close()
            print(f"Successfully wrote {len(all_jobs)} cron jobs to '{args.output}'")

if __name__ == "__main__":
    main()

