#!/bin/bash

# Check if an argument is provided
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 input_file"
  exit 1
fi

# Define the input file from the first argument
input_file="$1"

# Check if the input file exists and is readable
if [ ! -r "$input_file" ]; then
  echo "Error: File '$input_file' does not exist or is not readable."
  exit 1
fi

# Print header
printf "%10s %10s %10s %10s %10s\n" "Count" "Pages" "Kbytes" "Order" "Module"

# Use awk to process the file and extract the required information
awk '
# Function to extract module name from a line
function extract_module(line) {
  if (match(line, /\[([a-zA-Z0-9_]+)\]/, matches)) {
    return matches[1]
  }
  return ""
}

/Page allocated/ {
  in_allocation = 1
  # Extract order and calculate number of pages
  if (match($0, /order ([0-9]+)/, matches)) {
    order = matches[1]
    num_pages = 2^order
  }
}

/^\s*$/ {
  in_allocation = 0
  allocation_counted = 0
}

in_allocation && !allocation_counted {
  module = extract_module($0)
  if (module != "") {
    allocations[module, order]["count"]++
    allocation_counted = 1
    allocations[module, order]["pages"] += num_pages
  }
}

END {
  for (key in allocations) {
    split(key, arr, SUBSEP)
    module = arr[1]
    order = arr[2]
    printf "%10d %10d %10d %10d %-10s\n", \
	    allocations[key]["count"], \
	    allocations[key]["pages"], \
	    allocations[key]["pages"] * 4, \
	    order, \
	    module
  }
}
' "$input_file" | sort -k5,5 -k4,4n

