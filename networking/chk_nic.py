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
IP_ADDR_DETAIL_PATH = "sos_commands/networking/ip_-d_address"

unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}

def scale_value(kb, unit):
    if unit == "K": return kb
    if unit == "M": return kb / 1024
    if unit == "G": return kb / (1024 * 1024)

def _build_mtu_cache():
    mtu_info = {}
    if not os.path.exists(IP_ADDR_DETAIL_PATH):
        return mtu_info

    current_iface = None
    mtu = maxmtu = None

    with open(IP_ADDR_DETAIL_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Detect new interface block
            iface_match = re.match(r"\d+:\s+([^:@\s]+)", line)
            if iface_match:
                # Commit previous interface data
                if current_iface and (mtu or maxmtu):
                    mtu_info[current_iface] = {}
                    if mtu:
                        mtu_info[current_iface]["mtu"] = mtu
                    if maxmtu:
                        mtu_info[current_iface]["maxmtu"] = maxmtu
                # Reset for new block
                current_iface = iface_match.group(1)
                mtu = maxmtu = None
                continue

            if "mtu" in line:
                tokens = line.split()
                try:
                    if "mtu" in tokens:
                        mtu_idx = tokens.index("mtu")
                        mtu = int(tokens[mtu_idx + 1])
                    if "maxmtu" in tokens:
                        maxmtu_idx = tokens.index("maxmtu")
                        maxmtu = int(tokens[maxmtu_idx + 1])
                except (ValueError, IndexError):
                    continue

    # Commit the last interface
    if current_iface and (mtu or maxmtu):
        mtu_info[current_iface] = {}
        if mtu:
            mtu_info[current_iface]["mtu"] = mtu
        if maxmtu:
            mtu_info[current_iface]["maxmtu"] = maxmtu

    return mtu_info

def get_mtu(interface, verbose=False):
    cache = getattr(get_mtu, "_cache", None)
    if cache is None:
        cache = get_mtu._cache = _build_mtu_cache()

    mtu = cache.get(interface, {}).get("mtu")
    if mtu:
        return mtu

    if verbose:
        print(f"[Info] MTU for {interface} not found in {IP_ADDR_DETAIL_PATH}, falling back to 1500")
    return 1500

def get_max_mtu(interface, verbose=False):
    cache = getattr(get_mtu, "_cache", None)
    if cache is None:
        cache = get_mtu._cache = _build_mtu_cache()

    maxmtu = cache.get(interface, {}).get("maxmtu")
    if maxmtu:
        return maxmtu

    # Track missing warnings only once per interface
    if verbose:
        if not hasattr(get_max_mtu, "_warned"):
            get_max_mtu._warned = set()
        if interface not in get_max_mtu._warned:
            print(f"[Warning] Max MTU not found for {interface} in {IP_ADDR_DETAIL_PATH}, assuming 9000")
            get_max_mtu._warned.add(interface)

    return 9000

def get_interrupt_count(interface):
    try:
        with open(PROC_INTERRUPTS, 'r') as f:
            lines = f.readlines()
        matches = [line for line in lines if re.search(rf'\b{re.escape(interface)}\b', line)]
        return len(matches)
    except Exception:
        return 0

def print_nic_memory_table(nic_data, verbose=False, unit="M"):
    unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}
    label = unit_label.get(unit.upper(), "MiB")

    header_fmt = "{:<15} {:>5} {:>7} {:>7} {:>7} {:>14} {:>10}"
    row_fmt =    "{:<15} {:>5} {:>7} {:>7} {:>7} {:>14,} {:>10.2f}"

    print(header_fmt.format("Interface", "MTU", "Queues", "RX", "TX", "BufSize(KiB)", label))
    print("-" * 80)

    total_kb = 0
    verbose_lines = []

    for iface, mtu, queues, rx, tx, buffer_size in nic_data:
        buffer_count = (rx + tx) * queues
        iface_kb = buffer_count * buffer_size
        total_kb += iface_kb
        converted = scale_value(iface_kb, unit)

        print(row_fmt.format(iface, mtu, queues, rx, tx, buffer_size, converted))

        if verbose:
            formula = (f"{iface}: ({rx} + {tx}) * {queues} * {buffer_size} KiB = "
                       f"{buffer_count:,} * {buffer_size} = {iface_kb:,} KiB "
                       f"({converted:.2f} {label})")
            verbose_lines.append(formula)

    total_converted = scale_value(total_kb, unit)
    print("-" * 80)
    print(f"{'Total':<61}{total_converted:>10.2f} {label}")

    if verbose and verbose_lines:
        print("\nVerbose calculations:")
        for line in verbose_lines:
            print(line)

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
    nic_data = []
    for iface, (rx, rx_jumbo, tx) in nic_info.items():
        mtu = get_mtu(iface, verbose)
        queues = get_interrupt_count(iface)
        if queues == 0:
            continue

        buffer_size = JUMBO_BUFFER_SIZE if mtu > 1500 else STANDARD_BUFFER_SIZE
        active_rx = rx_jumbo if mtu > 1500 else rx

        nic_data.append((iface, mtu, queues, active_rx, tx, buffer_size))

    print_nic_memory_table(nic_data, verbose, unit)

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
    # No need to pre-build the map — we now use get_max_mtu() inline
    nic_data = []
    for iface, (rx, rx_jumbo, tx) in nic_info.items():
        mtu = get_max_mtu(iface, verbose)
        queues = get_interrupt_count(iface)
        if queues == 0:
            continue

        buffer_size = JUMBO_BUFFER_SIZE if mtu > 1500 else STANDARD_BUFFER_SIZE
        active_rx = rx_jumbo if mtu > 1500 and rx_jumbo > 0 else rx

        nic_data.append((iface, mtu, queues, active_rx, tx, buffer_size))

    print_nic_memory_table(nic_data, verbose, unit)

def print_debug_info(ethtool_files, interfaces):
    print("\n[Debug] Files referenced by the script:\n")

    print(f"  [*] {PROC_INTERRUPTS}")
    print(f"  [*] {IP_ADDR_DETAIL_PATH}")

    for f in ethtool_files:
        print(f"  [*] {f}")

    for iface in interfaces:
        nmcli_file = f"sos_commands/networkmanager/nmcli_dev_show_{iface}"
        if os.path.exists(nmcli_file):
            print(f"  [*] {nmcli_file}")
        else:
            print(f"  [!] {nmcli_file} (missing)")

def main():
    parser = argparse.ArgumentParser(
        description="Estimate NIC ring buffer memory usage from sosreport data"
    )
    parser.add_argument("-x", "--max", action="store_true",
        help="Estimate maximum NIC buffer usage assuming full ring capacity")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed memory calculation formulas")
    parser.add_argument("-f", "--filter", type=str, default="",
        help="Filter interfaces by name substring (e.g., ens5, bond, eno)")
    parser.add_argument("-d", "--debug", action="store_true",
        help="Print debug info about files used by the script")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-K", action="store_const", const="K", dest="unit", help="Display memory in KiB")
    group.add_argument("-M", action="store_const", const="M", dest="unit", help="Display memory in MiB (default)")
    group.add_argument("-G", action="store_const", const="G", dest="unit", help="Display memory in GiB")
    parser.set_defaults(unit="M")

    args = parser.parse_args()
    filter_pattern = args.filter

    ethtool_files = glob.glob(SOS_ETHTOOL_PATH)
    if not ethtool_files:
        print(f"No {SOS_ETHTOOL_PATH} files found")
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

        if args.debug:
            print_debug_info(ethtool_files, nic_info.keys())

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

        if args.debug:
            print_debug_info(ethtool_files, nic_info.keys())

        calculate_total_memory(nic_info, verbose=args.verbose, unit=args.unit)

if __name__ == "__main__":
    main()

