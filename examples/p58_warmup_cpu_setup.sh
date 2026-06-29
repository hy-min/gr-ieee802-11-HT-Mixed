#!/bin/bash
# Phase 58 Task 2: Switch CPU governor to performance and report cpuset topology
# Run BEFORE test_usrp_minimal_loopback.py

set -e

echo "=== Current CPU governor ==="
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sort -u || echo "no cpufreq"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: must run as root (sudo) to change governor"
    echo "Run: sudo $0"
    exit 1
fi

echo
echo "=== Switching all CPUs to performance governor ==="
for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance > "$cpu"
done

echo
echo "=== After switch ==="
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sort -u

echo
echo "=== CPU topology ==="
lscpu | grep -E "^CPU\(s\)|Thread|Core|Socket|NUMA" | head -10

echo
echo "=== Cpuset isolation status ==="
cat /sys/devices/system/cpu/isolated 2>/dev/null || echo "(no isolated cpus)"

echo
echo "=== Recommended cpuset for UHD callback ==="
echo "  - UHD callback thread is high-priority SCHED_FIFO"
echo "  - Pin to cpu0 (avoid cgroup-affine cpus)"
echo "  - Or isolate cpu2 via: isolcpus=2 boot param (requires reboot)"

echo
echo "Done. Now run:"
echo "  taskset --cpu-list 0 ./test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 35"
