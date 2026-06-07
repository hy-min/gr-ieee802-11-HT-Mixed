#!/home/hy/conda/envs/gnuradio/bin/python
"""
CFO (Carrier Frequency Offset) sweep loopback test

Runs the CFO loopback test across a range of CFO values to characterize
the receiver's CFO compensation performance.

Usage:
    python test_cfo_sweep.py

Exit code:
    0 if sweep completed
    1 if error
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Default test parameters
DEFAULT_CFO_VALUES = [0, 100, 250, 500, 750, 1000, 1500, 2000]
DEFAULT_DURATION = 15
DEFAULT_INTERVAL = 500


def run_cfo_test(cfo_ppm, duration, interval, ldpc=False, snr=30, sensitivity=0.01, payload_len=10):
    """Run a single CFO loopback test point and return (sent, recv, rate)."""
    script_dir = Path(__file__).resolve().parent
    test_script = script_dir / "test_cfo_loopback.py"

    python = "/home/hy/conda/envs/gnuradio/bin/python"
    cmd = [
        python,
        str(test_script),
        "--cfo-ppm", str(cfo_ppm),
        "--duration", str(duration),
        "--interval", str(interval),
        "--snr", str(snr),
        "--sensitivity", str(sensitivity),
        "--len", str(payload_len),
    ]
    if ldpc:
        cmd.append("--ldpc")

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = "/home/hy/conda/envs/gnuradio/lib"

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=duration + 30,
    )

    stdout = result.stdout
    stderr = result.stderr
    combined = stdout + "\n" + stderr

    # Parse Sent/Recv from output lines like:
    # [TEST] Sent: 15
    # [TEST] Recv: 1
    sent_match = re.search(r"\[TEST\]\s+Sent:\s*(\d+)", combined)
    recv_match = re.search(r"\[TEST\]\s+Recv:\s*(\d+)", combined)

    sent = int(sent_match.group(1)) if sent_match else 0
    recv = int(recv_match.group(1)) if recv_match else 0
    rate = recv / max(1, sent) * 100

    return sent, recv, rate, combined


def main():
    parser = argparse.ArgumentParser(
        description="CFO Sweep Loopback Test"
    )
    parser.add_argument(
        "--cfo-values",
        type=lambda s: [float(x.strip()) for x in s.split(",")],
        default=None,
        help="Comma-separated list of CFO values in ppm (default: 0,100,250,500,750,1000,1500,2000)"
    )
    parser.add_argument(
        "--duration", type=float, default=DEFAULT_DURATION,
        help=f"Test duration per point in seconds (default: {DEFAULT_DURATION})"
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Frame interval in ms (default: {DEFAULT_INTERVAL})"
    )
    parser.add_argument(
        "--snr", type=float, default=30,
        help="SNR in dB (default: 30)"
    )
    parser.add_argument(
        "--sensitivity", type=float, default=0.01,
        help="RX sensitivity (default: 0.01)"
    )
    parser.add_argument(
        "--len", type=int, default=10,
        help="Payload length in bytes (default: 10)"
    )
    parser.add_argument(
        "--ldpc", action="store_true",
        help="Enable LDPC coding"
    )
    args = parser.parse_args()

    cfo_values = args.cfo_values if args.cfo_values else DEFAULT_CFO_VALUES

    print("=" * 60)
    print("CFO Sweep Test")
    print("=" * 60)
    print(f"Duration per point: {args.duration}s")
    print(f"Frame interval: {args.interval} ms")
    print(f"CFO values (ppm): {cfo_values}")
    if args.ldpc:
        print("LDPC: enabled")
    print()

    results = []
    total_start = time.time()

    for i, cfo in enumerate(cfo_values):
        print("=" * 60)
        print(f"Testing CFO = {cfo} ppm  ({i + 1}/{len(cfo_values)})")
        print("=" * 60)

        point_start = time.time()
        sent, recv, rate, output = run_cfo_test(
            cfo_ppm=cfo,
            duration=args.duration,
            interval=args.interval,
            ldpc=args.ldpc,
            snr=args.snr,
            sensitivity=args.sensitivity,
            payload_len=args.len,
        )
        elapsed = time.time() - point_start

        # Filter and print only relevant lines (not excessive debug logs)
        relevant_lines = []
        for line in output.splitlines():
            # Keep [TEST] lines, result lines, and error lines
            if any(line.startswith(p) for p in ("[TEST]", "[HTSIG_DECODE]", "Traceback", "ERROR", "ImportError")):
                relevant_lines.append(line)
            # Also keep lines with CFO info
            elif "CFO=" in line and "ppm" in line:
                relevant_lines.append(line)
        if relevant_lines:
            print("\n".join(relevant_lines))
        else:
            # If no relevant lines, print last 20 lines for debugging
            lines = output.strip().splitlines()
            print("\n".join(lines[-20:]))

        status = "PASS" if recv > 0 else "FAIL"
        icon = "PASS" if recv > 0 else "FAIL"
        print(f"{icon} CFO={cfo:>6.0f} ppm | Sent={sent:>3} | Recv={recv:>3} | Rate={rate:>5.1f}% | Time={elapsed:.1f}s")
        print()

        results.append({
            "cfo": cfo,
            "sent": sent,
            "recv": recv,
            "rate": rate,
            "status": status,
        })

    total_elapsed = time.time() - total_start

    # Summary table
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"{'CFO (ppm)':>10} | {'Sent':>5} | {'Recv':>5} | {'Rate':>6} | Status")
    print("-" * 42)

    max_tolerable = None
    for r in results:
        icon = "PASS" if r["recv"] > 0 else "FAIL"
        print(
            f"{icon} {r['cfo']:>8.0f} | {r['sent']:>5} | {r['recv']:>5} | {r['rate']:>5.1f}% | {r['status']}"
        )
        if r["recv"] > 0:
            max_tolerable = r["cfo"]

    print()
    if max_tolerable is not None:
        print(f"Maximum tolerable CFO: {max_tolerable:.0f} ppm")
    else:
        print("Maximum tolerable CFO: 0 ppm (no frames decoded at any tested CFO)")

    print(f"Total elapsed time: {total_elapsed:.1f}s")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
