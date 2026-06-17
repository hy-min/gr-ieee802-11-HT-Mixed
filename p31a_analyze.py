#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 31 31a: analyze /tmp/p31a_diagnostic.csv for L-LTF0 timing pathology.

Goal: verify Phase 30 hypothesis that 1-2 sample offset causes H52 pathology
on USRP e2e frames.

Expected baseline (from loopback, Tasks 2/3):
  - splitter current_idx: 63 (LTS0 sample offset within frame window)
  - splitter lts1_expected_rel: 143 (= 63 + 80, LTS1 follow position)
  - equalizer lts0_bin: 0 (FFT-block index of LTS0)
  - equalizer lts1_bin: 1 (FFT-block index of LTS1, = lts0_bin + 1)

If USRP frames show:
  - current_idx != 63 OR lts0_bin != 0 in pathological frames
  → HYPOTHESIS CONFIRMED (timing offset is the issue)

If USRP frames show:
  - current_idx == 63 AND lts0_bin == 0 even in pathological frames
  → HYPOTHESIS REFUTED (timing is correct, issue is elsewhere)
"""
import csv
import sys
from collections import Counter

CSV_PATH = "/tmp/p31a_diagnostic.csv"

# Expected baseline values
EXPECTED_SPLITTER_CURRENT_IDX = 63
EXPECTED_EQ_LTS0_BIN = 0


def main():
    rows = []
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            try:
                splitter_lts0 = int(row["splitter_lts0_idx"]) if row["splitter_lts0_idx"] else None
                splitter_lts1 = int(row["splitter_lts1_idx"]) if row["splitter_lts1_idx"] else None
                eq_lts0 = int(row["eq_lts0_idx"]) if row["eq_lts0_idx"] else None
                eq_lts1 = int(row["eq_lts1_idx"]) if row["eq_lts1_idx"] else None
                avg_snr = float(row["avg_snr_lsig"]) if row["avg_snr_lsig"] else 0.0
                lsig_ok = int(row["lsig_ok"]) if row["lsig_ok"] else 0
            except ValueError:
                continue
            if splitter_lts0 is None and eq_lts0 is None:
                continue
            rows.append({
                "splitter_lts0": splitter_lts0,
                "splitter_lts1": splitter_lts1,
                "eq_lts0": eq_lts0,
                "eq_lts1": eq_lts1,
                "avg_snr": avg_snr,
                "lsig_ok": lsig_ok,
            })

    print(f"Analyzed {len(rows)} frames with dump records")

    if not rows:
        print("\n[VERDICT] No data — check env-var gating or run test_lltf_timing_diagnostic.py")
        return 1

    # Splitter current_idx distribution
    splitter_idx_rows = [r for r in rows if r["splitter_lts0"] is not None]
    if splitter_idx_rows:
        splitter_idxs = [r["splitter_lts0"] for r in splitter_idx_rows]
        print(f"\nSplitter LTS0 sample-offset distribution (expected = {EXPECTED_SPLITTER_CURRENT_IDX}):")
        print(f"  n={len(splitter_idxs)}, min={min(splitter_idxs)}, max={max(splitter_idxs)}")
        for idx, n in sorted(Counter(splitter_idxs).items()):
            marker = " <-- expected" if idx == EXPECTED_SPLITTER_CURRENT_IDX else ""
            print(f"  current_idx={idx}: {n} frames{marker}")

    # Equalizer lts0_bin distribution
    eq_idx_rows = [r for r in rows if r["eq_lts0"] is not None]
    if eq_idx_rows:
        eq_idxs = [r["eq_lts0"] for r in eq_idx_rows]
        print(f"\nEqualizer LTS0 bin distribution (expected = {EXPECTED_EQ_LTS0_BIN}):")
        print(f"  n={len(eq_idxs)}, min={min(eq_idxs)}, max={max(eq_idxs)}")
        for idx, n in sorted(Counter(eq_idxs).items()):
            marker = " <-- expected" if idx == EXPECTED_EQ_LTS0_BIN else ""
            print(f"  lts0_bin={idx}: {n} frames{marker}")

    # Pathology split
    clean = [r for r in rows if r["avg_snr"] < 100]
    borderline = [r for r in rows if 100 <= r["avg_snr"] < 1000]
    pathological = [r for r in rows if r["avg_snr"] >= 1000]
    print(f"\nPathology split (by avg_snr_lsig):")
    print(f"  clean (< 100): {len(clean)} frames")
    print(f"  borderline (100-1000): {len(borderline)} frames")
    print(f"  pathological (>= 1000): {len(pathological)} frames")

    # Pathology correlation with timing offset
    if pathological and splitter_idx_rows:
        path_splitter = [r["splitter_lts0"] for r in pathological if r["splitter_lts0"] is not None]
        clean_splitter = [r["splitter_lts0"] for r in clean if r["splitter_lts0"] is not None]
        print(f"\nPathological frames' splitter current_idx:")
        for idx, n in sorted(Counter(path_splitter).items()):
            print(f"  current_idx={idx}: {n} pathological frames")
        if clean_splitter:
            print(f"  (vs clean frames' splitter current_idx: {sorted(Counter(clean_splitter).items())})")

    if pathological and eq_idx_rows:
        path_eq = [r["eq_lts0"] for r in pathological if r["eq_lts0"] is not None]
        clean_eq = [r["eq_lts0"] for r in clean if r["eq_lts0"] is not None]
        print(f"\nPathological frames' equalizer lts0_bin:")
        for idx, n in sorted(Counter(path_eq).items()):
            print(f"  lts0_bin={idx}: {n} pathological frames")
        if clean_eq:
            print(f"  (vs clean frames' equalizer lts0_bin: {sorted(Counter(clean_eq).items())})")

    # Verdict
    # Ensure pathology-correlation lists are always defined (no UnboundLocalError
    # when there are no pathological frames or no splitter/eq rows)
    path_splitter = path_splitter if (pathological and splitter_idx_rows) else []
    clean_splitter = clean_splitter if (clean and splitter_idx_rows) else []
    path_eq = path_eq if (pathological and eq_idx_rows) else []
    clean_eq = clean_eq if (clean and eq_idx_rows) else []

    # Pathology-aware verdict: drift must correlate with pathology, not just exist
    splitter_drift_on_path = bool(path_splitter) and any(
        r != EXPECTED_SPLITTER_CURRENT_IDX for r in path_splitter
    )
    eq_drift_on_path = bool(path_eq) and any(
        r != EXPECTED_EQ_LTS0_BIN for r in path_eq
    )
    splitter_clean_at_baseline = bool(clean_splitter) and all(
        r == EXPECTED_SPLITTER_CURRENT_IDX for r in clean_splitter
    )
    eq_clean_at_baseline = bool(clean_eq) and all(
        r == EXPECTED_EQ_LTS0_BIN for r in clean_eq
    )

    if (splitter_drift_on_path and splitter_clean_at_baseline) or \
       (eq_drift_on_path and eq_clean_at_baseline):
        print(f"\n[VERDICT] HYPOTHESIS CONFIRMED — timing offset correlates with pathology")
        print(f"  splitter: drift_on_path={splitter_drift_on_path}, clean_at_baseline={splitter_clean_at_baseline}")
        print(f"  equalizer: drift_on_path={eq_drift_on_path}, clean_at_baseline={eq_clean_at_baseline}")
        print(f"  Recommendation: proceed to Task 7 (env-var-gated fixed offset correction)")
        return 0
    else:
        print(f"\n[VERDICT] HYPOTHESIS REFUTED — timing is correct (or drift doesn't correlate with pathology)")
        print(f"  splitter: drift_on_path={splitter_drift_on_path}, clean_at_baseline={splitter_clean_at_baseline}")
        print(f"  equalizer: drift_on_path={eq_drift_on_path}, clean_at_baseline={eq_clean_at_baseline}")
        print(f"  Recommendation: pivot investigation (Phase 32 or alternative root cause)")
        return 2


if __name__ == "__main__":
    sys.exit(main())
