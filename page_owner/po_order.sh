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

# AWK script to process the file
awk '/^Page/{
    sum += 2^$5
    arr[$5] += 2^$5
}
END {
    for (i in arr) {
        printf "Order %3s %10d pages (%11d KB)\n", i, arr[i], arr[i] * 4
    }
    printf "Total %14d pages (%11d KB)\n", sum, sum * 4
}' "$input_file"

