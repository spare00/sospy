# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a collection of Python diagnostic tools (`chk_*.py`) for analyzing sosreport output, perf data, strace logs, and other Linux system-level diagnostics. The scripts parse and visualize data related to memory, CPU, OOM events, interrupts, slabs, and more.

## Setup and Installation

Install the tools into `~/bin/` using symbolic links:

```bash
./setup_chk_tools.py          # Create symlinks for all chk_*.py scripts
./setup_chk_tools.py --force  # Overwrite existing symlinks
```

The setup script recursively finds all `chk_*.py` files and creates symlinks in `~/bin/`. After setup, scripts can be executed from anywhere (e.g., `chk_mem.py`, `chk_oom.py`).

## Project Structure

The codebase is organized by diagnostic domain, with each directory containing related scripts and sample data:

- **oom/** - OOM (Out of Memory) event analysis
  - `chk_oom.py` - Parse and display OOM killer events, slab usage, memory stats
  - `chk_oom_summary.py` - Summarize multiple OOM events
  - `chk_oom_ps.py` - Process information from OOM logs
  - `config.py` - Shared regex patterns for memory info extraction

- **meminfo/** - Memory analysis tools
  - `chk_mem.py` - Parse /proc/meminfo and buddyinfo with fragmentation analysis
  - `chk_zoneinfo.py` - Parse /proc/zoneinfo for zone-level memory stats

- **page_owner/** - Page allocation tracking
  - `chk_page_owner.py` - Analyze page_owner output for allocation patterns
  - `chk_po.py` - Alternative page_owner parser
  - `page_owner_slab_info.py` - Cross-reference page_owner with slab data

- **interrupt/** - Interrupt analysis
  - `chk_irq.py` - Compact /proc/interrupts view with CPU affinity, NUMA locality
  - `chk_softirq.py` - Softirq statistics

- **cpu_usage/** - CPU and process analysis
  - `chk_pidstat.py` - Parse pidstat output with timestamp normalization

- **slab/** - Slab allocator analysis
  - `chk_slab.py` - Parse /proc/slabinfo

- **iomem/** - Memory mapping and layout
  - `chk_iomem.py` - Parse /proc/iomem
  - `chk_lsmem.py` - Parse lsmem output

- **swap_usage/** - Swap analysis tools

- **sar/** - SAR (System Activity Report) parsing
  - `chk_sar.py` - Parse and analyze sar output

- **strace/**, **perf/**, **audit/**, **cgroup/**, **boot/**, **java/**, **decodecode/** - Domain-specific diagnostic tools

## Common Patterns

### Script Structure

All `chk_*.py` scripts follow similar patterns:

1. **Shebang**: `#!/usr/bin/env python3` for portability
2. **Argument parsing**: Use `argparse` for command-line options
3. **Path handling**: Accept `--path` to point to sosreport root or live `/`
4. **File parsing**: Read from `proc/`, `sys/`, or log files relative to the root path
5. **Output formatting**: Many scripts support colored output (TTY detection) and custom width/display options
6. **Unit conversion**: Common utilities for converting between pages, KB, MB, GB

### Common Arguments

Many scripts share these argument patterns:

- `--path SOSROOT` - Path to sosreport root or live system (default: current directory)
- `--pagesize N` - Page size in bytes (default: 4096)
- `-v` or `--verbose` - Enable verbose output, legends, or additional details
- `--no-color` - Disable colored output
- `--width N` - Control output width for tables

### Shared Utilities

Scripts define local utilities rather than importing from shared modules:

- **scale_value()** / **parse_memory_to_kb()** - Convert between memory units (P/K/M/G)
- **DEFAULT_PAGE_SIZE_BYTES = 4096** - Standard page size constant
- **Color/formatting** - ANSI escape codes for severity highlighting (risk/warning levels)
- **Regex patterns** - Kernel log timestamp patterns, memory stat patterns

### Sample Data

Each directory contains `sample*.txt` files demonstrating expected input formats. These are test fixtures showing the structure of sosreport files, /proc output, or log formats the scripts parse.

## Development Guidelines

### When Adding Features

- Test with the relevant `sample*.txt` files in the script's directory
- Scripts should be self-contained; avoid cross-directory imports
- Maintain the `chk_*.py` naming convention for tools installed by setup script
- Keep unit conversion utilities local to each script (duplication is acceptable)

### When Parsing New Data Sources

- Follow the pattern: read file → parse with regex → extract structured data → format output
- Handle missing files gracefully (FileNotFoundError)
- Use `encoding='utf-8', errors='ignore'` or `errors='replace'` for robustness
- Strip kernel timestamp prefixes `[uptime]` when parsing dmesg/kernel logs

### Memory and Page Calculations

- Default page size is 4096 bytes unless overridden with `--pagesize`
- Buddy allocator orders: order k holds 2^k base pages
- Fragmentation analysis: check ratio of order-0 pages vs total free pages
- OOM logs report memory in pages; convert to KB/MB/GB for display

### Code Style

- Scripts are standalone; avoid creating new shared libraries
- Use type hints where helpful but not required everywhere
- Prefer explicit over implicit (e.g., unit suffixes in variable names)
- Keep line length reasonable for terminal display (~100-120 chars)
