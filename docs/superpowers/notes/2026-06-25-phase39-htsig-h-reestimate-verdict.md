# Phase 39 Verdict — HT-SIG Pilot-Based H Re-estimation REFUTED (2026-06-25)

**Status:** ❌ **Hypothesis REFUTED.** Per-symbol H re-estimation from HT-SIG0/1 own 4 pilots (with 4→52 linear interpolation) makes things worse, not better. The H re-estimated from the noisy HT-SIG pilots is 6-12× larger than the L-LTF0-based Hhdr52 at the pilot SCs, and the linear interpolation between pilots amplifies noise at non-pilot SCs.

## TL;DR

| Metric | Phase 38 (no re-estimate) | Phase 39 (re-estimate ON) | Direction |
|---|---|---|---|
| HT_SIG_PARSE_FAIL | 6-9 | 29 | ❌ WORSE (3-5× more) |
| LSIG_DECODE OK | 109-158 | 180 | (LSIG unaffected — uses Hhdr52) |
| FCS_OK | 0 | 0 | still blocked |
| Best viterbi metric | 7-9 (rare) | 7-9 (rare) | (no convergence) |
| Dominant metric | 15 | 16 | ❌ slightly WORSE |

The new flag is opt-in (default OFF) and loopback is unaffected (H_a_ptr = Hhdr52 when OFF). Code is preserved for future reference.

## Implementation

**New env vars**:
- `IEEE80211_HTSIG_H_REESTIMATE=1` — re-estimate H_htsig0/H_htsig1 from each symbol's own 4 pilots, replace Hhdr52 for HT-SIG equalization (L-SIG stays on Hhdr52)
- `IEEE80211_HTSIG_H52_DUMP=1` — flood-gated dump of |H_htsig0|, |H_htsig1|, and ratio |H_htsig|/|Hhdr52| per SC

**New helper** (in `frame_equalizer_impl.cc`):
- `static bool estimate_H_from_htsig_pilots(const gr_complex* rx52, const gr_complex* H_fallback, gr_complex* H_htsig52)`
  - Computes H at 4 pilot SCs from rx52[48..51] / kHtsigPilotQbpsk = {+j, +j, +j, -j}
  - Sanity gate: n_valid >= 2 (else fallback to H_fallback)
  - Pilot bins: H_htsig52[48..51] = H_at_pilot (or fallback for invalid pilots)
  - Data bins: piecewise linear interpolation in complex plane between valid pilot anchors, with edge extrapolation

**Signature change** to `decode_htsig_from_rotated`:
- `const gr_complex* H52` → `const gr_complex* H52_a, const gr_complex* H52_b`
- HT-SIG0 path uses H52_a, HT-SIG1 path uses H52_b
- All 8 internal H52 references updated

**Viterbi call site** (`frame_equalizer_impl.cc:4492-4504`):
- Pass `H_a_ptr, H_b_ptr` (default to Hhdr52 when env var is OFF)

## Why It Failed

### 1. H_htsig vs Hhdr52 magnitude mismatch at pilot SCs

The dump shows `|H_htsig0|/|Hhdr52|` ratio at pilot bins (48-51) is consistently 6-12 (e.g., 6.61, 10.56, 12.30, 10.24 for frame 0). The L-LTF0 pilots and HT-SIG0 pilots are 4 μs apart in time, so a magnitude difference of 6-12× can only be explained by:

- **Massive time variation in the channel** (Doppler / fast fading). Unlikely for indoor 5 GHz.
- **HT-SIG0 pilots are dominated by noise**, not signal. The Hhdr52 from L-LTF0 is the true H, and the HT-SIG0 pilots are too noisy to estimate H.

### 2. Linear interpolation overshoots at non-pilot SCs

The ratio at non-pilot SCs varies wildly (0.12 to 110 across SCs). This means H_htsig is sometimes much larger, sometimes much smaller than Hhdr52. The 4-point linear interpolation can't model the actual channel shape — it just propagates noise from the pilots.

### 3. HTSIG_EQ_DUMP confirms degraded equalization

With H_htsig replacing Hhdr52:
```
htsig0 mean|re|=9.597 mean_im=-2.279 std_im=12.726
htsig1 mean|re|=8.948 mean_im=0.728 std_im=10.814
```

vs Phase 38 (Hhdr52, no re-estimate):
```
htsig0 mean|re|=2.0 mean_im=0.0 std_im=1.5
htsig1 mean|re|=2.0 mean_im=-0.3 std_im=1.9
```

`std_im` went from 1.5 to 12.7 — 8× worse. The H_htsig is mostly noise, and using it as the equalizer denominator actually amplified the noise floor.

### 4. Best viterbi metric degraded

Phase 38: 178/480 candidates at metric=16, 2 candidates at metric=7 (rare convergence attempt)
Phase 39: dominant metric=16, 2 candidates at metric=7 (same rarity)

The re-estimation shifted the metric distribution up by 1 (15→16), meaning the viterbi is slightly more uncertain with the worse H.

## What This Tells Us

- **HT-SIG0/1 pilots are corrupted at the same level as HT-SIG0/1 data** — they can't be trusted to estimate H
- **The channel at HT-SIG0/1 time is NOT well-approximated by the 4 pilots + linear interpolation** — there's structure at non-pilot SCs the linear model can't capture
- **The actual H is closer to Hhdr52 (L-LTF0-based) than to H_htsig (HT-SIG-pilot-based)**

## Why Phase 38 HTSIG_EQ_DUMP Was Right

The dump showed `|re| ≈ 2.0, std_im = 1.5`. The 1.5 noise floor is consistent with Hhdr52 having deep nulls at some SCs, but the signal is still recognizable on the REAL axis (after CFO/SFO/δ correction). The 4-rotation search SHOULD find a winner (rot=1 or rot=2 to put signal on IMAG axis), but the perpendicular noise exceeds the QBPSK 45° margin.

This means the bottleneck is not the H estimate — it's the noise floor itself. The 1.5 std_im on the perpendicular axis is the actual receiver sensitivity limit.

## What's Left to Try (Equalizer-Level)

After 10 REFUTED equalizer-level investigations, the project memory says:

> **9 equalizer-level investigations REFUTED, this is the 10th.** Question whether impairment is in equalizer or downstream viterbi/decoder.

Possible non-equalizer directions:
1. **Per-symbol δ tracking** (Phase 38 Step 4) — REFUTED, but worth re-trying with a more careful implementation (Phase 38 Step 4 had a bug where it never fired)
2. **Soft-decision LLR viterbi** on HT-SIG (Phase 37 Layer 3 PASS at 6 dB SNR — decoder is robust enough, but USRP may have unique noise structure)
3. **ML detection on the 64-PSK grid** (Phase 33b 64-PSK residual) — directly address the dominant noise source
4. **Re-examine FFT window placement** for HT-SIG0/1 (one sample off = 90° phase shift that would break QBPSK)

## Files Changed (preserved as opt-in)

- `lib/frame_equalizer_impl.h:140-152` — `d_apply_htsig_h_reestimate`, `d_log_htsig_h52` flags
- `lib/frame_equalizer_impl.cc`:
  - Lines 351-456: `estimate_H_from_htsig_pilots` helper (106 lines)
  - Lines 2116-2128: `decode_htsig_from_rotated` signature change
  - Lines 2144-2158, 2173-2186, 2224-2234, 2243-2254: internal H52 → H52_a/H52_b
  - Lines 2760-2779: env var inits
  - Lines 4412-4470: apply block in `general_work` (H_htsig0/1 compute + HTSIG_H52_DUMP)
  - Lines 4492-4504: viterbi call site update

The flag is default OFF. Loopback is unaffected. To re-verify:
```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py
# Expected: Final: OK=1 FAIL=0
```

## Conclusion

**Phase 39 hypothesis REFUTED.** HT-SIG0/1 pilots are too noisy to estimate H. The bottleneck is not the channel estimate — it's the noise floor at the equalizer output, which exceeds the QBPSK 45° margin. After 10 REFUTED equalizer-level investigations, the next direction should be either:
1. Re-examine whether the impairment is in the viterbi/decoder (despite Phase 37 confirming decoder is correct)
2. Re-examine FFT window placement for HT-SIG0/1
3. ML detection on the 64-PSK grid (Phase 33b residual)
