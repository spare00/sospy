#!/usr/bin/env python3

import argparse
import subprocess
import sys

def run_command(title, command, verbose=False):
    print(title)

    if verbose:
        print(f"$ {command}")

    try:
        output = subprocess.check_output(command, shell=True, executable='/bin/bash', text=True)
        print(output, end='')
        print("")
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description='Display top CPU-consuming processes/threads or filtered thread states.'
    )
    parser.add_argument('-a', action='store_true', help='Show all reports (-p -t -r -d -c)')
    parser.add_argument('-p', action='store_true', help='Show top CPU-consuming processes from ps')
    parser.add_argument('-t', action='store_true', help='Show top CPU-consuming threads from ps -elfL')
    parser.add_argument('-r', action='store_true', help='Show threads in R (running) state')
    parser.add_argument('-d', action='store_true', help='Show threads in D (uninterruptible sleep) state')
    parser.add_argument('-c', action='store_true', help='Show number of threads per application (by command)')
    parser.add_argument('-n', type=int, default=10, help='Number of top entries to display (default: 10)')
    parser.add_argument('-v', action='store_true', help='Verbose mode: show the actual command being run')

    args = parser.parse_args()

    if not (args.a or args.p or args.t or args.r or args.d):
        parser.print_help()
        return

    if args.a:
        args.p = args.t = args.r = args.d = args.c = True

    if args.p:
        cmd = (
            f'(head -n 1 && tail -n +2 | sort -nrk3 | head -n {args.n}) < ps && '
            f'awk \'NR!=1 {{ sum+=$3 }} END {{ printf "------------------------\\nTotal CPU usage: %% %.1f\\n", sum }}\' < ps'
        )
        run_command("Top CPU consuming processes", cmd, verbose=args.v)

    if args.t:
        cmd = (
            f'(head -n 1 && tail -n +2 | sort -nrk7 | head -n {args.n}) < sos_commands/process/ps_-elfL && '
            f'awk \'NR!=1 {{ sum+=$7 }} END {{ printf "------------------------\\nTotal CPU usage: %% %.1f\\n", sum }}\' < sos_commands/process/ps_-elfL'
        )
        run_command("Top CPU consuming threads", cmd, verbose=args.v)

    if args.r:
        cmd = (
            f'(head -n 1 && tail -n +2 | awk \'$2 ~/R/\' | sort -k7 -nr | head -n {args.n}) < sos_commands/process/ps_-elfL && '
            f'awk \'NR!=1 && $2~/R/ {{ sum+=$7 }} END {{ printf "------------------------\\nTotal CPU usage: %% %.1f\\n", sum }}\' sos_commands/process/ps_-elfL'
        )
        run_command("Top CPU-Consuming Threads in Running (R) State", cmd, verbose=args.v)

    if args.d:
        cmd = (
            f'(head -n 1 && tail -n +2 | awk \'$2 ~/D/\' | sort -k7 -nr | head -n {args.n}) < sos_commands/process/ps_-elfL'
        )
        run_command("Top CPU-Consuming Threads in Blocked (D) State", cmd, verbose=args.v)

    if args.c:
        cmd = (
            f'awk \'{{for (i=17; i<=NF; i++) printf "%s%s", $i, (i==NF ? "\\n" : OFS)}}\' '
            f'sos_commands/process/ps_-elfL | sort | uniq -c | sort -nrk1 | head -n {args.n}'
        )
        run_command("Top Applications by Thread Count", cmd, verbose=args.v)

if __name__ == '__main__':
    main()

