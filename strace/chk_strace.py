#!/usr/bin/env python3

import argparse
import re
from collections import defaultdict, Counter
from collections import defaultdict

class Color:
    RED = '\033[91m'
    YELLOW = '\033[93m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'

def parse_strace_line(line):
    pattern = (
        r'^(?:(?P<time>\d{2}:\d{2}:\d{2}\.\d+)\s+)?'
        r'(?P<syscall>\w+)\((?P<args>.*?)\)\s+='
        r'\s*(?P<ret>.+?)'
        r'(?:\s+<(?P<duration>[\d\.]+)>)?'
        r'\s*$'
    )
    match = re.match(pattern, line)
    if not match:
        return None
    return {
        'timestamp': match.group('time'),
        'syscall': match.group('syscall'),
        'args': match.group('args'),
        'return': match.group('ret').strip(),
        'duration': float(match.group('duration')) if match.group('duration') else None
    }

def analyze_strace_file(filename):

    # Number of calls for each syscall
    syscall_counter = Counter()
    # Total duration of each syscall
    syscall_time = defaultdict(float)
    # List of individual durations per syscall
    syscall_durations = defaultdict(list)

    errors = Counter()
    slow_calls = []

    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            parsed = parse_strace_line(line)
            if not parsed:
                continue
            syscall = parsed['syscall']
            syscall_counter[syscall] += 1
            if parsed['duration']:
                dur = parsed['duration']
                syscall_time[syscall] += dur
                syscall_durations[syscall].append(dur)
                slow_calls.append((dur, line.strip()))
            if parsed['return'].startswith('-1'):
                err_match = re.search(r'-1\s+(\w+)', parsed['return'])
                if err_match:
                    err_code = err_match.group(1)
                    errors[err_code] += 1

    return syscall_counter, syscall_time, syscall_durations, slow_calls, errors

def find_anomalies(total_time, durations, errors, time_dominance=0.5,
                   slow_ratio=10.0, error_threshold=50, verbose=False, debug=False):
    total_runtime = sum(total_time.values())
    flagged = {
        'dominant': set(),
        'outliers': set(),
        'error_types': []
    }

    if not total_runtime:
        return flagged, total_runtime

    # Set dominant for those syscalls consumed more time than dominance threshold 
    for syscall, total in total_time.items():
        if debug:
            print(f"[DEBUG] time: {syscall}: total {total} / total_runtime {total_runtime} = {total / total_runtime}  >= time_dominance {time_dominance}")
        if total / total_runtime >= time_dominance:
            flagged['dominant'].add(syscall)

    # Set outliner for those syscalls consumed time duration more than duration threshold.
    for syscall, times in durations.items():
        if len(times) < 5:
            continue
        avg = sum(times) / len(times)
        max_dur = max(times)
        if debug:
            print(f"[DEBUG] durating: {syscall}: max_dur {max_dur} > avg {avg} * slow_ratio {slow_ratio}")
        if max_dur > avg * slow_ratio:
            flagged['outliers'].add(syscall)

    # Highlight the error count over threshold
    for err, count in errors.items():
        if count >= error_threshold:
            flagged['error_types'].append((err, count))

    return flagged, total_runtime

def print_summary(counter, total_time, durations, slow_calls, errors,
                  top_n=10, slow_ratio=10.0, time_dominance=0.5,
                  flagged=None, verbose=False, debug=False):
    flagged = flagged or {'outliers': set(), 'dominant': set()}

    print(f"\n🔢 Top {top_n} most frequent syscalls:")
    for syscall, count in counter.most_common(top_n):
        print(f"{syscall:<20} {count} calls")

    if total_time:
        print(f"\n⏱️  Top {top_n} most time-consuming syscalls:")
        total_runtime = sum(total_time.values())
        print(f"Total time: {total_runtime:6f}")

        for syscall, t in sorted(total_time.items(), key=lambda x: x[1], reverse=True)[:top_n]:
            avg = t / len(durations[syscall])
            ratio = t / total_runtime if total_runtime else 0

            line = (
                f"{syscall:<20} total: {t:.6f}s  avg: {avg:.6f}s  calls: {len(durations[syscall])}"
            )

            if verbose:
                line += f"  (dominance ratio: {ratio:.9f})"  # Full 9 decimal precision for diagnosis

            color = None
            if syscall in flagged['dominant']:
                ratio = t / total_runtime
                if ratio >= time_dominance:
                    color = Color.RED
                elif ratio >= time_dominance / 2:
                    color = Color.YELLOW
                else:
                    color = None
            else:
                color = None

            if color:
                print(color + line + Color.RESET)
            else:
                print(line)

    if slow_calls:
        print(f"\n🐢 Top {top_n} slowest individual syscall calls:")
        top_slowest = sorted(slow_calls, reverse=True)[:top_n]
        for dur, line in top_slowest:
            syscall_match = re.match(r'(?:\d{2}:\d{2}:\d{2}\.\d+\s+)?(\w+)\(', line)
            if syscall_match:
                syscall_name = syscall_match.group(1)
                avg = sum(durations[syscall_name]) / len(durations[syscall_name])
                severity_ratio = dur / avg if avg > 0 else float('inf')

                if severity_ratio >= slow_ratio:
                    color = Color.RED
                elif severity_ratio >= slow_ratio / 2:
                    color = Color.YELLOW
                else:
                    color = None

                verbose_note = f"  (outlier ratio: {severity_ratio:.2f})" if verbose else ""

                line_output = f"{dur:.6f}s  -> {line.strip()}{verbose_note}"

                if color:
                    print(color + line_output + Color.RESET)
                else:
                    print(line_output)
            else:
                print(f"{dur:.6f}s  -> {line.strip()}")

    if errors:
        print("\n❗ Syscall Errors:")
        for err, count in errors.most_common():
            print(f"{err:<20} {count} errors")

def print_anomalies(flagged, total_time, total_runtime, durations,
                    slow_ratio=10.0, time_dominance=0.5,
                    verbose=False, debug=False):
    print("\n⚠️  Potential Issues:")

    if not total_runtime:
        print(Color.RED + "  ⛔ No syscall durations found. Did you use `strace -T`?" + Color.RESET)
        return

    # High total-time consumers (dominants)
    for syscall in sorted(flagged['dominant']):
        t = total_time[syscall]
        ratio = t / total_runtime

        if ratio >= time_dominance:
            color = Color.RED
        elif ratio >= time_dominance / 2:
            color = Color.YELLOW
        else:
            color = None

        if color:
            print(color + f"  ⏱️ [Dominant] {syscall} consumed {t:.3f}s ({ratio:.1%} of runtime)" + Color.RESET)
        else:
            print(f"  ⏱️ [Dominant] {syscall} consumed {t:.3f}s ({ratio:.1%} of runtime)")

        if verbose:
            avg = t / len(durations[syscall])
            print(f"     avg: {avg:.6f}s  calls: {len(durations[syscall])}")

    # Spikey syscalls (outliers)
    for syscall in sorted(flagged['outliers'], reverse=True):
        times = durations[syscall]
        avg = sum(times) / len(times)
        max_dur = max(times)
        severity_ratio = max_dur / avg if avg > 0 else float('inf')

        if severity_ratio >= slow_ratio:
            color = Color.RED
        elif severity_ratio >= slow_ratio / 2:
            color = Color.YELLOW
        else:
            color = None

        if color:
            print(color + f"  🐌 [Outlier] {syscall} outlier: max {max_dur:.6f}s vs avg {avg:.6f}s" + Color.RESET)
        else:
            print(f"  🐌 [Outlier] {syscall} outlier: max {max_dur:.6f}s vs avg {avg:.6f}s")

        if verbose:
            print(f"     {len(times)} calls, total {sum(times):.6f}s")

    # Error-heavy syscalls
    for err, count in flagged['error_types']:
        print(Color.MAGENTA + f"  ❗ High error rate: {err} occurred {count} times" + Color.RESET)

# Syscall category mapping
SYSCALL_CATEGORIES = {
    'read': 'File IO', 'write': 'File IO', 'open': 'File IO', 'close': 'File IO',
    'lseek': 'File IO', 'fsync': 'File IO', 'ioctl': 'File IO',
    'clone': 'Process', 'fork': 'Process', 'execve': 'Process', 'wait4': 'Process',
    'kill': 'Process',
    'pipe': 'IPC', 'pipe2': 'IPC', 'socket': 'IPC', 'connect': 'IPC',
    'sendto': 'IPC', 'recvfrom': 'IPC', 'sendmsg': 'IPC', 'recvmsg': 'IPC',
    'rt_sigaction': 'Signal', 'rt_sigprocmask': 'Signal', 'rt_sigreturn': 'Signal',
    'nanosleep': 'Timing', 'clock_gettime': 'Timing',
    # Fallback if not found in map
}

def categorize_syscalls(total_time, counter):
    category_time = defaultdict(float)
    category_count = defaultdict(int)

    for syscall, total in total_time.items():
        category = SYSCALL_CATEGORIES.get(syscall, 'Other')
        category_time[category] += total
        category_count[category] += counter[syscall]

    return category_time, category_count

def calculate_percentiles(durations, percentile_list=(50, 90, 95, 99)):
    from numpy import percentile

    result = {}
    for syscall, times in durations.items():
        if len(times) >= 5:
            result[syscall] = {
                f'p{p}': percentile(times, p) for p in percentile_list
            }
    return result

def print_extended_diagnostics(counter, total_time, durations, verbose=False):
    from math import isclose

    print("\n🧠 Extended Diagnostics:")

    total_runtime = sum(total_time.values())
    if not total_runtime:
        print("  No runtime data available.")
        return

    # 🗂️ Syscall Category Summary
    category_time, category_count = categorize_syscalls(total_time, counter)
    print("\n📊 Syscall Categories (by total time):")
    for category, t in sorted(category_time.items(), key=lambda x: x[1], reverse=True):
        count = category_count[category]
        ratio = t / total_runtime
        print(f"  {category:<10}  {count:>6} calls  total: {t:.6f}s  ({ratio:.2%} of runtime)")

    # 📈 Percentile Stats per Syscall
    percentiles = calculate_percentiles(durations)
    if percentiles:
        print("\n📈 Duration Percentiles (for syscalls with ≥5 samples):")
        for syscall, stats in sorted(percentiles.items(), key=lambda x: x[1].get('p99', 0), reverse=True):
            stats_line = '  '.join(f"{k}: {v:.6f}s" for k, v in stats.items())
            print(f"  {syscall:<20} {stats_line}")
    else:
        print("\n📈 No syscalls with enough data to calculate percentiles.")

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Analyze strace output to find performance bottlenecks, slow syscalls, "
            "and  frequent errors.\n\n"
            "  ⏱️  Dominant = syscall with high total runtime\n"
            "  🐌 Outlier = syscall with unusually slow calls\n\n"
            "Severity:\n"
            "  🔴 Red   = above the defined threshold\n"
            "  🟡 Yellow = above half the defined threshold\n"
        )
    )

    parser.add_argument(
        "file",
        help="Path to strace output file (use strace -T to include syscall durations)"
    )

    parser.add_argument(
        "-t", "--top", type=int, default=10,
        help="Number of top syscalls to show in each summary section (default: 10)"
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show extra details (e.g. avg time, call counts) in diagnostics"
    )

    parser.add_argument(
        "--time-dominance", type=float, default=0.5,
        help=(
            "Flag syscalls as dominant if they account for more than this fraction "
            "of total runtime (default: 0.5). Yellow = >threshold/2, Red = >threshold"
        )
    )

    parser.add_argument(
        "--slow-ratio", type=float, default=10.0,
        help=(
            "Flag syscalls as outliers if their slowest call exceeds average * ratio "
            "(default: 10.0). Yellow = >avg*(ratio/2), Red = >avg*ratio"
        )
    )

    parser.add_argument(
        "--error-threshold", type=int, default=50,
        help="Highlight error types with this many or more occurrences (default: 50)"
    )

    parser.add_argument(
        "-d", "--debug", action="store_true",
        help="Enable debug mode (internal diagnostic prints)"
    )

    args = parser.parse_args()

    counter, total_time, durations, slow_calls, errors = analyze_strace_file(args.file)

    # 👇 First: analyze
    flagged, total_runtime = find_anomalies(
        total_time, durations, errors,
        time_dominance=args.time_dominance,
        slow_ratio=args.slow_ratio,
        error_threshold=args.error_threshold,
        verbose=args.verbose,
        debug=args.debug
    )

    # 👇 Then: print summary
    print_summary(
        counter,
        total_time,
        durations,
        slow_calls,
        errors,
        top_n=args.top,
        slow_ratio=args.slow_ratio,
        flagged=flagged,
        verbose=args.verbose,
        debug=args.debug
    )

    # 👇 Finally: print diagnostics at the end
    print_anomalies(
        flagged,
        total_time,
        total_runtime,
        durations,
        slow_ratio=args.slow_ratio,
        time_dominance=args.time_dominance,
        verbose=args.verbose,
        debug=args.debug
    )

    if args.verbose:
        print_extended_diagnostics(
            counter,
            total_time,
            durations,
            verbose=args.verbose
        )


if __name__ == "__main__":
    main()
