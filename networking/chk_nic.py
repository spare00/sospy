#!/usr/bin/env python3

import os
import re
import glob
import argparse
import sys

STANDARD_BUFFER_SIZE = 2  # KiB per descriptor at MTU 1500 (4K page split)
JUMBO_BUFFER_SIZE = 16    # KiB per descriptor when MTU > 1500 (order-2 page)
SOS_ETHTOOL_G_GLOB = "sos_commands/networking/ethtool_-g_*"
SOS_ETHTOOL_L_DIR = "sos_commands/networking"
SYS_CLASS_NET = "sys/class/net"
PROC_INTERRUPTS = "proc/interrupts"
IP_ADDR_DETAIL_PATH = "sos_commands/networking/ip_-d_address"

# Logical/virtual devices do not allocate hardware DMA rings themselves.
VIRTUAL_PREFIXES = (
    "bond", "br", "virbr", "vlan", "team", "dummy",
    "tun", "tap", "veth", "docker", "cni", "flannel",
    "cali", "ovn", "geneve", "vxlan", "wg",
)

unit_label = {"K": "KiB", "M": "MiB", "G": "GiB"}


def scale_value(kb, unit):
    if unit == "K":
        return kb
    if unit == "M":
        return kb / 1024
    if unit == "G":
        return kb / (1024 * 1024)
    return kb / 1024


def is_virtual_iface(iface):
    name = iface.lower()
    if name == "lo" or name.startswith(VIRTUAL_PREFIXES):
        return True
    # eth0.100 style VLANs
    if re.match(r".+\.\d+$", name):
        return True
    return False


def _build_link_cache(root):
    """Parse ip -d address: mtu, maxmtu, and IFF_UP."""
    link_info = {}
    path = os.path.join(root, IP_ADDR_DETAIL_PATH)
    if not os.path.exists(path):
        return link_info

    current_iface = None
    current_data = {}

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # e.g. "8: eno12419np2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ..."
            iface_match = re.match(r"\d+:\s+([^:@\s]+):.*\bmtu\s+(\d+)", line)
            if iface_match:
                if current_iface and current_data:
                    link_info[current_iface] = current_data

                current_iface = iface_match.group(1)
                current_data = {"mtu": int(iface_match.group(2))}
                flags_match = re.search(r"<([^>]*)>", line)
                if flags_match:
                    flags = flags_match.group(1).split(",")
                    current_data["up"] = "UP" in flags
                continue

            if "maxmtu" in line and current_iface:
                tokens = line.split()
                try:
                    maxmtu_idx = tokens.index("maxmtu")
                    current_data["maxmtu"] = int(tokens[maxmtu_idx + 1])
                except (ValueError, IndexError):
                    continue

    if current_iface and current_data:
        link_info[current_iface] = current_data

    return link_info


def _sysfs_mtu(root, iface):
    path = os.path.join(root, SYS_CLASS_NET, iface, "mtu")
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _sysfs_up(root, iface):
    path = os.path.join(root, SYS_CLASS_NET, iface, "flags")
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            val = int(f.read().strip(), 16)
        return (val & 0x1) != 0
    except (OSError, ValueError):
        return None


def get_mtu(iface, link_info, root, verbose=False):
    mtu = link_info.get(iface, {}).get("mtu")
    if mtu:
        return mtu

    mtu = _sysfs_mtu(root, iface)
    if mtu:
        return mtu

    if verbose:
        print(f"[Info] MTU for {iface} not found, falling back to 1500")
    return 1500


def get_max_mtu(iface, link_info, root, verbose=False):
    maxmtu = link_info.get(iface, {}).get("maxmtu")
    if maxmtu:
        return maxmtu

    if verbose:
        warned = getattr(get_max_mtu, "_warned", set())
        if iface not in warned:
            print(f"[Warning] Max MTU not found for {iface}, assuming 9000")
            warned.add(iface)
            get_max_mtu._warned = warned

    return 9000


def get_is_up(iface, link_info, root):
    if "up" in link_info.get(iface, {}):
        return link_info[iface]["up"]
    return _sysfs_up(root, iface)


def load_interrupts(root):
    path = os.path.join(root, PROC_INTERRUPTS)
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.readlines()
    except OSError:
        return []


def interrupt_count(iface, irq_lines):
    pattern = rf"\b{re.escape(iface)}\b"
    return sum(1 for line in irq_lines if re.search(pattern, line))


def parse_ethtool_l(filepath):
    """Current Combined queue count, or RX queues if Combined is 0/absent."""
    if not os.path.exists(filepath):
        return 0
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # Only the current section — Pre-set maximums would over-count queues.
        section = re.search(
            r"Current hardware settings:.*", content, re.DOTALL | re.IGNORECASE
        )
        if not section:
            return 0
        text = section.group(0)
        combined = re.search(r"Combined:\s+(\d+)", text, re.IGNORECASE)
        rxq = re.search(r"^RX:\s+(\d+)", text, re.MULTILINE | re.IGNORECASE)
        c = int(combined.group(1)) if combined else 0
        r = int(rxq.group(1)) if rxq else 0
        return c if c > 0 else r
    except (OSError, ValueError):
        return 0


def get_queue_count(root, iface, irq_lines):
    l_path = os.path.join(root, SOS_ETHTOOL_L_DIR, f"ethtool_-l_{iface}")
    queues = parse_ethtool_l(l_path)
    if queues > 0:
        return queues, "ethtool -l"
    irq_q = interrupt_count(iface, irq_lines)
    if irq_q > 0:
        return irq_q, "interrupts"
    return 0, None


def _parse_ring_line(stripped, rx, rx_jumbo, tx):
    """Update ring counts from one ethtool -g body line."""
    try:
        if stripped.startswith("RX:") and "Mini" not in stripped:
            return int(stripped.split()[1]), rx_jumbo, tx
        if "RX Jumbo" in stripped:
            tokens = stripped.split()
            if len(tokens) > 2 and tokens[2].isdigit():
                return rx, int(tokens[2]), tx
        if stripped.startswith("TX:"):
            return rx, rx_jumbo, int(stripped.split()[1])
    except (ValueError, IndexError):
        pass
    return rx, rx_jumbo, tx


def parse_ethtool_g(filepath, use_max=False):
    """Parse ethtool -g current (default) or Pre-set maximums (use_max)."""
    iface = os.path.basename(filepath).replace("ethtool_-g_", "")
    rx, rx_jumbo, tx = 0, 0, 0
    saw_section_header = False
    in_section = False

    try:
        with open(filepath, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                stripped = line.strip()
                if "Pre-set maximums" in stripped:
                    saw_section_header = True
                    in_section = use_max
                    continue
                if "Current hardware settings" in stripped:
                    saw_section_header = True
                    in_section = not use_max
                    continue
                if saw_section_header and in_section and not stripped:
                    break
                if in_section or not saw_section_header:
                    rx, rx_jumbo, tx = _parse_ring_line(stripped, rx, rx_jumbo, tx)
    except OSError:
        return iface, 0, 0, 0

    return iface, rx, rx_jumbo, tx


def print_nic_memory_table(nic_data, verbose=False, unit="M", include_tx=False):
    label = unit_label.get(unit.upper(), "MiB")
    mode = "RX+TX" if include_tx else "RX only"

    header_fmt = "{:<15} {:<8} {:>5} {:>7} {:>7} {:>7} {:>14} {:>10}"
    row_fmt = "{:<15} {:<8} {:>5} {:>7} {:>7} {:>7} {:>14,} {:>10.2f}"
    # pad total label to the start of the memory column
    total_pad = 15 + 1 + 8 + 1 + 5 + 1 + 7 + 1 + 7 + 1 + 7 + 1 + 14

    print(header_fmt.format(
        "Interface", "Status", "MTU", "Queues", "RX", "TX", "BufSize(KiB)", label
    ))
    print("-" * (total_pad + 1 + 10))

    total_kb = 0
    up_kb = 0
    verbose_lines = []

    for iface, status, mtu, queues, rx, tx, buffer_size, qsrc in nic_data:
        desc_count = (rx + tx) if include_tx else rx
        buffer_count = desc_count * queues
        iface_kb = buffer_count * buffer_size
        total_kb += iface_kb
        if status == "UP":
            up_kb += iface_kb
        converted = scale_value(iface_kb, unit)

        print(row_fmt.format(iface, status, mtu, queues, rx, tx, buffer_size, converted))

        if verbose:
            if include_tx:
                left = f"({rx} + {tx}) * {queues}"
            else:
                left = f"{rx} * {queues}"
            src_note = f" [queues from {qsrc}]" if qsrc else ""
            formula = (
                f"{iface}: {left} * {buffer_size} KiB = "
                f"{buffer_count:,} * {buffer_size} = {iface_kb:,} KiB "
                f"({converted:.2f} {label}){src_note}"
            )
            verbose_lines.append(formula)

    total_converted = scale_value(total_kb, unit)
    print("-" * (total_pad + 1 + 10))
    print(f"{'Total (' + mode + ')':<{total_pad}}{total_converted:>10.2f} {label}")

    has_up = any(status == "UP" for _, status, *_ in nic_data)
    has_down = any(status == "DOWN" for _, status, *_ in nic_data)
    if has_up and has_down:
        up_converted = scale_value(up_kb, unit)
        print(f"{'  UP interfaces only':<{total_pad}}{up_converted:>10.2f} {label}")

    if not include_tx:
        print(f"\nTX rings excluded; pass --tx to include them in the estimate.")

    if verbose and verbose_lines:
        print("\nVerbose calculations:")
        for line in verbose_lines:
            print(line)


def build_nic_data(nic_info, root, verbose=False, use_max=False, include_virtual=False):
    link_info = _build_link_cache(root)
    irq_lines = load_interrupts(root)
    nic_data = []

    for iface, (rx, rx_jumbo, tx) in nic_info.items():
        if is_virtual_iface(iface) and not include_virtual:
            continue

        mtu = get_max_mtu(iface, link_info, root, verbose) if use_max \
            else get_mtu(iface, link_info, root, verbose)
        queues, qsrc = get_queue_count(root, iface, irq_lines)
        if queues == 0:
            continue

        is_up = get_is_up(iface, link_info, root)
        if is_up is True:
            status = "UP"
        elif is_up is False:
            status = "DOWN"
        else:
            status = "?"

        if is_virtual_iface(iface):
            status = f"{status}/virt"

        buffer_size = JUMBO_BUFFER_SIZE if mtu > 1500 else STANDARD_BUFFER_SIZE
        active_rx = rx_jumbo if mtu > 1500 and rx_jumbo > 0 else rx

        nic_data.append((iface, status, mtu, queues, active_rx, tx, buffer_size, qsrc))

    return nic_data


def print_debug_info(root, ethtool_files, interfaces, irq_lines):
    print("\n[Debug] Files referenced by the script:\n")

    print(f"  [*] {os.path.join(root, PROC_INTERRUPTS)}")
    print(f"  [*] {os.path.join(root, IP_ADDR_DETAIL_PATH)}")

    for f in ethtool_files:
        print(f"  [*] {f}")

    for iface in interfaces:
        l_path = os.path.join(root, SOS_ETHTOOL_L_DIR, f"ethtool_-l_{iface}")
        if os.path.exists(l_path):
            print(f"  [*] {l_path}")
        else:
            print(f"  [!] {l_path} (missing; queues fall back to interrupts)")

        nmcli_file = os.path.join(root, f"sos_commands/networkmanager/nmcli_dev_show_{iface}")
        if os.path.exists(nmcli_file):
            print(f"  [*] {nmcli_file}")

        if irq_lines is not None:
            nirq = interrupt_count(iface, irq_lines)
            print(f"      interrupts matching {iface}: {nirq}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Estimate NIC ring-buffer DMA memory from sosreport data. "
            "Default is RX only (the usual unaccounted-memory path); "
            "TX payload is typically already in process RSS or slab."
        )
    )
    parser.add_argument("--path", default=".",
        help="Path to sosreport root or live / (default: current directory)")
    parser.add_argument("-x", "--max", action="store_true",
        help="Estimate maximum NIC buffer usage assuming full ring capacity")
    parser.add_argument("-v", "--verbose", action="store_true",
        help="Show detailed memory calculation formulas")
    parser.add_argument("-f", "--filter", type=str, default="",
        help="Filter interfaces by name substring (e.g., ens5, bond, eno)")
    parser.add_argument("-d", "--debug", action="store_true",
        help="Print debug info about files used by the script")
    parser.add_argument("--virtual", action="store_true",
        help="Include virtual devices (bond, bridge, vlan, veth, ...)")

    tx_group = parser.add_mutually_exclusive_group()
    tx_group.add_argument("--tx", dest="include_tx", action="store_true",
        help="Include TX rings in the estimate (RX+TX) * queues * bufsize")
    tx_group.add_argument("--no-tx", dest="include_tx", action="store_false",
        help="Count RX rings only (default)")
    parser.set_defaults(include_tx=False)

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-K", action="store_const", const="K", dest="unit", help="Display memory in KiB")
    group.add_argument("-M", action="store_const", const="M", dest="unit", help="Display memory in MiB (default)")
    group.add_argument("-G", action="store_const", const="G", dest="unit", help="Display memory in GiB")
    parser.set_defaults(unit="M")

    args = parser.parse_args()
    root = os.path.abspath(args.path)
    filter_pattern = args.filter

    ethtool_files = sorted(glob.glob(os.path.join(root, SOS_ETHTOOL_G_GLOB)))
    if not ethtool_files:
        print(f"No {os.path.join(root, SOS_ETHTOOL_G_GLOB)} files found")
        sys.exit(1)

    nic_info = {}
    for f in ethtool_files:
        iface, rx, rx_jumbo, tx = parse_ethtool_g(f, use_max=args.max)
        if filter_pattern and filter_pattern not in iface:
            continue
        nic_info[iface] = (rx, rx_jumbo, tx)

    if not nic_info:
        print(f"No matching interfaces for filter: '{filter_pattern}'")
        sys.exit(1)

    if args.debug:
        irq_lines = load_interrupts(root)
        print_debug_info(root, ethtool_files, nic_info.keys(), irq_lines)

    nic_data = build_nic_data(
        nic_info, root,
        verbose=args.verbose,
        use_max=args.max,
        include_virtual=args.virtual,
    )
    if not nic_data:
        print("No interfaces with a usable queue count "
              "(ethtool -l Combined/RX, else /proc/interrupts).")
        if not args.virtual:
            print("Virtual devices are skipped; pass --virtual to include them.")
        sys.exit(1)

    print_nic_memory_table(
        nic_data, verbose=args.verbose, unit=args.unit, include_tx=args.include_tx
    )


if __name__ == "__main__":
    main()
