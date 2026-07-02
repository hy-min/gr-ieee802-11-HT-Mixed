#!/usr/bin/conda/envs/gnuradio/bin/python
"""Parse p78b v2 dump log into per-frame JSON metrics.

Extracts per-frame:
  - frame_id (from HTSIG_EQ_DUMP frame=N or DELTA_PER_SYMBOL sym)
  - eq_htsig0: 48 complex values (data SCs from HTSIG_EQ_DUMP)
  - eq_htsig1: 48 complex values
  - pilots_htsig0: 4 complex values (pilot SCs)
  - pilots_htsig1: 4 complex values
  - delta: per-frame delta dict (h52_delta, lsig_delta/phi/bin, htsig0_delta/phi/bin, htsig1_delta/phi/bin)
  - ht_sig_cands: list of (rot, inv_a, inv_b, metric, fail) tuples
  - ht_sig_parse_fail: bool
  - is_ht_frame: bool
  - avg_snr_lsig, avg_snr_htsig

Saves to /tmp/p78b_per_frame.json
"""
import json
import re
import sys

LOG_PATH = "/home/hy/gr-ieee802-11/docs/superpowers/notes/p78b_dump_v2.log"
OUT_PATH = "/tmp/p78b_per_frame.json"

# Regex for HTSIG_EQ_DUMP:
# [HTSIG_EQ_DUMP] frame=N htsig0_eq=[r+i,r+i,...] htsig1_eq=[r+i,r+i,...]
# Each list has 52 entries (48 data + 4 pilots)
# Trailing summary "htsig0 mean|re|=... mean_im=... std_im=... htsig1 mean|re|=... mean_im=... std_im=..."
re_eq = re.compile(
    r'\[HTSIG_EQ_DUMP\] frame=(\d+) htsig0_eq=\[(.*?)\] htsig1_eq=\[(.*?)\]'
)

# Regex for HTSIG_BIN_DUMP:
# [HTSIG_BIN_DUMP] counter=N frame=N htsig0=[r+i,...] htsig1=[r+i,...]
re_bin = re.compile(
    r'\[HTSIG_BIN_DUMP\] counter=(\d+) frame=(\d+) htsig0=\[(.*?)\] htsig1=\[(.*?)\]'
)

# Regex for complex number: e.g., "1.234-0.567i" or "1.234+0.567i" or "nan+nani"
# Group 1: real, Group 2: imaginary
re_cnum = re.compile(r'([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?|nan)([+-]\d+\.?\d*(?:[eE][+-]?\d+)?|nan)i')

# Regex for DELTA_PER_SYMBOL:
# [DELTA_PER_SYMBOL] sym=N H52_delta=X LSIG_delta=X phi=X |bin|=X HTSIG0_delta=X phi=X |bin|=X HTSIG1_delta=X phi=X |bin|=X
re_delta = re.compile(
    r'\[DELTA_PER_SYMBOL\] sym=(\d+) H52_delta=([+-]?\d+\.?\d*)'
    r' LSIG_delta=([+-]?\d+\.?\d*) phi=([+-]?\d+\.?\d*) \|bin\|=([+-]?\d+\.?\d*)'
    r' HTSIG0_delta=([+-]?\d+\.?\d*) phi=([+-]?\d+\.?\d*) \|bin\|=([+-]?\d+\.?\d*)'
    r' HTSIG1_delta=([+-]?\d+\.?\d*) phi=([+-]?\d+\.?\d*) \|bin\|=([+-]?\d+\.?\d*)'
)

# Regex for HT_SIG_CAND
# [HT_SIG_CAND] sym=N rot=N inv_a=N inv_b=N metric=N fail=...
re_htsig_cand = re.compile(
    r'\[HT_SIG_CAND\] sym=(\d+) rot=(\d+) inv_a=(\d+) inv_b=(\d+) metric=(\d+) fail=(\S+)'
)

# Regex for HT_SIG_PARSE_FAIL
# [HT_SIG_PARSE_FAIL] timeout_sym=N n_candidates=N best_metric=... threshold=... avg_snr_lsig=X avg_snr_htsig=Y lsig_rate=... lsig_len=... lsig_inv=... last_rot=... last_inv_a=... last_inv_b=... is_ht_frame=...
re_htsig_fail = re.compile(r'\[HT_SIG_PARSE_FAIL\] timeout_sym=(\d+)')

# Regex for LSIG_PARSE_FAIL
# [LSIG_PARSE_FAIL] sym=N reason='...' rate=... length=... parity_ok=... avg_snr=X avg_snr_ht=Y inv_tried=... is_ht_frame=...
re_lsig_fail = re.compile(r'\[LSIG_PARSE_FAIL\] sym=(\d+).*?avg_snr=([+-]?\d+\.?\d*).*?is_ht_frame=(\d)')

# Regex for is_ht_frame (used standalone)
re_is_ht = re.compile(r'is_ht_frame=(\d)')

# Regex for L-SIG_DECODE_OK / similar (avg_snr_lsig)
re_avg_snr_lsig = re.compile(r'avg_snr_lsig=([+-]?\d+\.?\d*)')
re_avg_snr_htsig = re.compile(r'avg_snr_htsig=([+-]?\d+\.?\d*)')


def parse_complex_list(s):
    """Parse a list of complex numbers from a string like '1.2+3.4i,5.6-7.8i,...'"""
    out = []
    parts = s.split(',')
    for p in parts:
        p = p.strip()
        m = re_cnum.fullmatch(p)
        if m:
            re_str, im_str = m.group(1), m.group(2)
            try:
                re_v = float(re_str)
                im_v = float(im_str)
            except ValueError:
                re_v = float('nan')
                im_v = float('nan')
            out.append({'re': re_v, 'im': im_v})
        else:
            out.append({'re': float('nan'), 'im': float('nan')})
    return out


def get_or_create_frame(frames, sym_to_idx, key):
    """Get frame at key, or create new."""
    if key not in sym_to_idx:
        sym_to_idx[key] = len(frames)
        frames.append({'frame_id': key})
    return frames[sym_to_idx[key]]


def parse_log(log_path):
    frames = []  # list of per-frame dicts
    sym_to_idx = {}  # sym/frame -> frame index

    with open(log_path) as f:
        for line in f:
            # Try eq dump
            m = re_eq.search(line)
            if m:
                frame_id = int(m.group(1))
                htsig0_list = parse_complex_list(m.group(2))
                htsig1_list = parse_complex_list(m.group(3))
                entry = get_or_create_frame(frames, sym_to_idx, frame_id)
                entry['eq_htsig0'] = htsig0_list
                entry['eq_htsig1'] = htsig1_list
                # Pilots are last 4 SCs (indices 48-51) for HT-SIG
                if len(htsig0_list) >= 52:
                    entry['eq_htsig0_pilots'] = htsig0_list[48:52]
                    entry['eq_htsig1_pilots'] = htsig1_list[48:52]
                # Extract inline summary stats if present
                m_sum = re.search(
                    r'htsig0 mean\|re\|=([+-]?\d+\.?\d*) mean_im=([+-]?\d+\.?\d*) std_im=([+-]?\d+\.?\d*)'
                    r' htsig1 mean\|re\|=([+-]?\d+\.?\d*) mean_im=([+-]?\d+\.?\d*) std_im=([+-]?\d+\.?\d*)',
                    line,
                )
                if m_sum:
                    entry['eq_htsig0_mean_abs_re'] = float(m_sum.group(1))
                    entry['eq_htsig0_mean_im'] = float(m_sum.group(2))
                    entry['eq_htsig0_std_im'] = float(m_sum.group(3))
                    entry['eq_htsig1_mean_abs_re'] = float(m_sum.group(4))
                    entry['eq_htsig1_mean_im'] = float(m_sum.group(5))
                    entry['eq_htsig1_std_im'] = float(m_sum.group(6))
                continue

            # Try bin dump
            m = re_bin.search(line)
            if m:
                counter = int(m.group(1))
                frame_id = int(m.group(2))
                bin0_list = parse_complex_list(m.group(3))
                bin1_list = parse_complex_list(m.group(4))
                entry = get_or_create_frame(frames, sym_to_idx, frame_id)
                entry['bin_htsig0'] = bin0_list
                entry['bin_htsig1'] = bin1_list
                entry['bin_counter'] = counter
                continue

            # Try delta dump
            m = re_delta.search(line)
            if m:
                sym = int(m.group(1))
                frame_dict = {
                    'sym': sym,
                    'h52_delta': float(m.group(2)),
                    'lsig_delta': float(m.group(3)),
                    'lsig_phi': float(m.group(4)),
                    'lsig_bin': float(m.group(5)),
                    'htsig0_delta': float(m.group(6)),
                    'htsig0_phi': float(m.group(7)),
                    'htsig0_bin': float(m.group(8)),
                    'htsig1_delta': float(m.group(9)),
                    'htsig1_phi': float(m.group(10)),
                    'htsig1_bin': float(m.group(11)),
                }
                entry = get_or_create_frame(frames, sym_to_idx, sym)
                entry['delta'] = frame_dict
                continue

            # Try HT_SIG_PARSE_FAIL (highest signal of "this frame failed")
            m = re_htsig_fail.search(line)
            if m:
                sym = int(m.group(1))
                entry = get_or_create_frame(frames, sym_to_idx, sym)
                entry['ht_sig_parse_fail'] = True
                m_snr_l = re_avg_snr_lsig.search(line)
                m_snr_h = re_avg_snr_htsig.search(line)
                if m_snr_l:
                    entry['avg_snr_lsig'] = float(m_snr_l.group(1))
                if m_snr_h:
                    entry['avg_snr_htsig'] = float(m_snr_h.group(1))
                m2 = re_is_ht.search(line)
                if m2:
                    entry['is_ht_frame'] = (int(m2.group(1)) == 1)
                continue

            # Try LSIG_PARSE_FAIL
            m = re_lsig_fail.search(line)
            if m:
                sym = int(m.group(1))
                entry = get_or_create_frame(frames, sym_to_idx, sym)
                entry['lsig_parse_fail'] = True
                entry['avg_snr_lsig_lsigfail'] = float(m.group(2))
                entry['is_ht_frame'] = (int(m.group(3)) == 1)
                continue

            # Try HT_SIG_CAND
            m = re_htsig_cand.search(line)
            if m:
                sym = int(m.group(1))
                entry = get_or_create_frame(frames, sym_to_idx, sym)
                if 'ht_sig_cands' not in entry:
                    entry['ht_sig_cands'] = []
                entry['ht_sig_cands'].append({
                    'rot': int(m.group(2)),
                    'inv_a': int(m.group(3)),
                    'inv_b': int(m.group(4)),
                    'metric': int(m.group(5)),
                    'fail': m.group(6),
                })
                continue

    return frames


def compute_summary(frames):
    """Compute summary statistics across all frames."""
    eq_im_means = []
    eq_re_means_abs = []
    eq_std_ims = []
    delta_h52 = []
    delta_htsig0_minus_htsig1 = []
    n_fail = 0
    n_ht = 0
    n_lsig_fail = 0
    n_cands_total = 0

    for f in frames:
        # Use inline summary stats if available, else compute
        if 'eq_htsig0_mean_abs_re' in f:
            eq_re_means_abs.append(f['eq_htsig0_mean_abs_re'])
            eq_im_means.append(f['eq_htsig0_mean_im'])
            eq_std_ims.append(f['eq_htsig0_std_im'])
        elif 'eq_htsig0' in f:
            try:
                import numpy as np
                data = f['eq_htsig0'][:48]
                re_vals = [c['re'] for c in data if not np.isnan(c['re'])]
                im_vals = [c['im'] for c in data if not np.isnan(c['im'])]
                if re_vals and im_vals:
                    eq_re_means_abs.append(np.mean(np.abs(re_vals)))
                    eq_im_means.append(np.mean(im_vals))
                    eq_std_ims.append(np.std(im_vals))
            except ImportError:
                pass

        if 'delta' in f:
            delta_h52.append(f['delta']['h52_delta'])
            delta_htsig0_minus_htsig1.append(
                f['delta']['htsig0_phi'] - f['delta']['htsig1_phi']
            )

        if f.get('ht_sig_parse_fail'):
            n_fail += 1
        if f.get('lsig_parse_fail'):
            n_lsig_fail += 1
        if f.get('is_ht_frame'):
            n_ht += 1
        if 'ht_sig_cands' in f:
            n_cands_total += len(f['ht_sig_cands'])

    print("\n=== Summary statistics ===")
    print(f"  Total frames: {len(frames)}")
    print(f"  HT_SIG_PARSE_FAIL: {n_fail}")
    print(f"  LSIG_PARSE_FAIL: {n_lsig_fail}")
    print(f"  is_ht_frame=1: {n_ht}")
    print(f"  Total HT_SIG_CAND candidates: {n_cands_total}")
    if eq_re_means_abs:
        try:
            import numpy as np
            print(f"  eq_htsig0 mean(|re|): {np.mean(eq_re_means_abs):.3f} (target <0.3 for QBPSK)")
            print(f"  eq_htsig0 mean(im): {np.mean(eq_im_means):.3f} (target != 0 for QBPSK)")
            print(f"  eq_htsig0 std(im): {np.mean(eq_std_ims):.3f} (target <0.3)")
        except ImportError:
            print(f"  eq_htsig0 mean(|re|): {sum(eq_re_means_abs)/len(eq_re_means_abs):.3f}")
            print(f"  eq_htsig0 mean(im): {sum(eq_im_means)/len(eq_im_means):.3f}")
            print(f"  eq_htsig0 std(im): {sum(eq_std_ims)/len(eq_std_ims):.3f}")
    if delta_h52:
        try:
            import numpy as np
            print(f"  H52_delta mean: {np.mean(delta_h52):.3f}")
            print(f"  H52_delta std: {np.std(delta_h52):.3f}")
            print(f"  HTSIG0-HTSIG1 phase diff mean: {np.mean(delta_htsig0_minus_htsig1):.3f} rad")
            print(f"  HTSIG0-HTSIG1 phase diff std: {np.std(delta_htsig0_minus_htsig1):.3f} rad")
        except ImportError:
            n = len(delta_h52)
            print(f"  H52_delta mean: {sum(delta_h52)/n:.3f}")
            print(f"  H52_delta std: {(sum((x-sum(delta_h52)/n)**2 for x in delta_h52)/n)**0.5:.3f}")
            n2 = len(delta_htsig0_minus_htsig1)
            print(f"  HTSIG0-HTSIG1 phase diff mean: {sum(delta_htsig0_minus_htsig1)/n2:.3f} rad")


def main():
    frames = parse_log(LOG_PATH)
    print(f"Parsed {len(frames)} unique frames")

    # Stats
    n_eq = sum(1 for f in frames if 'eq_htsig0' in f)
    n_bin = sum(1 for f in frames if 'bin_htsig0' in f)
    n_delta = sum(1 for f in frames if 'delta' in f)
    n_fail = sum(1 for f in frames if f.get('ht_sig_parse_fail'))
    n_lsig_fail = sum(1 for f in frames if f.get('lsig_parse_fail'))
    n_cands = sum(1 for f in frames if 'ht_sig_cands' in f)
    n_ht = sum(1 for f in frames if f.get('is_ht_frame'))
    print(f"  with eq dump: {n_eq}")
    print(f"  with bin dump: {n_bin}")
    print(f"  with delta dump: {n_delta}")
    print(f"  with HT_SIG_PARSE_FAIL: {n_fail}")
    print(f"  with LSIG_PARSE_FAIL: {n_lsig_fail}")
    print(f"  with HT_SIG cands: {n_cands}")
    print(f"  is_ht_frame=1: {n_ht}")

    # Save
    with open(OUT_PATH, 'w') as f:
        json.dump(frames, f, indent=2)
    print(f"Saved to {OUT_PATH}")

    # Summary
    compute_summary(frames)


if __name__ == "__main__":
    main()