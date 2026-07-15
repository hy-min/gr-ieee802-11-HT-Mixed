#!/usr/bin/env python3
"""Phase 86 T3: audit the 5 "stable null SCs" from Phase 78b.

Per Phase 78b verdict: USRP shows 5 stable globally-null SCs at {-21,-7,+7,+21,-13}
on 5250 MHz cable. The first 4 are PILOT SCs in 802.11n, the 5th is a data SC.

If pilots are null in the L-LTF, then H52 at pilot SCs is undefined and any phase
correction derived from pilots is biased. This could explain rate=0x9.

Method:
  1. Parse [LTF0_FFT_PRECOMP] lines (raw L-LTF0 FFT, before any compensation).
  2. For each frame, compute |H| at all 52 active SCs.
  3. Identify which SCs are NULL across all frames.
  4. Cross-check against Phase 78b's claimed set {-21,-7,+7,+21,-13}.
"""
import re
import sys
import numpy as np


DUMP_FILE = '/tmp/p86_full_dump.log'

# Per the C++ dump format:
# [LTF0_FFT_PRECOMP] counter=0 SC[0:5]=a+bi ...  |SC[26]|=X arg[26]=Y
# Counter=0 means L-LTF0, counter=1 means L-LTF1
# The dump shows 6 sample SCs (0,1,2,3,4,5) and SC[26]
# We need all 52 SCs, so this format is incomplete.

# Check the full format
PRECOMP_RE = re.compile(
    r'\[LTF0_FFT_PRECOMP\] counter=(\d+) (SC\[\d+:\d+\]=[^|]+)\s*\|SC\[(\d+)\]=([\d.\-]+) arg\[(\d+)\]=([\d.\-]+)'
)
# Each SC[i:j]= is a comma-separated list of "a+bi" complex values


def parse_complex(s):
    """Parse 'a+bi' or 'a-bi' format."""
    s = s.strip()
    m = re.match(r'([\d.\-]+)([\+\-])([\d.\-]+)i', s)
    if not m:
        return None
    return float(m.group(1)) + 1j * float(m.group(3)) * (1 if m.group(2) == '+' else -1)


def main():
    print(f"[P86-T3] Parsing {DUMP_FILE}...")
    with open(DUMP_FILE) as f:
        lines = f.readlines()

    # Find all PRECOMP lines and parse
    frames_ltf0 = []  # list of dicts: {frame_idx, sc_values: list of 52 complex}
    frames_ltf1 = []

    for line in lines:
        m = PRECOMP_RE.search(line)
        if not m:
            continue
        counter = int(m.group(1))
        sc_str = m.group(2)
        # Parse "SC[0:5]=a+bi,b+ci,..."
        sc_list = []
        for s in sc_str.split('=')[1].split(','):
            s = s.strip()
            if not s:
                continue
            c = parse_complex(s)
            if c is not None:
                sc_list.append(c)
        # The dump format is incomplete — only shows 6 SCs (indices 0-5) and SC[26]
        # This is the C++ implementation choice, not enough for full analysis.
        if counter == 0:
            frames_ltf0.append({'sc_list': sc_list})
        else:
            frames_ltf1.append({'sc_list': sc_list})

    print(f"[P86-T3] Got {len(frames_ltf0)} L-LTF0 frames and {len(frames_ltf1)} L-LTF1 frames")
    print(f"[P86-T3] SCs dumped per frame: {len(frames_ltf0[0]['sc_list']) if frames_ltf0 else 0} + SC[26] = 7 total")

    # The dump format is INCOMPLETE — only 7 out of 52 SCs are shown
    # We need a different dump format. Let's fall back to reading
    # HHDR52_PER_FRAME which has more SCs.

    print("\n[P86-T3] === Falling back to HHDR52_PER_FRAME dump (more SCs) ===")
    HHDR52_RE = re.compile(
        r'\[HHDR52_PER_FRAME\] frame_sym=(\d+) (.+)'
    )
    # Format: H[0]=0.453+-1.235j H[10]=1.027+-2.020j ...
    hhdr52_per_frame = {}  # frame_sym -> list of (idx, complex)
    for line in lines:
        m = HHDR52_RE.search(line)
        if not m:
            continue
        frame_sym = int(m.group(1))
        rest = m.group(2)
        sc_dict = {}
        for m2 in re.finditer(r'H\[(\d+)\]=([\d.\-+]+j)', rest):
            idx = int(m2.group(1))
            s = m2.group(2)
            # parse complex
            m3 = re.match(r'([\d.\-]+)([\+\-])([\d.\-]+)j', s)
            if m3:
                v = float(m3.group(1)) + 1j * float(m3.group(3)) * (1 if m3.group(2) == '+' else -1)
                sc_dict[idx] = v
        hhdr52_per_frame.setdefault(frame_sym, []).append(sc_dict)

    # frame_sym=4 means HT-SIG1 (the Hhdr52 is computed at counter=4)
    hhdr52_data = hhdr52_per_frame.get(4, [])
    print(f"[P86-T3] Got {len(hhdr52_data)} Hhdr52 dumps at frame_sym=4")
    if not hhdr52_data:
        return

    # Check how many SCs are dumped per frame
    sample = hhdr52_data[0]
    print(f"[P86-T3] SCs dumped per Hhdr52 frame: {sorted(sample.keys())[:20]}...")
    print(f"[P86-T3] Total SCs dumped: {len(sample)}")

    # Stack into array (only the dumped SCs)
    all_scs = sorted(sample.keys())
    n_frames = len(hhdr52_data)
    H52 = np.zeros((n_frames, len(all_scs)), dtype=np.complex64)
    for i, frame in enumerate(hhdr52_data):
        for j, sc in enumerate(all_scs):
            H52[i, j] = frame[sc]

    # For each dumped SC, compute mean |H|, std |H|
    print("\n[P86-T3] === Per-SC |H| statistics (Hhdr52 from frame_sym=4) ===")
    print(f"  {'SC':>4} {'mean':>8} {'std':>8} {'min':>8} {'max':>8}  type")
    for j, sc in enumerate(all_scs):
        mags = np.abs(H52[:, j])
        if sc in (-21, -7, 7, 21):
            sc_type = "PILOT (Phase 78b: stable null)"
        else:
            sc_type = ""
        print(f"  {sc:>4} {mags.mean():>8.3f} {mags.std():>8.3f} "
              f"{mags.min():>8.3f} {mags.max():>8.3f}  {sc_type}")

    # Now check rate=0x9 vs rate=0xD distribution
    rate_lines = [l for l in lines if 'lsig_rate=' in l]
    print(f"\n[P86-T3] L-SIG rate log lines: {len(rate_lines)}")
    if rate_lines:
        from collections import Counter
        rates = []
        for l in rate_lines:
            m = re.search(r'lsig_rate=0x([0-9A-Fa-f]+)', l)
            if m:
                rates.append(int(m.group(1), 16))
        c = Counter(rates)
        print(f"[P86-T3] Rate distribution: {dict(c)}")


if __name__ == '__main__':
    main()