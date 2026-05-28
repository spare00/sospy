#!/usr/bin/env python3
"""
chk_irq.py — Compact /proc/interrupts view with IRQ CPU affinity.

Like xsos -f INTERRUPTS: after each IRQ's smp_affinity_list, one character per
CPU — '.' if that CPU's interrupt count is 0, '▊' if non-zero (same idea as xsos).

Usage:
    ./chk_irq.py [--path SOSROOT] [-c] [-H] [-n] [-p] [-v] [--width N]
                 [--irq N] [--desc KEYWORD]

    --path    Path to sosreport root or live / (default: current directory)
    --irq     Show only IRQ(s) with this number (repeatable; matches irq column)
    --desc    Show only rows whose description contains KEYWORD (case-insensitive)
    -c        Show smp_affinity_list (default: off; bar + description only)
    -H        Show affinity_hint (hex bitmask →CPU list like smp_affinity_list when needed)
    -n        Show IRQ NUMA node from proc/irq/<n>/node
    -p        Show % of IRQ counts handled on CPUs outside smp_affinity_list
    -v        Print legend (CPU count, column sources, ▊/. meaning)
    --width   Max characters when truncating affinity / hint text (default: 256)

NUMA locality highlight (TTY, unless --no-color): smp_affinity_list vs CPUs on
proc/irq/<n>/node; topology from numactl --hardware or sysfs node cpulist.
  • Yellow — partial mismatch: some affinity CPUs on-node, some off-node.
  • Red — whole mismatch: no affinity CPU is on the IRQ's node (all off-node).
"""

from __future__ import annotations

import argparse
import glob
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


def irq_matches(irq_key: str, irq_filter: str) -> bool:
    """True if irq_key matches filter (exact or numeric IRQ number)."""
    if irq_key == irq_filter:
        return True
    if irq_key.isdigit() and irq_filter.isdigit():
        return int(irq_key) == int(irq_filter)
    return False


def filter_interrupt_rows(
    rows: list[tuple[str, list[int], str]],
    irq_filters: list[str] | None,
    desc_keyword: str | None,
) -> list[tuple[str, list[int], str]]:
    """Keep rows matching all given filters (AND)."""
    out = rows
    if irq_filters:
        wanted = {f.strip() for f in irq_filters if f.strip()}
        out = [
            row
            for row in out
            if any(irq_matches(row[0], filt) for filt in wanted)
        ]
    if desc_keyword:
        needle = desc_keyword.casefold()
        out = [row for row in out if needle in row[2].casefold()]
    return out


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


def read_affinity_hint(root: str, irq_key: str) -> str | None:
    if not irq_key.isdigit():
        return None
    p = os.path.join(root, "proc", "irq", irq_key, "affinity_hint")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


_HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]+$")


def cpus_sorted_to_smp_list_style(cpus: list[int]) -> str:
    """Comma-separated ranges/singles like smp_affinity_list (e.g. 0-3,7,9-11)."""
    cpus = sorted(set(cpus))
    if not cpus:
        return "-"
    parts: list[str] = []
    lo = hi = cpus[0]
    for c in cpus[1:]:
        if c == hi + 1:
            hi = c
        else:
            parts.append(str(lo) if lo == hi else f"{lo}-{hi}")
            lo = hi = c
    parts.append(str(lo) if lo == hi else f"{lo}-{hi}")
    return ",".join(parts)


def hex_irq_mask_words_to_cpulist(hex_words: list[str]) -> str:
    """
    Kernel affinity_hint / smp_affinity: comma-separated hex u32 (or mixed-width) words.

    In /proc, groups are usually printed with the **lowest** CPU block **last** in the
    string (e.g. …,00000001 for CPU 0). Bit i inside a word is CPU offset i within that
    block; 00000001 → bit 0 → first CPU in that block; 04000000 → bit 26 → CPU 26 in
    that block. Reverse words so index 0 maps to CPUs 0–bits_per_word-1.
    """
    max_hex_len = max(len(w) for w in hex_words)
    bits_per_word = max_hex_len * 4
    norm = [w.zfill(max_hex_len) for w in hex_words]
    norm.reverse()
    cpus: list[int] = []
    for wi, ww in enumerate(norm):
        v = int(ww, 16)
        for b in range(bits_per_word):
            if v & (1 << b):
                cpus.append(wi * bits_per_word + b)
    return cpus_sorted_to_smp_list_style(cpus)


def format_affinity_hint_for_display(raw: str) -> str:
    """
    Comma-separated hex (IRQ affinity bitmask) → smp_affinity_list-style digits.
    Leaves cpulist-style text (digits, '-', ',') unchanged when not a hex mask.
    """
    s = raw.strip()
    if not s or s == "-":
        return "-"
    if "," in s:
        words = [t.strip() for t in s.split(",") if t.strip()]
        if words and all(_HEX_TOKEN_RE.match(t) for t in words):
            return hex_irq_mask_words_to_cpulist(words)
        return raw
    # Single long hex string (no commas): LSB = CPU 0
    if _HEX_TOKEN_RE.match(s) and len(s) >= 4:
        try:
            val = int(s, 16)
        except ValueError:
            return raw
        cpus = [i for i in range(val.bit_length()) if (val >> i) & 1]
        return cpus_sorted_to_smp_list_style(cpus)
    return raw


def read_irq_numa_node(root: str, irq_key: str) -> int | None:
    """NUMA node id from proc/irq/<n>/node (irqbalance / locality)."""
    if not irq_key.isdigit():
        return None
    p = os.path.join(root, "proc", "irq", irq_key, "node")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            t = f.read().strip()
        if not t or not t.lstrip("-").isdigit():
            return None
        return int(t)
    except OSError:
        return None


def expand_cpu_token(token: str) -> set[int]:
    t = token.strip()
    if not t:
        return set()
    if "-" in t:
        a, _, b = t.partition("-")
        try:
            lo, hi = int(a), int(b)
            if lo <= hi:
                return set(range(lo, hi + 1))
        except ValueError:
            return set()
        return set()
    try:
        return {int(t)}
    except ValueError:
        return set()


def parse_cpulist_like(s: str) -> set[int]:
    """Parse smp_affinity_list / sysfs cpulist (comma-separated ranges and singles)."""
    out: set[int] = set()
    if not s or s == "-":
        return out
    for part in s.split(","):
        out |= expand_cpu_token(part)
    return out


def off_affinity_irq_pct(counts: list[int], affinity_raw: str | None) -> float | None:
    """
    Percent of this IRQ's /proc/interrupts counts on CPUs not in smp_affinity_list.

    Returns None when affinity is unknown or unparsable.
    """
    if affinity_raw is None:
        return None
    affinity = parse_cpulist_like(affinity_raw)
    if not affinity:
        return None
    total = sum(counts)
    if total == 0:
        return 0.0
    off = sum(c for i, c in enumerate(counts) if i not in affinity)
    return 100.0 * off / total


def format_off_affinity_pct(pct: float | None) -> str:
    if pct is None:
        return "-"
    return f"{pct:5.1f}%"


def parse_numactl_hardware_nodes(path: str) -> dict[int, set[int]]:
    """
    Parse `numactl --hardware` output:
        node 0 cpus: 0 1 2 ...
    """
    nodes: dict[int, set[int]] = {}
    pat = re.compile(r"^\s*node\s+(\d+)\s+cpus:\s*(.*)$")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.match(line)
                if not m:
                    continue
                nid = int(m.group(1))
                rest = m.group(2).strip()
                cpus: set[int] = set()
                for tok in rest.split():
                    try:
                        cpus.add(int(tok))
                    except ValueError:
                        continue
                if cpus:
                    nodes[nid] = cpus
    except OSError:
        return {}
    return nodes


def find_numactl_hardware(root: str) -> str | None:
    patterns = [
        os.path.join(root, "sos_commands", "numa", "numactl_--hardware"),
        os.path.join(root, "sos_commands", "numa", "numactl*hardware*"),
    ]
    direct = patterns[0]
    if os.path.isfile(direct):
        return direct
    globs = glob.glob(patterns[1])
    return globs[0] if globs else None


def load_node_cpus_sysfs(root: str) -> dict[int, set[int]]:
    """Fallback: sys/devices/system/node/nodeN/cpulist."""
    base = os.path.join(root, "sys", "devices", "system", "node")
    if not os.path.isdir(base):
        return {}
    nodes: dict[int, set[int]] = {}
    for node_dir in sorted(glob.glob(os.path.join(base, "node[0-9]*"))):
        m = re.search(r"node(\d+)$", os.path.basename(node_dir))
        if not m:
            continue
        nid = int(m.group(1))
        cp = os.path.join(node_dir, "cpulist")
        if not os.path.isfile(cp):
            continue
        try:
            with open(cp, encoding="utf-8", errors="replace") as f:
                lst = f.read().strip()
        except OSError:
            continue
        cpus = parse_cpulist_like(lst)
        if cpus:
            nodes[nid] = cpus
    return nodes


def load_node_cpu_map(root: str) -> dict[int, set[int]]:
    npath = find_numactl_hardware(root)
    if npath and os.path.isfile(npath):
        nodes = parse_numactl_hardware_nodes(npath)
        if nodes:
            return nodes
    return load_node_cpus_sysfs(root)


def numa_affinity_mismatch_severity(
    affinity_raw: str | None,
    irq_node: int | None,
    node_cpus: dict[int, set[int]],
) -> str | None:
    """
    None — OK or cannot judge (missing topology / irq node / affinity).
    "partial" — some smp_affinity CPUs on proc/irq/<n>/node, some not (yellow).
    "whole" — none on-node, at least one off-node (red).
    """
    if affinity_raw is None or irq_node is None:
        return None
    allowed = node_cpus.get(irq_node)
    if not allowed:
        return None
    aff = parse_cpulist_like(affinity_raw)
    if not aff:
        return None
    inside = aff & allowed
    outside = aff - allowed
    if not outside:
        return None
    if not inside:
        return "whole"
    return "partial"


def highlight_line(line: str, severity: str | None, color: bool) -> str:
    if not severity or not color:
        return line
    if severity == "whole":
        return f"\033[1m\033[91m{line}\033[0m"
    if severity == "partial":
        return f"\033[1m\033[93m{line}\033[0m"
    return line


def truncate_affinity(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    if max_len <= 1:
        return "…"
    return s[: max_len - 1] + "…"


def pad_header_cell(label: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(label) <= width:
        return label.ljust(width)
    if width <= 1:
        return label[0]
    return label[: width - 1] + "…"


def format_column_headers(
    irq_width: int,
    show_hint: bool,
    hint_col_width: int,
    show_affinity: bool,
    affinity_col_width: int,
    show_numa: bool,
    numa_col_width: int,
    n_cpus: int,
    show_off_affinity_pct: bool = False,
    off_affinity_col_width: int = 1,
) -> str:
    """One header row aligned with format_irq_line / join(\" \")."""
    parts: list[str] = [f"{'IRQ':>{irq_width}}"]
    if show_hint:
        parts.append(pad_header_cell("affinity_hint", hint_col_width))
    if show_affinity:
        parts.append(pad_header_cell("affinity", affinity_col_width))
    if show_numa:
        parts.append(pad_header_cell("node", numa_col_width))
    if show_off_affinity_pct:
        parts.append(pad_header_cell("off_aff%", off_affinity_col_width))
    # Same width as compact_cpu_bar() (one cell per CPU); dots as neutral ruler.
    parts.append(("." * n_cpus) if n_cpus else "")
    parts.append("description")
    return " ".join(parts)


def format_irq_line(
    irq_key: str,
    irq_width: int,
    counts: list[int],
    n_cpus: int,
    description: str,
    show_hint: bool,
    hint_padded: str,
    show_affinity: bool,
    affinity_padded: str,
    show_numa: bool,
    numa_padded: str,
    show_off_affinity_pct: bool = False,
    off_affinity_padded: str = "",
) -> str:
    irq_label = f"{irq_key}:"
    bar = compact_cpu_bar(counts, n_cpus)
    parts: list[str] = [f"{irq_label:>{irq_width}}"]
    if show_hint:
        parts.append(hint_padded)
    if show_affinity:
        parts.append(affinity_padded)
    if show_numa:
        parts.append(numa_padded)
    if show_off_affinity_pct:
        parts.append(off_affinity_padded)
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
        "-H",
        action="store_true",
        help="Show affinity_hint; hex comma-separated masks are shown like smp_affinity_list",
    )
    parser.add_argument(
        "-n",
        action="store_true",
        help="Show IRQ NUMA node from proc/irq/<n>/node",
    )
    parser.add_argument(
        "-p",
        action="store_true",
        help="Show %% of IRQ counts on CPUs outside smp_affinity_list (off_aff%%)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print legend (CPU count, column sources, ▊/. meaning)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI highlight (yellow=partial, red=whole NUMA mismatch)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=256,
        metavar="N",
        help="Truncate smp_affinity_list to N chars if longer (default: 256)",
    )
    parser.add_argument(
        "--irq",
        action="append",
        metavar="N",
        default=None,
        help="Show only matching IRQ number (repeatable)",
    )
    parser.add_argument(
        "--desc",
        metavar="KEYWORD",
        default=None,
        help="Show only rows whose description contains KEYWORD (case-insensitive)",
    )
    args = parser.parse_args()
    root = os.path.abspath(args.path)
    aff_max = max(4, args.width)
    show_affinity = args.c
    show_hint = args.H
    show_numa = args.n
    show_off_affinity_pct = args.p
    use_color = sys.stdout.isatty() and not args.no_color

    cpus, rows = parse_interrupts(root)
    if not rows:
        print("No interrupt lines parsed.", file=sys.stderr)
        sys.exit(1)

    rows = filter_interrupt_rows(rows, args.irq, args.desc)
    if not rows:
        print("No interrupts match the given filters.", file=sys.stderr)
        sys.exit(1)

    irq_w = max(len(r[0]) + 1 for r in rows)  # "12:" etc.
    irq_w = max(irq_w, 5)

    n_cpu = len(cpus)
    node_cpu_map = load_node_cpu_map(root)

    aff_displays: list[str] = []
    affinity_raws: list[str | None] = []
    hint_displays: list[str] = []
    irq_nodes: list[int | None] = []
    for irq_key, _counts, _desc in rows:
        aff = read_smp_affinity_list(root, irq_key)
        affinity_raws.append(aff)
        raw = aff if aff is not None else "-"
        aff_displays.append(truncate_affinity(raw, aff_max))
        hint = read_affinity_hint(root, irq_key)
        hint_raw = hint if hint is not None else "-"
        hint_display = format_affinity_hint_for_display(hint_raw)
        hint_displays.append(truncate_affinity(hint_display, aff_max))
        irq_nodes.append(read_irq_numa_node(root, irq_key))
    affinity_col_w = max(len(a) for a in aff_displays) if aff_displays else 1
    if show_affinity:
        affinity_col_w = max(affinity_col_w, len("affinity"))
    hint_col_w = max(len(h) for h in hint_displays) if hint_displays else 1
    if show_hint:
        hint_col_w = max(hint_col_w, len("affinity_hint"))

    numa_displays: list[str] = []
    if show_numa:
        for inode in irq_nodes:
            numa_displays.append(str(inode) if inode is not None else "-")
    numa_col_w = (
        max(len(a) for a in numa_displays) if show_numa and numa_displays else 1
    )
    if show_numa:
        numa_col_w = max(numa_col_w, len("node"))

    off_affinity_displays: list[str] = []
    if show_off_affinity_pct:
        for (irq_key, counts, _desc), aff_raw in zip(rows, affinity_raws):
            if irq_key.isdigit():
                pct = off_affinity_irq_pct(counts, aff_raw)
            else:
                pct = None
            off_affinity_displays.append(format_off_affinity_pct(pct))
    off_affinity_col_w = (
        max(len(a) for a in off_affinity_displays)
        if show_off_affinity_pct and off_affinity_displays
        else 1
    )
    if show_off_affinity_pct:
        off_affinity_col_w = max(off_affinity_col_w, len("off_aff%"))

    title = "INTERRUPTS (per-CPU ▊/.)"
    print(title)
    if args.verbose:
        legend_bits = [f"{n_cpu} CPUs"]
        if show_hint:
            legend_bits.append(
                "affinity_hint from proc/irq/<n>/affinity_hint "
                "(comma-separated hex words → smp_affinity_list-style CPUs)"
            )
        if show_affinity:
            legend_bits.append("affinity from proc/irq/<n>/smp_affinity_list")
        if show_numa:
            legend_bits.append("node from proc/irq/<n>/node (IRQ NUMA mapping)")
        if show_off_affinity_pct:
            legend_bits.append(
                "off_aff% = share of counts on CPUs not in smp_affinity_list"
            )
        if args.irq or args.desc:
            filt_bits: list[str] = []
            if args.irq:
                filt_bits.append(f"irq={','.join(args.irq)}")
            if args.desc:
                filt_bits.append(f"desc contains {args.desc!r}")
            legend_bits.append("filters: " + "; ".join(filt_bits))
        legend_bits.append("▊ = non-zero count, . = zero")
        legend_bits.append(
            "highlight: affinity vs proc/irq/<n>/node CPUs "
            "(yellow=partial off-node, red=all off-node; topology numactl or sysfs)"
        )
        print(f"    ({'; '.join(legend_bits)})")

    print(
        "    "
        + format_column_headers(
            irq_w,
            show_hint,
            hint_col_w,
            show_affinity,
            affinity_col_w,
            show_numa,
            numa_col_w,
            n_cpu,
            show_off_affinity_pct,
            off_affinity_col_w,
        )
    )

    numa_iter = numa_displays if show_numa else [""] * len(rows)
    hint_iter = hint_displays if show_hint else [""] * len(rows)
    off_affinity_iter = (
        off_affinity_displays if show_off_affinity_pct else [""] * len(rows)
    )
    for (irq_key, counts, desc), aff_display, numa_raw, aff_raw, irq_node, hint_display, off_aff_display in zip(
        rows,
        aff_displays,
        numa_iter,
        affinity_raws,
        irq_nodes,
        hint_iter,
        off_affinity_iter,
    ):
        hint_padded = hint_display.ljust(hint_col_w)
        aff_padded = aff_display.ljust(affinity_col_w)
        numa_padded = numa_raw.ljust(numa_col_w)
        off_aff_padded = off_aff_display.ljust(off_affinity_col_w)
        line = format_irq_line(
            irq_key,
            irq_w,
            counts,
            n_cpu,
            desc,
            show_hint,
            hint_padded,
            show_affinity,
            aff_padded,
            show_numa,
            numa_padded,
            show_off_affinity_pct,
            off_aff_padded,
        )
        sev = numa_affinity_mismatch_severity(aff_raw, irq_node, node_cpu_map)
        line_out = highlight_line(f"    {line}", sev, use_color)
        print(line_out)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
