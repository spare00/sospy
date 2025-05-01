#!/usr/bin/env python3

import os
import re
import subprocess
import glob
import argparse

STANDARD_BUFFER_SIZE = 2048
JUMBO_BUFFER_SIZE = 16384
SOS_ETHTOOL_PATH = "sos_commands/networking/ethtool_-g_*"
SYS_CLASS_NET = "sys/class/net"
PROC_INTERRUPTS = "proc/interrupts"

def get_mtu(interface):
    try:
        nmcli_path = f"sos_commands/networkmanager/nmcli_dev_show_{interface}"
        with open(nmcli_path) as f:
            for line in f:
                if "GENERAL.MTU:" in line:
                    return int(line.strip().split()[-1])
    except Exception:
        pass
    print("fallback to 1500")
    return 1500  # fallback default

def get_interrupt_count(interface):
    try:
        with open(PROC_INTERRUPTS, 'r') as f:
            lines = f.readlines()
        matches = [line for line in lines if re.search(rf'\b{interface}', line)]
        return len(matches)
    except Exception:
        return 0

def parse_ethtool_file(filepath):
    iface = os.path.basename(filepath).replace("ethtool_-g_", "")
    rx, rx_jumbo, tx = 0, 0, 0
    with open(filepath, 'r') as f:
        for line in f:
            if "RX:" in line and "Mini" not in line:
                rx = int(line.strip().split()[1])
            elif "RX Jumbo" in line:
                tokens = line.strip().split()
                if len(tokens) > 2 and tokens[2].isdigit():
                    rx_jumbo = int(tokens[2])
            elif "TX:" in line:
                tx = int(line.strip().split()[1])
    return iface, rx, rx_jumbo, tx

def calculate_total_memory(nic_info, verbose=False):
    total_bytes = 0
    verbose_lines = []

    print(f"{'Interface':<15} {'MTU':<6} {'Queues':<7} {'RX':<5} {'TX':<5} {'BufSize':<8} {'MiB':>8}")
    print("-" * 64)

    for iface, (rx, rx_jumbo, tx) in nic_info.items():
        mtu = get_mtu(iface)
        queues = get_interrupt_count(iface)
        if queues == 0:
            continue

        if mtu > 1500:
            active_rx = rx_jumbo
            buffer_size = JUMBO_BUFFER_SIZE
        else:
            active_rx = rx
            buffer_size = STANDARD_BUFFER_SIZE

        buffer_count = (active_rx + tx) * queues
        iface_bytes = buffer_count * buffer_size
        total_bytes += iface_bytes

        mib = iface_bytes / (1024 ** 2)
        print(f"{iface:<15} {mtu:<6} {queues:<7} {active_rx:<5} {tx:<5} {buffer_size:<8} {mib:>8.2f}")

        if verbose:
            formula = (f"{iface}: ({active_rx} + {tx}) * {queues} * {buffer_size} = "
                       f"{buffer_count} * {buffer_size} = {iface_bytes} bytes "
                       f"({mib:.2f} MiB)")
            verbose_lines.append(formula)

    total_mib = total_bytes / (1024 ** 2)
    print("-" * 64)
    print(f"{'Total':<53} {total_mib:>8.2f} MiB")

    if verbose and verbose_lines:
        print("\nVerbose calculations:")
        for line in verbose_lines:
            print(line)

def main():
    parser = argparse.ArgumentParser(
        description="Estimate NIC ring buffer memory usage from sosreport data"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed memory calculation formulas"
    )
    parser.add_argument(
        "-f", "--filter",
        help="Filter interfaces by name substring (e.g., ens5, bond, eno)",
        type=str,
        default=""
    )
    args = parser.parse_args()
    filter_pattern = args.filter

    ethtool_files = glob.glob(SOS_ETHTOOL_PATH)
    if not ethtool_files:
        print("No ethtool_-g_* files found under sos_commands/networking/")
        exit(1)

    nic_info = {}
    for f in ethtool_files:
        iface, rx, rx_jumbo, tx = parse_ethtool_file(f)
        if filter_pattern and filter_pattern not in iface:
            continue
        nic_info[iface] = (rx, rx_jumbo, tx)

    if not nic_info:
        print(f"No matching interfaces for filter: '{filter_pattern}'")
        exit(1)

    calculate_total_memory(nic_info, verbose=args.verbose)

if __name__ == "__main__":
    main()

