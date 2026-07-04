#!/usr/bin/env python3
"""Phase 87 T2: per-frame rel_idx jitter analysis.

Parse the SPLITTER_TIMING dump from /tmp/p87_splitter_timing_5s.log:
- [SPLITTER_TIMING] seq=N frame_start_abs=X current_idx=Y rel_idx=Z expected_first_sym=176

The dump fires when rel_idx <= 8, so we see the first 9 samples of each frame.

Hypothesis: if frame_start_abs is jittery across frames (std > 0.5 sample), then
sync_long output is misaligned per frame, causing CPE phase std=90°.

Method:
  1. Extract unique (seq, frame_start_abs) pairs.
  2. Compare consecutive gaps.
  3. Check that frame_start_abs always starts at a position where rel_idx=0
     (i.e., the splitter correctly receives frame_start).
  4. Compare with Python L-STF detection positions for cross-check.
"""
import re
import numpy as np

LOG_FILE = '/tmp/p87_splitter_timing_5s.log'

# Regex: [SPLITTER_TIMING] seq=1 frame_start_abs=0 current_idx=0 rel_idx=0 expected_first_sym=176
LINE_RE = re.compile(
    r'\[SPLITTER_TIMING\] seq=(\d+) frame_start_abs=(\d+) current_idx=(\d+) rel_idx=(\d+) expected_first_sym=(\d+)'
)


def main():
    print(f"[P87-T2] Parsing {LOG_FILE}...")
    frames = {}  # seq -> {frame_start_abs, first_rel_idx, max_rel_idx}
    with open(LOG_FILE) as f:
        for line in f:
            m = LINE_RE.search(line)
            if not m:
                continue
            seq = int(m.group(1))
            fsa = int(m.group(2))
            ci = int(m.group(3))
            ri = int(m.group(4))
            ef = int(m.group(5))
            if seq not in frames:
                frames[seq] = {'frame_start_abs': fsa, 'min_rel_idx': ri,
                                'max_rel_idx': ri, 'expected_first_sym': ef}
            else:
                if ri < frames[seq]['min_rel_idx']:
                    frames[seq]['min_rel_idx'] = ri
                if ri > frames[seq]['max_rel_idx']:
                    frames[seq]['max_rel_idx'] = ri

    print(f"[P87-T2] Got {len(frames)} unique frames in dump")
    if not frames:
        return

    # Sort by seq
    seqs = sorted(frames.keys())
    fsa_arr = np.array([frames[s]['frame_start_abs'] for s in seqs])

    # Inter-frame gaps
    gaps = np.diff(fsa_arr)
    print(f"\n[P87-T2] === Per-frame frame_start_abs (first 10) ===")
    for i in range(min(10, len(seqs))):
        s = seqs[i]
        print(f"  seq={s:>4} frame_start_abs={frames[s]['frame_start_abs']:>10} "
              f"min_rel_idx={frames[s]['min_rel_idx']} max_rel_idx={frames[s]['max_rel_idx']}")

    print(f"\n[P87-T2] === Inter-frame gap (frame_start_abs differences) ===")
    print(f"  n gaps: {len(gaps)}")
    print(f"  gap mean: {gaps.mean():.2f}")
    print(f"  gap std:  {gaps.std():.2f}")
    print(f"  gap min:  {gaps.min()}")
    print(f"  gap max:  {gaps.max()}")
    print(f"  gap CV:   {gaps.std()/gaps.mean():.4f}")

    # If gaps are constant (CV < 0.01), frames are sent at fixed rate → jitter = 0
    # If gaps vary, it's either real frame timing or jitter

    # Cross-check with sync_long output if available
    print(f"\n[P87-T2] === Frame boundary relative to d_frame_start=174 ===")
    # In sync_long, d_frame_start=174 means frame starts at sample 174 relative to L-STF start.
    # In the splitter, frame_start_abs is the ABSOLUTE sample position of d_frame_start.
    # The first sample entering the splitter (rel_idx=0) should be d_frame_start_abs.
    # So frame_start_abs is the absolute sample where d_frame_start=174 was hit.

    # Compare min_rel_idx — should always be 0 (first sample in dump)
    min_rel_idx_arr = np.array([frames[s]['min_rel_idx'] for s in seqs])
    max_rel_idx_arr = np.array([frames[s]['max_rel_idx'] for s in seqs])
    print(f"  min_rel_idx distribution: unique values = {np.unique(min_rel_idx_arr)}")
    print(f"  max_rel_idx distribution: unique values = {np.unique(max_rel_idx_arr)}")
    print(f"  All frames have rel_idx range [0..8]: {(max_rel_idx_arr == 8).all()}")

    # If first_rel_idx is always 0, the splitter correctly receives frame_start.
    # The question becomes: is frame_start_abs itself jittery relative to actual L-STF?

    # Compare to Python L-STF detection: 149 frames in 600M samples
    # Python gap: ~4M samples between frames (USRP sends frames every ~0.2s)
    # C++ gap here: ~7800 samples = ~390 µs at 20 MHz
    # That's 30x faster than USRP, suggesting the replay sends MORE frames

    print(f"\n[P87-T2] === Frame rate analysis ===")
    total_samples = fsa_arr.max() + 1000  # approximate
    rate_per_sec = len(seqs) / (total_samples / 20e6)
    print(f"  {len(seqs)} frames over ~{total_samples} samples ({total_samples/20e6:.2f}s)")
    print(f"  Rate: {rate_per_sec:.0f} frames/sec")
    print(f"  Gap mean: {gaps.mean():.0f} samples = {gaps.mean()/20e6*1e3:.2f} ms")

    # Critical: check if frame_start_abs is jittery modulo the expected frame period
    # If frames arrive at 7800-sample intervals, jitter = std of (frame_start_abs - expected_position)
    expected_period = gaps.mean()
    expected_positions = fsa_arr[0] + np.arange(len(fsa_arr)) * expected_period
    jitter = fsa_arr - expected_positions
    print(f"\n[P87-T2] === Position jitter vs constant period model ===")
    print(f"  Period = {expected_period:.2f} samples")
    print(f"  Jitter mean: {jitter.mean():.4f}")
    print(f"  Jitter std:  {jitter.std():.4f}")
    print(f"  Jitter range: [{jitter.min():.0f}, {jitter.max():.0f}]")

    # Cross-check: how does Python detect L-STF starts?
    print(f"\n[P87-T2] === Python L-STF detection cross-check ===")
    print("  (see /tmp/p28_loopback_iq.fc32, 5s = 600M samples)")
    print("  Python detected 149 L-STF starts")
    print("  C++ splitter received", len(seqs), "frames")
    print("  If counts differ: C++ detected different frame boundaries than Python")

    # Save
    np.savez('/tmp/p87_t2_jitter.npz',
             seqs=seqs, frame_start_abs=fsa_arr, gaps=gaps, jitter=jitter)


if __name__ == '__main__':
    main()