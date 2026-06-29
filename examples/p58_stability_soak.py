#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 58 Task 6: 30-min stability soak with all 5 pivots applied.
3 runs x 35s + 2 x 5min idle = ~20 min total.
Output: /tmp/p58_t6_soak_summary.txt
"""
import subprocess
import re
import time
import statistics
import os
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("/tmp/p58_soak")
LOG_DIR.mkdir(exist_ok=True)
SUMMARY = Path("/tmp/p58_t6_soak_summary.txt")

CMD_TEMPLATE = [
    "taskset", "--cpu-list", "0-1",
    "timeout", "110",
    "/home/hy/conda/envs/gnuradio/bin/python",
    "/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py",
    "--freq", "5890",
    "--tx-gain", "20",
    "--rx-scale", "45",
    "--duration", "35",
    "--warmup", "60",
]
ENV = {
    **os.environ,
    "LD_PRELOAD": "/home/hy/gr-ieee802-11/wrap_rpc2.so",
    "PYTHONPATH": "/home/hy/gr-ieee802-11/build/python/bindings:/home/hy/gr-ieee802-11/python:/home/hy/gr-ieee802-11/examples",
    "LD_LIBRARY_PATH": "",
    "IEEE80211_LSIG_RATE_FORCE": "0xD",
    "IEEE80211_LLTF_OFFSET_CORRECT": "14",
    "IEEE80211_TIMING_OFFSET_APPLY": "1",
}


def run_one(run_id):
    log_path = LOG_DIR / f"run_{run_id}.log"
    print(f"[SOAK] Run {run_id} starting at {datetime.now().isoformat()}")
    with open(log_path, "w") as f:
        proc = subprocess.run(CMD_TEMPLATE, stdout=f, stderr=subprocess.STDOUT, env=ENV)
    content = log_path.read_text()
    snrs = [float(m.group(1)) for m in re.finditer(r"avg_snr=([\d.]+)", content)]
    ht_sigs = len(re.findall(r"HT_SIG_CAND", content))
    lsig_oks = len(re.findall(r"LSIG_DECODE.*OK", content))
    overflows = sum(int(m.group(1)) for m in re.finditer(r"(\d+) overflows", content))
    avg_snr = statistics.mean(snrs) if snrs else 0.0
    return {
        "run": run_id,
        "time": datetime.now().isoformat(),
        "avg_snr": round(avg_snr, 2),
        "ht_sig_cand": ht_sigs,
        "lsig_ok": lsig_oks,
        "overflows": overflows,
    }


def main():
    # Truncate summary file
    SUMMARY.write_text("")

    results = []
    for i in range(1, 4):
        r = run_one(i)
        results.append(r)
        with open(SUMMARY, "a") as f:
            f.write(f"Run {r['run']} ({r['time']}):\n")
            f.write(f"  avg_snr={r['avg_snr']}\n")
            f.write(f"  HT_SIG_CAND={r['ht_sig_cand']}\n")
            f.write(f"  LSIG_OK={r['lsig_ok']}\n")
            f.write(f"  overflows_total={r['overflows']}\n")
        if i < 3:
            idle_start = datetime.now().isoformat()
            print(f"[SOAK] Idle 5 min starting at {idle_start}")
            with open(SUMMARY, "a") as f:
                f.write(f"\nIdle 5 min started at: {idle_start}\n")
            time.sleep(5 * 60)
            idle_end = datetime.now().isoformat()
            print(f"[SOAK] Idle 5 min ended at {idle_end}")
            with open(SUMMARY, "a") as f:
                f.write(f"Idle 5 min ended at: {idle_end}\n\n")

    # Stability analysis
    snrs = [r["avg_snr"] for r in results]
    mean_snr = statistics.mean(snrs)
    std_snr = statistics.stdev(snrs) if len(snrs) > 1 else 0
    cv = std_snr / mean_snr if mean_snr > 0 else 0

    if cv < 0.20:
        verdict = "STABLE (cv < 0.20)"
    elif cv < 0.50:
        verdict = "MARGINAL (cv 0.20-0.50)"
    else:
        verdict = "UNSTABLE (cv > 0.50)"

    with open(SUMMARY, "a") as f:
        f.write(f"\n=== STABILITY ANALYSIS ===\n")
        f.write(f"avg_snr mean: {mean_snr:.4f}\n")
        f.write(f"avg_snr std: {std_snr:.4f}\n")
        f.write(f"avg_snr range: {min(snrs):.4f} - {max(snrs):.4f}\n")
        f.write(f"coefficient of variation (CV): {cv:.3f}\n")
        f.write(f"VERDICT: {verdict}\n")
    print(f"[SOAK] Done. {verdict}. See {SUMMARY}")


if __name__ == "__main__":
    main()