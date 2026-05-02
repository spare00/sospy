chk_*.py Tools Setup for SOS, Perf, and Strace Analysis
=======================================================

This project provides a collection of chk_*.py diagnostic scripts designed to help analyze
data from sosreport, perf, strace, and other system-level tools. These scripts are made
easily executable from anywhere on your system using a setup utility.


1. Prerequisites
----------------

- Python 3 must be installed.
- Ensure your shell includes ~/bin in the PATH. Add this to your ~/.bashrc or ~/.zshrc:

    export PATH="$HOME/bin:$PATH"


2. Getting Started
------------------

Step 1: Clone the Repository

    git clone https://github.com/spare00/sospy.git

Step 2: Make the setup script executable from the root of your cloned repository

    chmod +x setup_chk_tools.py

Step 3: Run the script

    ./setup_chk_tools.py

This will:
- Ensure the ~/bin/ directory exists.
- Recursively search the repository for `chk_*.py` files (from the directory where you run the script).
- Create symbolic links in `~/bin/` named after each script basename (e.g. `chk_mem.py` -> full path to that file).
- Skip symlinks that already point at the correct target. Use `--force` to replace symlinks that point elsewhere; correct links are still skipped even with `--force`.

Step 4: (Optional) Replace stale symlinks (same name in `~/bin/` but wrong target)

    ./setup_chk_tools.py --force


3. Verifying Setup
------------------

After setup, you can execute any of the scripts from any directory, for example:

    chk_pidstat.py
    chk_ps_cpu.py
    chk_mem.py


4. Optional Configuration
-------------------------

- Ensure every script begins with a proper shebang line for portability:

    #!/usr/bin/env python3

- Make each script executable if not already:

    chmod +x path/to/chk_*.py


5. Uninstallation (Manual)
--------------------------

To remove symlinks in `~/bin/` whose names match `chk_*.py` (how this project names links):

    find ~/bin -maxdepth 1 -type l -name 'chk_*.py' -delete

Run `find` without `-delete` first if you want to preview matches. If two scripts in different folders share the same basename, the setup script would link only one of them; that case is rare in this tree.


6. Notes
--------

- This project is intended for local development, debugging, and performance analysis.
- The setup_chk_tools.py script should NOT be named setup.py to avoid conflicts
  with standard Python packaging tools.
- Run `./setup_chk_tools.py` from the repository root so discovery finds all `chk_*.py` files under subdirectories.


7. Available `chk_*.py` scripts (overview)
------------------------------------------

Scripts live under topic directories; names are indicative only—use each file’s docstring or `--help` for usage.

| Area | Scripts |
|------|---------|
| CPU / processes | `cpu_usage/chk_pidstat.py`, `ps/chk_ps_cpu.py` |
| Memory | `meminfo/chk_mem.py`, `swap_usage/chk_swap.py`, `page_owner/chk_po.py`, `page_owner/chk_page_owner.py`, `page_owner/chk_pg_optimized.py` |
| OOM | `oom/chk_oom.py`, `oom/chk_oom_summary.py`, `oom/chk_oom_ps.py` |
| Slab / page owner helpers | `slab/chk_slab.py`; see also `page_owner/page_owner_slab_info.py` (not linked as `chk_*.py`) |
| I/O / memory map | `iomem/chk_iomem.py`, `iomem/chk_lsmem.py` |
| Storage / SAR | `sar/chk_sar.py` |
| Networking | `networking/chk_nic.py` |
| Tracing | `strace/chk_strace.py`, `perf/chk_perf_script.py` |
| Kernel / boot / cgroups | `boot/chk_bootmode.py`, `cgroup/chk_cg.py`, `interrupt/chk_softirq.py` |
| Security / audit | `audit/chk_audit.py` |
| JVM | `java/chk_xmx.py` |
| Misc | `decodecode/chk_code.py` |

Other Python utilities (e.g. `setup_chk_tools.py`, `date/ts_tool.py`) are not installed as `chk_*.py` symlinks by the setup script.


8. Project Purpose
------------------

This suite of Python tools is designed to streamline the post-analysis of:

- sosreport output (e.g., memory, slab, CPU stats, OOM, cgroup layout)
- perf report data
- strace output

By unifying them under easily callable CLI tools, you can quickly extract useful insights
without navigating deeply into logs or manually filtering system snapshots.


Maintained by: spare00
