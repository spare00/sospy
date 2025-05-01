#!/usr/bin/env python3

import os
import sys
import argparse
from pathlib import Path
import fnmatch

def find_chk_scripts(root='.'):
    """Recursively find Python files strictly matching chk_*.py."""
    return [
        p.resolve() for p in Path(root).rglob('chk_*.py')
        if p.is_file() and fnmatch.fnmatch(p.name, 'chk_*.py')
    ]

def ensure_bin_dir(bin_dir):
    """Ensure ~/bin directory exists."""
    bin_dir.mkdir(parents=True, exist_ok=True)

def create_symlink(target, link_path, force=False):
    """Create or update a symbolic link."""
    if link_path.exists() or link_path.is_symlink():
        if link_path.is_symlink():
            if link_path.resolve() == target:
                print(f"[SKIP] Symlink already exists and is correct: {link_path}")
                return
            elif force:
                print(f"[OVERWRITE] Updating symlink: {link_path}")
                link_path.unlink()
            else:
                print(f"[SKIP] Symlink exists, use --force to overwrite: {link_path}")
                return
        else:
            print(f"[SKIP] Path exists and is not a symlink: {link_path}")
            return

    link_path.symlink_to(target)
    print(f"[LINKED] {link_path} -> {target}")

def main():
    parser = argparse.ArgumentParser(
        description="Set up symlinks for chk_*.py scripts into ~/bin/"
    )
    parser.add_argument('-f', '--force', action='store_true',
                        help="Force overwrite of existing symlinks")
    args = parser.parse_args()

    bin_dir = Path.home() / 'bin'
    ensure_bin_dir(bin_dir)

    scripts = find_chk_scripts()
    if not scripts:
        print("No chk_*.py scripts found.")
        return

    for script in scripts:
        link_path = bin_dir / script.name
        create_symlink(script, link_path, force=args.force)

if __name__ == "__main__":
    main()
