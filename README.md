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
- Recursively search for chk_*.py Python files.
- Create symbolic links for each script in ~/bin/.
- Skip existing symlinks unless the --force option is used.

Step 4: (Optional) Force overwrite existing symlinks

    ./setup_chk_tools.py --force


3. Verifying Setup
------------------

After setup, you can execute any of the scripts from any directory, for example:

    chk_pidstat.py
    chk_ps_cpu.py
    chk_rcu.py


4. Optional Configuration
-------------------------

- Ensure every script begins with a proper shebang line for portability:

    #!/usr/bin/env python3

- Make each script executable if not already:

    chmod +x path/to/chk_*.py


5. Uninstallation (Manual)
--------------------------

To remove all symbolic links created in ~/bin/:

    find ~/bin -type l -lname "*chk_*.py" -delete


6. Notes
--------

- This project is intended for local development, debugging, and performance analysis.
- The setup_chk_tools.py script should NOT be named setup.py to avoid conflicts
  with standard Python packaging tools.


7. Project Purpose
------------------

This suite of Python tools is designed to streamline the post-analysis of:

- sosreport output (e.g., memory, slab, CPU stats)
- perf report data
- strace output

By unifying them under easily callable CLI tools, you can quickly extract useful insights
without navigating deeply into logs or manually filtering system snapshots.


Maintained by: spare00
