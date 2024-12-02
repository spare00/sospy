#!/usr/bin/env python3

import argparse
from collections import defaultdict

def calculate_usage_per_user(filename, sort_by):
    """Calculate and display CPU usage grouped by user."""
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        return

    user_usage = defaultdict(lambda: {'usr': 0.0, 'system': 0.0, 'wait': 0.0, 'cpu': 0.0, 'count': 0})
    total_usr, total_system, total_wait, total_cpu, total_count = 0.0, 0.0, 0.0, 0.0, 0

    for line in lines:
        if "USER" in line or "Time" in line:
            continue
        columns = line.split()
        if len(columns) < 8:
            continue
        try:
            user = columns[1]
            usr = float(columns[3].strip('%'))
            system = float(columns[4].strip('%'))
            wait = float(columns[6].strip('%'))
            cpu = float(columns[7].strip('%'))

            user_usage[user]['usr'] += usr
            user_usage[user]['system'] += system
            user_usage[user]['wait'] += wait
            user_usage[user]['cpu'] += cpu
            user_usage[user]['count'] += 1

            total_usr += usr
            total_system += system
            total_wait += wait
            total_cpu += cpu
            total_count += 1
        except ValueError:
            continue

    # Sort users by the selected field
    sorted_users = sorted(user_usage.items(), key=lambda x: x[1][sort_by], reverse=True)
    print(f"{'%usr':<10} {'%system':<10} {'%wait':<10} {'%CPU':<10} {'Count':<8} {'User':<10}")
    print("-" * 60)
    for user, usage in sorted_users:
        print(f"{usage['usr']:<10.2f} {usage['system']:<10.2f} {usage['wait']:<10.2f} {usage['cpu']:<10.2f} {usage['count']:<8} {user:<10}")
    print("-" * 60)
    print(f"{total_usr:<10.2f} {total_system:<10.2f} {total_wait:<10.2f} {total_cpu:<10.2f} {total_count:<8} {'Total':<10}")

def calculate_usage_per_command(filename, sort_by):
    """Calculate and display CPU usage grouped by command."""
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        return

    command_usage = defaultdict(lambda: {'usr': 0.0, 'system': 0.0, 'wait': 0.0, 'cpu': 0.0, 'count': 0})
    total_usr, total_system, total_wait, total_cpu, total_count = 0.0, 0.0, 0.0, 0.0, 0

    for line in lines:
        if "USER" in line or "Time" in line:
            continue
        columns = line.split()
        if len(columns) < 8:
            continue
        try:
            command = columns[-1]
            usr = float(columns[3].strip('%'))
            system = float(columns[4].strip('%'))
            wait = float(columns[6].strip('%'))
            cpu = float(columns[7].strip('%'))

            command_usage[command]['usr'] += usr
            command_usage[command]['system'] += system
            command_usage[command]['wait'] += wait
            command_usage[command]['cpu'] += cpu
            command_usage[command]['count'] += 1

            total_usr += usr
            total_system += system
            total_wait += wait
            total_cpu += cpu
            total_count += 1
        except ValueError:
            continue

    # Sort commands by the selected field
    sorted_commands = sorted(command_usage.items(), key=lambda x: x[1][sort_by], reverse=True)
    sorted_commands = sorted_commands[:10]  # Limit to top 10 commands

    print(f"{'%usr':<10} {'%system':<10} {'%wait':<10} {'%CPU':<10} {'Count':<8} {'Command':<20}")
    print("-" * 70)
    for command, usage in sorted_commands:
        print(f"{usage['usr']:<10.2f} {usage['system']:<10.2f} {usage['wait']:<10.2f} {usage['cpu']:<10.2f} {usage['count']:<8} {command:<20}")
    print("-" * 70)
    print(f"{total_usr:<10.2f} {total_system:<10.2f} {total_wait:<10.2f} {total_cpu:<10.2f} {total_count:<8} {'Total':<20}")

def calculate_cpu_utilization(filename):
    """Calculate and display total CPU utilization."""
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        return

    total_usr = 0.0
    total_system = 0.0
    total_wait = 0.0
    total_cpu = 0.0
    count = 0

    for line in lines:
        if "USER" in line or "Time" in line:
            continue
        columns = line.split()
        if len(columns) < 8:
            continue
        try:
            usr = float(columns[3].strip('%'))
            system = float(columns[4].strip('%'))
            wait = float(columns[6].strip('%'))
            cpu = float(columns[7].strip('%'))

            total_usr += usr
            total_system += system
            total_wait += wait
            total_cpu += cpu
            count += 1
        except ValueError:
            continue

    if count > 0:
        print(f"Total %usr: {total_usr:.2f}%")
        print(f"Total %system: {total_system:.2f}%")
        print(f"Total %wait: {total_wait:.2f}%")
        print(f"Total %CPU: {total_cpu:.2f}%")
    else:
        print("No valid data found in the file.")

def main():
    parser = argparse.ArgumentParser(
        description="Analyze CPU utilization data from a file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "filename",
        nargs="?",
        default="sos_commands/process/pidstat_-p_ALL_-rudvwsRU_--human_-h",
        help="Path to the input file containing CPU utilization data."
    )
    parser.add_argument(
        "-u", "--user",
        action="store_true",
        help="Show CPU usage per user, grouped and summed by %%usr, %%system, %%wait, and %%CPU, sorted by total %%CPU."
    )
    parser.add_argument(
        "-c", "--command",
        action="store_true",
        help="Show CPU usage per command, grouped and summed by %%usr, %%system, %%wait, and %%CPU, sorted by total %%CPU. Displays only the top 10 commands."
    )
    parser.add_argument(
        "--sort",
        choices=["usr", "system", "wait", "cpu", "count"],
        default="cpu",
        help="Field to sort by. Defaults to 'cpu'."
    )
    args = parser.parse_args()

    if args.user:
        calculate_usage_per_user(args.filename, args.sort)
    elif args.command:
        calculate_usage_per_command(args.filename, args.sort)
    else:
        calculate_cpu_utilization(args.filename)

if __name__ == "__main__":
    main()
