#!/usr/bin/env python3

import subprocess
import re
import sys
import os
import argparse

# Dynamically resolve the decodecode path relative to this script's real location
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DECODECODE_PATH = os.path.join(SCRIPT_DIR, "decodecode")

def extract_code_blocks(filename):
    blocks = []
    current_rip = None

    with open(filename, 'r') as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        rip_match = re.search(r'\[\s*\d+\.\d+\]\s+RIP:\s+(\S+):(.+)', line)
        if rip_match:
            segment = rip_match.group(1)
            location = rip_match.group(2).strip()
            current_rip = f"{segment}:{location}"

        code_match = re.search(r'(\[\s*\d+\.\d+\])\s+Code:\s+.*', line)
        if code_match:
            timestamp = code_match.group(1)
            code_line = line.strip()
            blocks.append((timestamp, current_rip, code_line, i, lines))  # include line index + full log
    return blocks

def run_decodecode(code_line, debug=False):
    code_line = re.sub(r'^\[\s*\d+\.\d+\]\s+', '', code_line)
    if not code_line.startswith("Code:"):
        return f"[ERROR] Invalid code line: {code_line}"

    env = os.environ.copy()
    env["AFLAGS"] = "--64"

    result = subprocess.run(
        [DECODECODE_PATH],
        input=f"{code_line}\n",
        capture_output=True,
        text=True,
        env=env
    )

    if result.returncode != 0 and debug:
        return (
            f"[WARNING] decodecode exited with {result.returncode} (possibly expected)\n"
            f"Input line: {code_line}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    # Always return full stdout, even if empty
    return result.stdout

def extract_trap_register(disasm_output):
    """
    Parses the disassembly output to find the trapping instruction
    and extracts the register used in a memory address (e.g., (%rax))
    """
    for line in disasm_output.splitlines():
        if '<-- trapping instruction' in line:
            # Try to extract something like (%rax) or (%rdx,%rcx,8)
            mem_match = re.search(r'\((%[a-z0-9]+)(?:,[^)]*)?\)', line)
            if mem_match:
                return mem_match.group(1).upper().lstrip('%')  # e.g. RAX
            # Fallback: pick first register
            reg_match = re.search(r'%([a-z0-9]+)', line)
            if reg_match:
                return reg_match.group(1).upper()
    return None

def find_register_value(register_name, log_lines, start_index):
    reg_pattern = re.compile(rf'\b{register_name}:\s*([0-9a-fA-Fx]+)')
    # Look forward a few lines after code line
    for i in range(start_index, min(len(log_lines), start_index + 20)):
        match = reg_pattern.search(log_lines[i])
        if match:
            return match.group(1)
    return None

def main():
    parser = argparse.ArgumentParser(
        description="Extract and decode 'Code:' lines from kernel oops logs."
    )
    parser.add_argument("logfile", help="Input kernel oops log file")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show extra disassembly")
    args = parser.parse_args()

    if not os.path.isfile(args.logfile):
        print(f"[ERROR] File not found: {args.logfile}")
        sys.exit(1)

    code_blocks = extract_code_blocks(args.logfile)
    if not code_blocks:
        print("No 'Code:' lines found in the file.")
        return

    for idx, (timestamp, rip_info, code_line, line_no, full_log) in enumerate(code_blocks, start=1):
        print(f"\n=== Decoding Code Block #{idx} ===")
        print(f"⏱️  Timestamp: {timestamp}")
        print(f"📍 RIP: {rip_info or '[unknown]'}")

        if rip_info and rip_info.startswith("0010"):
            print("🧠 Context: [KERNEL MODE]")
        elif rip_info and rip_info.startswith("0033"):
            print("🧍 Context: [USER MODE]")
        else:
            print("❓ Context: [UNKNOWN]")

        disasm_output = run_decodecode(code_line, debug=args.debug)

        if not disasm_output.strip():
            print(f"[⚠️] No disassembly output from decodecode for block #{idx}.")
            continue

        # Split decodecode output
        lines = disasm_output.splitlines()
        main_disasm = []
        extra_disasm = []
        in_extra = False

        for line in lines:
            if line.strip().startswith("Code starting with the faulting instruction"):
                in_extra = True
                continue
            if in_extra:
                extra_disasm.append(line)
            else:
                main_disasm.append(line)

        # Always show the main part
        print("\n".join(main_disasm))

        # Show the extra disasm only in verbose mode
        if args.verbose and extra_disasm:
            print("\nCode starting with the faulting instruction")
            print("===========================================")
            print("\n".join(extra_disasm))

        # Extract and display the register involved in the fault
        trap_reg = extract_trap_register(disasm_output)
        if trap_reg:
            reg_val = find_register_value(trap_reg, full_log, line_no)
            if reg_val:
                print(f"\n💥 Trapping instruction used {trap_reg} = {reg_val}")
            else:
                print(f"\n💥 Trapping instruction used {trap_reg}, but value not found nearby.")

if __name__ == "__main__":
    main()

