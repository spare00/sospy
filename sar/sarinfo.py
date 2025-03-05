#!/usr/bin/env python3

import sys
import os
import re
import subprocess
import argparse
from datetime import datetime

# Paths and variables
SAR_DIR_DEFAULT = "/var/log/sa"
SAR_DIR_T_OPTION = "sos_commands/sar"
DATE_FILE = "./date"
DEFAULT_LINES = 5

# Function to fetch date from './date' file
def get_sar_date():
    try:
        with open(DATE_FILE, "r") as f:
            date_str = f.readline().strip()

        # Split by spaces and extract the third item (day)
        parts = date_str.split()
        if len(parts) < 3:
            raise ValueError(f"Unexpected date format: {date_str}")

        day = parts[2].zfill(2)  # Ensure two digits (e.g., "1" -> "01")

        return day
    except Exception as e:
        print(f"Error: Unable to read or parse {DATE_FILE}: {e}")
        sys.exit(1)

# Function to determine SAR file path
def get_sar_file(use_today):
    sar_date = get_sar_date()
    sar_dir = SAR_DIR_T_OPTION if use_today else SAR_DIR_DEFAULT
    suffix = "sar" if use_today else "sa"
    return f"{sar_dir}/{suffix}{sar_date}"

# Function to extract CPU metrics
def extract_cpu_metrics(file_path, tail_lines=DEFAULT_LINES):
    try:
        result = subprocess.run(
            f"grep -E 'usr| all' {file_path} | awk '{{print}} /^Average:/ {{exit}}'",
            shell=True, check=True, capture_output=True, text=True
        )
        cpu_output = result.stdout.splitlines()[-tail_lines:]
        return "\n".join(cpu_output)
    except subprocess.CalledProcessError:
        return f"Error: Could not process CPU metrics from {file_path}."

# Function to extract tail metrics
def extract_tail_metrics(file_path, tail_lines=DEFAULT_LINES):
    try:
        result = subprocess.run(
            f"grep --no-group-separator -E 'proc|swp|vmeff|mem|runq|hug|file|Ave' {file_path} -B{tail_lines} | "
            f"grep -E 'proc|swp|vmeff|mem|runq|hug|file' -A{tail_lines}",
            shell=True, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return f"Error: Could not process tail metrics from {file_path}."

# Main function
def main():
    # Argument parser setup
    parser = argparse.ArgumentParser(
        description="Analyze SAR logs for system performance metrics."
    )
    parser.add_argument(
        "-t", "--today",
        action="store_true",
        help="Use 'sos_commands/sar/sarDD' based on the date the sosreport was collected, instead of '/var/log/sa/saDD'."
    )
    parser.add_argument(
        "-n", "--num-lines",
        type=int,
        default=DEFAULT_LINES,
        help=f"Number of lines to display (default: {DEFAULT_LINES})."
    )
    
    args = parser.parse_args()

    # Determine SAR file path
    sar_file = get_sar_file(args.today)
    if not os.path.exists(sar_file):
        print(f"Error: SAR file for derived date ({sar_file}) does not exist.")
        return

    # Display metrics
    print("CPU Metrics:")
    print(extract_cpu_metrics(sar_file, args.num_lines))

    print("\nTail Metrics:")
    print(extract_tail_metrics(sar_file, args.num_lines))

# Execute script
if __name__ == "__main__":
    main()
