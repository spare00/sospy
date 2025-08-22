#!/usr/bin/env python3

import argparse
import subprocess
import sys

def trim_output(output, max_len):
    if not max_len:
        return output
    trimmed_lines = []
    for line in output.splitlines():
        if len(line) > max_len:
            trimmed_lines.append(line[:max_len] + " ...")
        else:
            trimmed_lines.append(line)
    return "\n".join(trimmed_lines)

def run_command(title, command, verbose=False, max_line_len=None):
    print(title)
    if verbose:
        print(f"$ {command}")
    try:
        output = subprocess.check_output(
            command, shell=True, executable='/bin/bash', text=True, stderr=subprocess.STDOUT
        )
        output = trim_output(output, max_line_len)
        print(output)
        print("")
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}\n{e.output if hasattr(e, 'output') else ''}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description='Display top CPU-consuming processes/threads or filtered thread states.'
    )
    parser.add_argument('-a', action='store_true', help='Show all reports (-s -p -t -r -d -c1 -P)')
    parser.add_argument('-s', action='store_true', help='Show top CPU-consuming processes (Aggregated by Thread Group)')
    parser.add_argument('-p', action='store_true', help='Show top CPU-consuming processes')
    parser.add_argument('-t', action='store_true', help='Show top CPU-consuming threads')
    parser.add_argument('-r', action='store_true', help='Show threads in R (running) state')
    parser.add_argument('-d', action='store_true', help='Show threads in D (uninterruptible sleep) state')
    parser.add_argument('-c', type=int, choices=[1, 2], metavar='MODE',
        help='Show top commands by thread count (1 = base executable only, 2 = full command + args)'
    )
    parser.add_argument('-P', '--ppid', action='store_true',
        help='Show sum of CPU usage of all child threads grouped by PPID')
    parser.add_argument('-n', type=int, default=10, help='Number of top entries to display (default: 10)')
    parser.add_argument('-l', '--max-line-len', type=int, default=140, help='Maximum length of each printed output line')
    parser.add_argument('-v', action='store_true', help='Verbose mode: show the actual command being run')

    # Allow overriding input files (defaults preserve current behavior)
    parser.add_argument('--psl-file', default='sos_commands/process/ps_-elfL',
                        help='Path to ps -elfL output (threads) [default: sos_commands/process/ps_-elfL]')
    parser.add_argument('--ps-file', default='ps',
                        help='Path to ps output used by -p [default: ps]')

    args = parser.parse_args()

    if not (args.a or args.s or args.p or args.t or args.r or args.d or args.c or args.ppid):
        parser.print_help()
        return

    if args.a:
        args.s = args.p = args.t = args.r = args.d = args.ppid = True
        if args.c is None:
            args.c = 1

    # Aggregated by Thread Group (from -elfL)
    if args.s:
        cmd = (
            f"printf \"%10s %6s %5s %s\\n\" \"PID\" \"%CPU\" \"NLWP\" \"CMD\"; "
            f"awk 'NR > 1 {{ cpu[$4] += $7; cmd[$4] = $17; nlwp[$4] = $8 }} "
            f"END {{ for (pid in cpu) printf \"%10s %6d %5d %s\\n\", pid, cpu[pid], nlwp[pid], cmd[pid] }}' "
            f"{args.psl_file} | sort -nrk2 | head -n {args.n}"
        )
        run_command("Top CPU-Consuming Processes (Aggregated by Thread Group)", cmd, verbose=args.v, max_line_len=args.max_line_len)

    # Processes (file given by --ps-file)
    if args.p:
        cmd = (
            f'(head -n 1 && tail -n +2 | sort -nrk3 | head -n {args.n}) < {args.ps_file} && '
            f'awk \'NR!=1 {{ sum+=$3 }} END {{ printf "------------------------\\nTotal CPU usage: %% %.1f\\n", sum }}\' < {args.ps_file}'
        )
        run_command("Top CPU-Consuming Processes", cmd, verbose=args.v, max_line_len=args.max_line_len)

    # Threads
    if args.t:
        cmd = (
            f'(head -n 1 && tail -n +2 | sort -nrk7 | head -n {args.n}) < {args.psl_file} && '
            f'awk \'NR!=1 {{ sum+=$7 }} END {{ printf "------------------------\\nTotal CPU usage: %% %.1f\\n", sum }}\' < {args.psl_file}'
        )
        run_command("Top CPU-Consuming Threads", cmd, verbose=args.v, max_line_len=args.max_line_len)

    # Running (R) state
    if args.r:
        cmd = (
            f'(head -n 1 && tail -n +2 | awk \'$2 ~/R/\' | sort -k7 -nr | head -n {args.n}) < {args.psl_file} && '
            f'awk \'NR!=1 && $2~/R/ {{ sum+=$7 }} END {{ printf "------------------------\\nTotal CPU usage: %% %.1f\\n", sum }}\' {args.psl_file}'
        )
        run_command("Top CPU-Consuming Threads in Running (R) State", cmd, verbose=args.v, max_line_len=args.max_line_len)

    # Blocked (D) state
    if args.d:
        cmd = (
            f'(head -n 1 && tail -n +2 | awk \'$2 ~/D/\' | sort -k7 -nr | head -n {args.n}) < {args.psl_file}'
        )
        run_command("Top CPU-Consuming Threads in Blocked (D) State", cmd, verbose=args.v, max_line_len=args.max_line_len)

    # New: CPU sum per PPID (from ps -elfL)
    if args.ppid:
        # Group by PPID ($5), sum CPU ($7), count threads, and try to show parent cmd by mapping PID->CMD.
        cmd = (
            f'printf "%10s %6s %5s %s\\n" "PPID" "%CPU" "THR" "PARENT_CMD"; '
            f"awk 'NR>1 {{ "
            f"  cpu[$5]+=$7; thr[$5]++; "
            f"  if (!( $4 in seen )) {{ parent[$4]=$17; seen[$4]=1 }} "
            f"}} "
            f"END {{ "
            f"  for (ppid in cpu) printf \"%10s %6.1f %5d %s\\n\", ppid, cpu[ppid], thr[ppid], (ppid in parent ? parent[ppid] : \"-\") "
            f"}}' {args.psl_file} | sort -nrk2 | head -n {args.n}"
        )
        run_command("Sum of CPU Usage of Child Threads Grouped by PPID", cmd, verbose=args.v, max_line_len=args.max_line_len)

    # Commands by thread count
    if args.c is not None:
        if args.c == 2:
            cmd = (
                f"printf \"%6s %s\\n\" \"CNT\" \"CMD\"; "
                f"awk '{{for (i=17; i<=NF; i++) printf \"%s%s\", $i, (i==NF ? \"\\n\" : OFS)}}' "
                f"{args.psl_file} | sort | uniq -c | sort -nrk1 | head -n {args.n}"
            )
            title = "Top Commands by Thread Count (Grouped by Full Command Line)"
        else:
            cmd = (
                f"printf \"%6s %s\\n\" \"CNT\" \"CMD\"; "
                f"awk 'NF > 0 {{print $17}}' {args.psl_file} | sort | uniq -c | sort -nrk1 | head -n {args.n}"
            )
            title = "Top Commands by Thread Count (Grouped by Base Executable)"
        run_command(title, cmd, verbose=args.v, max_line_len=args.max_line_len)

if __name__ == '__main__':
    main()

