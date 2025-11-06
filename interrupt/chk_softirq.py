#!/usr/bin/env python3
"""
chk_softirq.py — Analyze softirq statistics from a live system or sosreport.

Usage:
    ./chk_softirq.py [--path SOSROOT] [-v] [-d] [-s]

Options:
    --path SOSROOT   Path to sosreport root (default: current directory)
    -v, --verbose    Show detailed per-CPU softirq counts
    -s, --sum        Show total softirq summary (default)
    -d, --debug      Show debug information
"""

import argparse
import sys
import os
import shutil

def colorize(text, color=None, bold=False):
    if not sys.stdout.isatty():
        return text
    colors = {
        "cyan": "\033[96m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "reset": "\033[0m",
    }
    style = ""
    if color in colors:
        style += colors[color]
    if bold:
        style += "\033[1m"
    return f"{style}{text}{colors['reset']}"

# ----------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------

def parse_softirqs(root_path):
    """Read per-CPU softirq stats from proc/softirqs"""
    path = os.path.join(root_path, "proc", "softirqs")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found")

    data = {}
    with open(path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    cpus = lines[0].split()
    for line in lines[1:]:
        parts = line.replace(":", "").split()
        name = parts[0]
        values = list(map(int, parts[1:]))
        data[name] = values
    return cpus, data, lines, path


def parse_stat_softirq(root_path):
    """Read summarized softirq stats from proc/stat"""
    path = os.path.join(root_path, "proc", "stat")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found")

    with open(path, "r") as f:
        for line in f:
            if line.startswith("softirq "):
                parts = line.split()
                total = int(parts[1])
                # Map according to kernel order
                fields = [
                    "HI", "TIMER", "NET_TX", "NET_RX",
                    "BLOCK", "IRQ_POLL", "TASKLET",
                    "SCHED", "HRTIMER", "RCU"
                ]
                values = [int(x) for x in parts[2:]]
                return fields, values, total, path
    # If not found, return None to signal fallback
    return None, None, None, path

# ----------------------------------------------------------------------
# Display helpers
# ----------------------------------------------------------------------

def print_sum_from_stat(fields, values, total, path):
    title = colorize(f"SoftIRQ Summary ({path})", "cyan", True)
    print(f"\n{title}")
    print("=" * len(title))
    print(f"{'SoftIRQ':<12} {'Total Count':>18}")
    print("-" * 32)

    for name, val in zip(fields, values):
        print(f"{name:<12} {val:>18,}")

    print("-" * 32)
    print(f"{colorize('Total', 'green', True):<12} {colorize(f'{total:,}', 'green', True):>18}\n")


def print_sum_from_softirqs(data, path):
    title = colorize(f"SoftIRQ Summary ({path}) [fallback]", "cyan", True)
    print(f"\n{title}")
    print("=" * len(title))
    print(f"{'SoftIRQ':<12} {'Total Count':>18}")
    print("-" * 32)

    total = 0
    for irq, values in data.items():
        s = sum(values)
        total += s
        print(f"{irq:<12} {s:>18,}")

    print("-" * 32)
    print(f"{colorize('Total', 'green', True):<12} {colorize(f'{total:,}', 'green', True):>18}\n")


def print_verbose(cpus, data, path):
    title = colorize(f"SoftIRQ Detailed View (per CPU) — {path}", "cyan", True)
    print(f"\n{title}")
    print("=" * len(title))
    header = f"{'SoftIRQ':<10} " + " ".join(f"{cpu:>12}" for cpu in cpus)
    print(header)
    print("-" * len(header))

    for irq, values in data.items():
        print(f"{irq:<10} " + " ".join(f"{v:12,d}" for v in values))

    print("-" * len(header))
    total = sum(sum(v) for v in data.values())
    print(f"{colorize('Total', 'green', True):<10} {colorize(f'{total:,}', 'green', True):>12}\n")

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Parse and summarize softirq statistics")
    parser.add_argument("--path", default=".", help="Path to sosreport root (default: current directory)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed per-CPU counts")
    parser.add_argument("-s", "--sum", action="store_true", help="Show only summed softirq counts (default)")
    parser.add_argument("-d", "--debug", action="store_true", help="Show debug output")
    args = parser.parse_args()

    root = args.path

    if args.verbose:
        # Always use /proc/softirqs for detailed per-CPU view
        cpus, data, raw, path = parse_softirqs(root)
        if args.debug:
            print(colorize("DEBUG: Reading per-CPU softirqs from:", "yellow"), path)
            print("\n".join(raw), "\n")
        print_verbose(cpus, data, path)
    else:
        # Default or -s: use /proc/stat if available, else fall back
        fields, values, total, stat_path = parse_stat_softirq(root)
        if fields is not None:
            if args.debug:
                print(colorize("DEBUG: Using summarized data from:", "yellow"), stat_path)
            print_sum_from_stat(fields, values, total, stat_path)
        else:
            if args.debug:
                print(colorize("DEBUG: softirq line not found in /proc/stat, falling back to /proc/softirqs", "yellow"))
            cpus, data, raw, soft_path = parse_softirqs(root)
            print_sum_from_softirqs(data, soft_path)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

