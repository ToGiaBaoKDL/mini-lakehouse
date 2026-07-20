#!/bin/sh

output="$(java -jar /deployments/polaris-admin-tool.jar "$@" 2>&1)"
status=$?
printf '%s\n' "$output"

if [ "$status" -eq 3 ] && printf '%s' "$output" | grep -q "already been bootstrapped"; then
    printf '%s\n' "Polaris realm already exists; bootstrap contract is satisfied."
    exit 0
fi

exit "$status"
