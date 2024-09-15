#!/usr/bin/env python3

import sys

# Check if an argument is provided
if len(sys.argv) != 2:
    print("Usage: ./convert_timestamp.py <timestamp_in_seconds>")
    sys.exit(1)

# Get the timestamp from the argument (as a string) and convert it to an integer
try:
    timestamp = int(sys.argv[1])
except ValueError:
    print("Error: The argument must be an integer representing seconds.")
    sys.exit(1)

# Convert to days, hours, minutes, and seconds
days = timestamp // (24 * 3600)
timestamp %= (24 * 3600)
hours = timestamp // 3600
timestamp %= 3600
minutes = timestamp // 60
seconds = timestamp % 60

# Display the result
print(f"{days} days, {hours} hours, {minutes} minutes, {seconds} seconds")
