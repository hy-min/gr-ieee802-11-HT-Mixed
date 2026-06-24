#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 35 offline HT-SIG diagnostic analyzer.

Parses [HTSIG_BIN_DUMP] / [HTSIG_PILOT_DUMP] lines from a USRP log, computes
per-frame statistics (HT-SIG0 vs HT-SIG1 pilot diff, pilot coherence within
symbol, |H| distribution, pilots vs data SCs), and identifies which layer
the HT-SIG viterbi failure lives in.

Dump format:
  [HTSIG_BIN_DUMP]   counter=4 frame=N htsig0=[a+bi,...] htsig1=[a+bi,...]
  [HTSIG_PILOT_DUMP] counter=4 frame=N htsig0_pilots=arg[a,b,c,d]
                                    htsig1_pilots=arg[a,b,c,d]
Args may be nan (safe_arg sentinel for low-|H| pilots).

Usage:
    python examples/p35_htsig_analyze.py /tmp/p35a_usrp.log
"""
import math
import re
import sys
import numpy as np


# Pilot SC indices in the 52-subcarrier HT-SIG layout.
# kScIndex52 maps the LAST 4 bins (indices 48..51) to SC positions
# -21, -7, 7, 21 — these are the HT-SIG pilot subcarriers.
PILOT_BINS = [48, 49, 50, 51]
DATA_BINS = [i for i in range(52) if i not in PILOT_BINS]

# Match a+bi / a-bi / a-bi / a (real only). Tolerates optional sign on either side.
COMPLEX_TOKEN_RE = re.compile(
    r"^([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)([-+]\d*\.?\d+(?:[eE][-+]?\d+)?)i$"
)
REAL_TOKEN_RE = re.compile(r"^([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)$")
# nan / inf sentinels (safe_arg / safe_div fallbacks)
NAN_TOKENS = {"nan", "inf", "-inf", "+inf"}


def parse_complex_token(tok):
    """Parse a single 'a+bi' / 'nan+0.000i' / 'nan' / float token."""
    tok = tok.strip()
    if not tok:
        return complex(float("nan"), float("nan"))
    low = tok.lower()
    if low in NAN_TOKENS:
        return complex(float("nan"), float("nan"))
    m = COMPLEX_TOKEN_RE.match(tok)
    if m:
        return complex(float(m.group(1)), float(m.group(2)))
    m = REAL_TOKEN_RE.match(tok)
    if m:
        return complex(float(m.group(1)), 0.0)
    # Last resort: try Python's complex()
    try:
        return complex(tok.replace("i", "j"))
    except ValueError:
        return complex(float("nan"), float("nan"))


def parse_complex_csv(s):
    """Parse 'a+bi,a+bi,...' into numpy complex128 array (NaN-tolerant)."""
    toks = [t for t in s.split(",") if t.strip()]
    return np.array([parse_complex_token(t) for t in toks], dtype=np.complex128)


def parse_pilot_csv(*args):
    """Parse 4 pilot arg values; non-numeric -> NaN."""
    out = []
    for a in args:
        a = a.strip()
        try:
            out.append(float(a))
        except ValueError:
            out.append(float("nan"))
    return np.array(out, dtype=np.float64)


# Compiled line matchers. Note: HT-SIG bins/dump are on a single line in the
# log, so a non-greedy match up to the closing bracket is sufficient.
HTSIG_BIN_RE = re.compile(
    r"\[HTSIG_BIN_DUMP\]\s+counter=(\d+)\s+frame=(\d+)\s+"
    r"htsig0=\[(.*?)\]\s+htsig1=\[(.*?)\]"
)
HTSIG_PILOT_RE = re.compile(
    r"\[HTSIG_PILOT_DUMP\]\s+counter=(\d+)\s+frame=(\d+)\s+"
    r"htsig0_pilots=arg\[(.*?)\]\s+htsig1_pilots=arg\[(.*?)\]"
)


def circular_diff(a, b):
    """Per-element wrapped diff of two arg arrays in [-pi, pi]."""
    d = a - b
    return (d + np.pi) % (2 * np.pi) - np.pi


def pilot_coherence_std(p4):
    """Std of 4 pilot phases (circular-safe: just use linear std if in range)."""
    valid = p4[~np.isnan(p4)]
    if valid.size < 2:
        return float("nan")
    return float(np.std(valid))


def main():
    if len(sys.argv) < 2:
        print("Usage: p35_htsig_analyze.py <usrp_log>", file=sys.stderr)
        sys.exit(1)
    log_path = sys.argv[1]

    bin_frames = {}     # frame_id -> {"htsig0": np.ndarray, "htsig1": np.ndarray}
    pilot_frames = {}   # frame_id -> {"htsig0_pilots": np.ndarray, ...}

    parse_errors = 0
    with open(log_path, "r", errors="replace") as f:
        for line in f:
            m = HTSIG_BIN_RE.search(line)
            if m:
                fid = int(m.group(2))
                try:
                    h0 = parse_complex_csv(m.group(3))
                    h1 = parse_complex_csv(m.group(4))
                except Exception:
                    parse_errors += 1
                    continue
                if h0.size != 52 or h1.size != 52:
                    parse_errors += 1
                    continue
                bin_frames[fid] = {"htsig0": h0, "htsig1": h1}
                continue
            m = HTSIG_PILOT_RE.search(line)
            if m:
                fid = int(m.group(2))
                pilot_frames[fid] = {
                    "htsig0_pilots": parse_pilot_csv(*m.group(3).split(",")),
                    "htsig1_pilots": parse_pilot_csv(*m.group(4).split(",")),
                }

    n_bin = len(bin_frames)
    n_pilot = len(pilot_frames)
    print(f"[P35] Parsed {log_path}")
    print(f"[P35]   BIN dumps:   {n_bin} frames")
    print(f"[P35]   PILOT dumps: {n_pilot} frames")
    if parse_errors:
        print(f"[P35]   Parse errors: {parse_errors}")
    if n_bin == 0 and n_pilot == 0:
        print("[P35] No HTSIG dumps found — check that env-vars were enabled")
        return

    # ---- Per-frame: HT-SIG0 vs HT-SIG1 pilot diff ----
    if n_pilot > 0:
        diffs = []
        per_frame_summary = []
        for fid in sorted(pilot_frames.keys()):
            h0 = pilot_frames[fid]["htsig0_pilots"]
            h1 = pilot_frames[fid]["htsig1_pilots"]
            d = circular_diff(h1, h0)
            diffs.append(d)
            per_frame_summary.append((fid, d, pilot_coherence_std(h0),
                                      pilot_coherence_std(h1)))
        diffs = np.array(diffs)  # shape (N, 4)

        print("\n[P35] ===== HT-SIG1 - HT-SIG0 pilot phase diff =====")
        print(f"[P35]   N frames: {len(diffs)}")
        # Per-pilot-position stats
        labels = ["pilot@-21", "pilot@-7", "pilot@+7", "pilot@+21"]
        for i, lab in enumerate(labels):
            col = diffs[:, i]
            valid = col[~np.isnan(col)]
            if valid.size:
                print(f"[P35]   {lab}: mean={np.mean(valid):+.3f}rad  "
                      f"std={np.std(valid):.3f}rad  "
                      f"max|diff|={np.max(np.abs(valid)):.3f}rad")
        # Aggregate across all pilots
        flat = diffs.flatten()
        flat = flat[~np.isnan(flat)]
        if flat.size:
            print(f"[P35]   ALL pilots pooled: mean={np.mean(flat):+.3f}rad  "
                  f"std={np.std(flat):.3f}rad  "
                  f"max|diff|={np.max(np.abs(flat)):.3f}rad")

        # Per-frame coherence (std of 4 pilots within each symbol)
        print("\n[P35] ===== Pilot coherence (std of 4 pilots within symbol) =====")
        print(f"[P35]   frame  htsig0_std  htsig1_std  mean_|h0-h1|")
        for fid, d, c0, c1 in per_frame_summary:
            valid_d = d[~np.isnan(d)]
            mean_abs = float(np.mean(np.abs(valid_d))) if valid_d.size else float("nan")
            print(f"[P35]   {fid:>5}  {c0:>9.3f}  {c1:>9.3f}  {mean_abs:>10.3f}")

    # ---- BIN dump: |H| distribution, pilots vs data ----
    if n_bin > 0:
        all_h0 = []
        all_h1 = []
        pilot_h0_mag = []
        pilot_h1_mag = []
        data_h0_mag = []
        data_h1_mag = []
        for fid in sorted(bin_frames.keys()):
            h0 = bin_frames[fid]["htsig0"]
            h1 = bin_frames[fid]["htsig1"]
            all_h0.append(np.abs(h0))
            all_h1.append(np.abs(h1))
            pilot_h0_mag.append(np.abs(h0[PILOT_BINS]))
            pilot_h1_mag.append(np.abs(h1[PILOT_BINS]))
            data_h0_mag.append(np.abs(h0[DATA_BINS]))
            data_h1_mag.append(np.abs(h1[DATA_BINS]))

        all_h0 = np.concatenate(all_h0)
        all_h1 = np.concatenate(all_h1)
        pilot_h0_mag = np.concatenate(pilot_h0_mag)
        pilot_h1_mag = np.concatenate(pilot_h1_mag)
        data_h0_mag = np.concatenate(data_h0_mag)
        data_h1_mag = np.concatenate(data_h1_mag)

        print("\n[P35] ===== |bin| distribution across all BIN dumps =====")
        # The dump is RAW d_early_eqsym (post-CFO+SFO+delta), not divided by H.
        # Values are typically tens-to-hundreds on USRP.
        for label, arr in [
            ("htsig0 ALL bins", all_h0),
            ("htsig1 ALL bins", all_h1),
            ("htsig0 PILOTS", pilot_h0_mag),
            ("htsig1 PILOTS", pilot_h1_mag),
            ("htsig0 DATA SCs", data_h0_mag),
            ("htsig1 DATA SCs", data_h1_mag),
        ]:
            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                continue
            print(f"[P35]   {label:>18}: mean={np.mean(arr):7.2f}  "
                  f"median={np.median(arr):7.2f}  "
                  f"std={np.std(arr):6.2f}  "
                  f"min={np.min(arr):7.2f}  "
                  f"max={np.max(arr):7.2f}")

        # Per-frame summary
        print("\n[P35] ===== Per-frame |bin| pilots vs data =====")
        print(f"[P35]   frame  |h0_pilot|_mean  |h0_data|_mean  "
              f"|h1_pilot|_mean  |h1_data|_mean  pilot/data_h0")
        for fid in sorted(bin_frames.keys()):
            h0 = bin_frames[fid]["htsig0"]
            h1 = bin_frames[fid]["htsig1"]
            p0 = np.abs(h0[PILOT_BINS]).mean()
            d0 = np.abs(h0[DATA_BINS]).mean()
            p1 = np.abs(h1[PILOT_BINS]).mean()
            d1 = np.abs(h1[DATA_BINS]).mean()
            ratio = p0 / d0 if d0 > 1e-9 else float("nan")
            print(f"[P35]   {fid:>5}  {p0:>14.2f}  {d0:>13.2f}  "
                  f"{p1:>14.2f}  {d1:>13.2f}  {ratio:>13.3f}")

    # ---- Layer diagnosis hint ----
    print("\n[P35] ===== Layer diagnosis hint =====")
    if n_pilot > 0:
        flat = np.concatenate([d for fid in sorted(pilot_frames.keys())
                                for d in [circular_diff(pilot_frames[fid]["htsig1_pilots"],
                                                        pilot_frames[fid]["htsig0_pilots"])]])
        flat = flat[~np.isnan(flat)]
        if flat.size:
            std = float(np.std(flat))
            max_abs = float(np.max(np.abs(flat)))
            if std > 2.0:
                layer = "H52 estimation (pilots random across [-pi,pi])"
                path = "Task 7a — re-investigate H52 path / L-LTF0 sample timing"
            elif std > 0.5:
                layer = "per-symbol phase drift (pilots clustered but spread)"
                path = "Task 7c — per-symbol H update from HT-SIG pilots"
            elif max_abs < 0.5:
                layer = "downstream (pilots coherent + small diff)"
                path = "Task 7d — viterbi threshold / metric / CRC mask"
            else:
                layer = "mixed / quantized grid (e.g., 64-PSK residual)"
                path = "Task 7b — improve δ estimation / ML on 64-PSK grid"
            print(f"[P35]   Pilot-diff std={std:.3f}rad  max|d|={max_abs:.3f}rad")
            print(f"[P35]   -> Layer: {layer}")
            print(f"[P35]   -> Path:  {path}")

    print("\n[P35] Done.")


if __name__ == "__main__":
    main()