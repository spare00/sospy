#!/usr/bin/env python3
"""
chk_irq.py — Compact /proc/interrupts view with IRQ CPU affinity.

Like xsos -f INTERRUPTS: after each IRQ's smp_affinity_list, one character per
CPU — '.' if that CPU's interrupt count is 0, '▊' if non-zero (same idea as xsos).

Usage:
    ./chk_irq.py [--path SOSROOT] [-c] [-n] [-v] [--width N]

    --path    Path to sosreport root or live / (default: current directory)
    -c        Show smp_affinity_list (default: off; bar + description only)
    -n        Show NUMA node from sys/class/net/<iface>/device/numa_node
    -v        Print legend (CPU count, column sources, ▊/. meaning)
    --width   Max characters when truncating long smp_affinity_list text (default: 256)
"""

from __future__ import annotations

import argparse
import os
import re
import sys


def parse_interrupts(root: str) -> tuple[list[str], list[tuple[str, list[int], str]]]:
    path = os.path.join(root, "proc", "interrupts")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    if not lines:
        return [], []

    header_parts = lines[0].split()
    cpus = [p for p in header_parts if p.upper().startswith("CPU")]
    n = len(cpus)
    rows: list[tuple[str, list[int], str]] = []

    for line in lines[1:]:
        m = re.match(r"^\s*([^:]+):\s+(.*)$", line.rstrip())
        if not m:
            continue
        irq_key = m.group(1).strip()
        rest = m.group(2)
        parts = rest.split()
        counts: list[int] = []
        i = 0
        while i < len(parts) and len(counts) < n:
            try:
                counts.append(int(parts[i]))
            except ValueError:
                break
            i += 1
        desc = " ".join(parts[i:]).strip()
        rows.append((irq_key, counts, desc))

    return cpus, rows


def compact_cpu_bar(counts: list[int], n_cpus: int, zero: str = ".", nonzero: str = "▊") -> str:
    """One glyph per CPU: nonzero interrupt count → nonzero char, else zero char."""
    if n_cpus <= 0:
        return ""
    if len(counts) < n_cpus:
        counts = counts + [0] * (n_cpus - len(counts))
    elif len(counts) > n_cpus:
        counts = counts[:n_cpus]
    return "".join(nonzero if c else zero for c in counts)


def read_smp_affinity_list(root: str, irq_key: str) -> str | None:
    if not irq_key.isdigit():
        return None
    p = os.path.join(root, "proc", "irq", irq_key, "smp_affinity_list")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def truncate_affinity(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    if max_len <= 1:
        return "…"
    return s[: max_len - 1] + "…"


def netdev_name_candidates(desc: str) -> list[str]:
    """
    sysfs lives under sys/class/net/<name>/... — IRQ descriptions often end with the
    netdev or a queue name like enp59s0f0-0; try full token then parent without -N.
    """
    tokens = desc.split()
    if not tokens:
        return []
    name = tokens[-1]
    out: list[str] = []
    for cand in (name,):
        if cand and cand not in out:
            out.append(cand)
    m = re.match(r"^(.+)-(\d+)$", name)
    if m:
        base = m.group(1)
        if base and base not in out:
            out.append(base)
    return out


def read_numa_node(root: str, desc: str) -> str | None:
    for cand in netdev_name_candidates(desc):
        p = os.path.join(root, "sys", "class", "net", cand, "device", "numa_node")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            continue
    return None


def format_irq_line(
    irq_key: str,
    irq_width: int,
    counts: list[int],
    n_cpus: int,
    description: str,
    show_affinity: bool,
    affinity_padded: str,
    show_numa: bool,
    numa_padded: str,
) -> str:
    irq_label = f"{irq_key}:"
    bar = compact_cpu_bar(counts, n_cpus)
    parts: list[str] = [f"{irq_label:>{irq_width}}"]
    if show_affinity:
        parts.append(affinity_padded)
    if show_numa:
        parts.append(numa_padded)
    parts.append(bar)
    parts.append(description)
    return " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show /proc/interrupts in compact form with smp_affinity_list per IRQ"
    )
    parser.add_argument(
        "--path",
        default=".",
        help="Path to sosreport root or filesystem root (default: .)",
    )
    parser.add_argument(
        "-c",
        action="store_true",
        help="Show smp_affinity_list column (default: omit)",
    )
    parser.add_argument(
        "-n",
        action="store_true",
        help="Show NUMA node from sys/class/net/<iface>/device/numa_node",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print legend (CPU count, column sources, ▊/. meaning)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=256,
        metavar="N",
        help="Truncate smp_affinity_list to N chars if longer (default: 256)",
    )
    args = parser.parse_args()
    root = os.path.abspath(args.path)
    aff_max = max(4, args.width)
    show_affinity = args.c
    show_numa = args.n

    cpus, rows = parse_interrupts(root)
    if not rows:
        print("No interrupt lines parsed.", file=sys.stderr)
        sys.exit(1)

    irq_w = max(len(r[0]) + 1 for r in rows)  # "12:" etc.
    irq_w = max(irq_w, 5)

    n_cpu = len(cpus)
    aff_displays: list[str] = []
    for irq_key, _counts, _desc in rows:
        aff = read_smp_affinity_list(root, irq_key)
        raw = aff if aff is not None else "-"
        aff_displays.append(truncate_affinity(raw, aff_max))
    affinity_col_w = max(len(a) for a in aff_displays) if aff_displays else 1

    numa_displays: list[str] = []
    if show_numa:
        for _irq_key, _counts, desc in rows:
            nv = read_numa_node(root, desc)
            numa_displays.append(nv if nv is not None else "-")
    numa_col_w = (
        max(len(a) for a in numa_displays) if show_numa and numa_displays else 1
    )

    title = "INTERRUPTS (per-CPU ▊/.)"
    print(title)
    if args.verbose:
        legend_bits = [f"{n_cpu} CPUs"]
        if show_affinity:
            legend_bits.append("affinity from proc/irq/<n>/smp_affinity_list")
        if show_numa:
            legend_bits.append("NUMA from sys/class/net/<iface>/device/numa_node")
        legend_bits.append("▊ = non-zero count, . = zero")
        print(f"    ({'; '.join(legend_bits)})")

    numa_iter = numa_displays if show_numa else [""] * len(rows)
    for (irq_key, counts, desc), aff_display, numa_raw in zip(
        rows, aff_displays, numa_iter
    ):
        aff_padded = aff_display.ljust(affinity_col_w)
        numa_padded = numa_raw.ljust(numa_col_w)
        line = format_irq_line(
            irq_key,
            irq_w,
            counts,
            n_cpu,
            desc,
            show_affinity,
            aff_padded,
            show_numa,
            numa_padded,
        )
        print(f"    {line}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
