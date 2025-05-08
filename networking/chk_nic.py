#!/usr/bin/env python3

import os
import re
import subprocess
import glob
import argparse

STANDARD_BUFFER_SIZE = 2  #kB
JUMBO_BUFFER_SIZE = 16    #kB
SOS_ETHTOOL_PATH = "sos_commands/networking/ethtool_-g_*"
SYS_CLASS_NET = "sys/class/net"
PROC_INTERRUPTS = "proc/interrupts"

unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}

def scale_value(kb, unit):
    if unit == "K": return kb
    if unit == "M": return kb / 1024
    if unit == "G": return kb / (1024 * 1024)

def get_mtu(interface, verbose=False):
    try:
        nmcli_path = f"sos_commands/networkmanager/nmcli_dev_show_{interface}"
        with open(nmcli_path) as f:
            for line in f:
                if "GENERAL.MTU:" in line:
                    return int(line.strip().split()[-1])
    except Exception:
        pass
    if verbose:
        print(f"MTU file not found for {interface}, falling back to 1500")

    return 1500  # fallback default

def get_interrupt_count(interface):
    try:
        with open(PROC_INTERRUPTS, 'r') as f:
            lines = f.readlines()
        matches = [line for line in lines if re.search(rf'\b{re.escape(interface)}\b', line)]
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

def calculate_total_memory(nic_info, verbose=False, unit="M"):
    label = unit_label.get(unit.upper(), "MiB")

    total_kb = 0
    verbose_lines = []

    header_fmt = "{:<15} {:>7} {:>7} {:>7} {:>5} {:>14} {:>10}"
    row_fmt =    "{:<15} {:>7} {:>7} {:>7} {:>5} {:>14,} {:>10.2f}"

    print(header_fmt.format("Interface", "RX", "TX", "Queues", "MTU", "BufSize(KiB)", label))
    print("-" * 80)

    for iface, (rx, rx_jumbo, tx) in nic_info.items():
        mtu = get_mtu(iface, verbose)
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
        iface_kb = buffer_count * buffer_size
        total_kb += iface_kb

        converted = scale_value(iface_kb, unit)
        print(row_fmt.format(iface, active_rx, tx, queues, mtu, buffer_size, converted))

        if verbose:
            formula = (f"{iface:<15}: ({active_rx} + {tx}) * {queues} * {buffer_size} = "
                       f"{iface_kb:>8,} KiB "
                       f"({converted:>8.2f} {label})")
            verbose_lines.append(formula)

    total_converted = scale_value(total_kb, unit)
    print("-" * 80)
    print(f"{'Total':<63}{total_converted:>8.2f} {label}")

    if verbose and verbose_lines:
        print("\nVerbose calculations:")
        for line in verbose_lines:
            print(line)

def parse_max_ethtool_file(filepath):
    iface = os.path.basename(filepath).replace("ethtool_-g_", "")
    max_rx, max_rx_jumbo, max_tx = 0, 0, 0
    in_max_section = False

    with open(filepath, 'r') as f:
        for line in f:
            if "Pre-set maximums" in line:
                in_max_section = True
                continue
            elif in_max_section and (line.strip() == "" or "Current hardware settings" in line):
                break
            if in_max_section:
                if "RX:" in line and "Mini" not in line:
                    max_rx = int(line.strip().split()[1])
                elif "RX Jumbo" in line:
                    tokens = line.strip().split()
                    if len(tokens) > 2 and tokens[2].isdigit():
                        max_rx_jumbo = int(tokens[2])
                elif "TX:" in line:
                    max_tx = int(line.strip().split()[1])
    return iface, max_rx, max_rx_jumbo, max_tx

def calculate_max_memory(nic_info, verbose=False, unit="M"):
    label = unit_label.get(unit.upper(), "MiB")

    total_kb = 0
    verbose_lines = []

    header_fmt = "{:<15} {:>7} {:>7} {:>7} {:>5} {:>14} {:>10}"
    row_fmt =    "{:<15} {:>7} {:>7} {:>7} {:>5} {:>14,} {:>10.2f}"

    print(header_fmt.format("Interface", "RX", "TX", "Queues", "MTU", "BufSize(KiB)", label))
    print("-" * 80)

    for iface, (max_rx, max_rx_jumbo, max_tx) in nic_info.items():
        mtu = get_mtu(iface, verbose)
        queues = get_interrupt_count(iface)
        if queues == 0:
            continue

        if mtu > 1500:
            active_rx = max_rx_jumbo
            buffer_size = JUMBO_BUFFER_SIZE
        else:
            active_rx = max_rx
            buffer_size = STANDARD_BUFFER_SIZE

        buffer_count = (active_rx + max_tx) * queues
        iface_kb = buffer_count * buffer_size
        total_kb += iface_kb

        converted = scale_value(iface_kb, unit)
        print(row_fmt.format(iface, active_rx, max_tx, queues, mtu, buffer_size, converted))

        if verbose:
            formula = (f"{iface:<15}: ({active_rx} + {max_tx}) * {queues} * {buffer_size} = "
                       f"{iface_kb:>8,} KiB "
                       f"({converted:>8.2f} {label})")
            verbose_lines.append(formula)

    total_converted = scale_value(total_kb, unit)
    print("-" * 80)
    print(f"{'Total':<63}{total_converted:>8.2f} {label}")

    if verbose and verbose_lines:
        print("\nVerbose calculations:")
        for line in verbose_lines:
            print(line)

def main():
    parser = argparse.ArgumentParser(
        description="Estimate NIC ring buffer memory usage from sosreport data"
    )
    parser.add_argument(
        "-x", "--max",
        action="store_true",
        help="Estimate maximum NIC buffer usage assuming full ring capacity"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed memory calculation formulas")
    parser.add_argument("-f", "--filter", help="Filter interfaces by name substring (e.g., ens5, bond, eno)",
        type=str,
        default=""
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-K", action="store_const", const="K", dest="unit", help="Display memory in KiB")
    group.add_argument("-M", action="store_const", const="M", dest="unit", help="Display memory in MiB (default)")
    group.add_argument("-G", action="store_const", const="G", dest="unit", help="Display memory in GiB")
    parser.set_defaults(unit="M")

    args = parser.parse_args()
    filter_pattern = args.filter

    ethtool_files = glob.glob(SOS_ETHTOOL_PATH)
    if not ethtool_files:
        print("No ethtool_-g_* files found under sos_commands/networking/")
        exit(1)

    if args.max:
        nic_info = {}
        for f in ethtool_files:
            iface, rx, rx_jumbo, tx = parse_max_ethtool_file(f)
            if filter_pattern and filter_pattern not in iface:
                continue
            nic_info[iface] = (rx, rx_jumbo, tx)

        if not nic_info:
            print(f"No matching interfaces for filter: '{filter_pattern}'")
            exit(1)

        calculate_max_memory(nic_info, verbose=args.verbose, unit=args.unit)
    else:
        nic_info = {}
        for f in ethtool_files:
            iface, rx, rx_jumbo, tx = parse_ethtool_file(f)
            if filter_pattern and filter_pattern not in iface:
                continue
            nic_info[iface] = (rx, rx_jumbo, tx)

        if not nic_info:
            print(f"No matching interfaces for filter: '{filter_pattern}'")
            exit(1)

        calculate_total_memory(nic_info, verbose=args.verbose, unit=args.unit)

if __name__ == "__main__":
    main()

