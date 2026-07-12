# Phase 143: BPSK-HT-SIG Fallback Architecture Design

**Date:** 2026-07-12  
**Status:** Draft (pending implementation plan)  
**Goal:** Break the USRP 1.77 rad per-SC phase-noise floor that blocks HT-SIG viterbi decoding.  
**Constraint:** Preserve standard 802.11n compatibility as the default path; non-standard BPSK-HT-SIG is opt-in fallback only.

---

## 1. Problem Statement

After 30+ equalizer-layer attacks (2-way H52, Wiener, Kalman, DDE, cross-frame averaging, frequency-domain smoothing, etc.), the project still cannot achieve `FCS_OK >= 1` on USRP X310 + UBX-160 hardware.

**Root cause (Phase 112 R1):** the USRP analog chain imposes ~**1.77 rad (101°) per-SC phase noise** on the received channel estimate.

- L-SIG uses **BPSK** (180° constellation spacing) and decodes reliably.
- HT-SIG0/HT-SIG1 use **QBPSK** (90° spacing) per 802.11n §20.3.9.4.4.
- 1.77 rad > π/2, so QBPSK constellation points are statistically crossed → HT-SIG viterbi sees 12–18 bit errors per 96 coded bits, far above the d_free=10 correction limit.

This design replaces QBPSK with **BPSK for HT-SIG0/HT-SIG1 only**, doubling the angular margin while keeping all other frame structure identical.

---

## 2. Design Overview

Introduce a single opt-in flag:

```bash
export IEEE80211_HTSIG_BPSK_FALLBACK=1
```

When enabled:

| Layer | Standard Mode | Fallback Mode |
|-------|--------------|---------------|
| L-SIG | BPSK on real axis | BPSK on real axis (unchanged) |
| HT-SIG0 | QBPSK (×j rotation) | BPSK (no rotation) |
| HT-SIG1 | QBPSK (×j rotation) | BPSK (no rotation) |
| HT-SIG pilots | `[+j,+j,+j,-j]` / `[-j,-j,-j,+j]` | `[+1,+1,+1,-1]` / `[-1,-1,-1,+1]` |
| RX bit extraction | `sign(imag(eq))` | `sign(real(eq))` |
| RX LLR / CPE | imag-axis reference | real-axis reference |

Capacity is unchanged: 2 OFDM symbols × 48 data subcarriers × 1 bit = 96 bits, exactly the HT-SIG payload size.

---

## 3. Why This Breaks the Floor

- BPSK decision boundary is at 0° (real axis); the two valid points are 180° apart.
- With 1.77 rad phase noise, the conditional error probability is dominated by points that rotate past π, which is the tail of the distribution.
- In contrast, QBPSK decision boundaries are at ±45°; 1.77 rad routinely crosses both boundaries.
- The L-SIG already proves the analog chain supports BPSK at the same SNR/phase-noise conditions.

---

## 4. Compatibility Model

This is a **TX/RX coordinated fallback**, not a receiver-only hack.

- **Default OFF:** All existing code paths and standard-device interoperability are preserved.
- **TX=1, RX=1:** Correct decoding of BPSK-HT-SIG frames.
- **TX=0, RX=1 or TX=1, RX=0:** HT-SIG CRC fails (safe degradation, no false positives).
- **Standard 802.11n devices:**
  - L-SIG remains standard, so the medium reservation (NAV) is valid.
  - Standard devices attempt to decode HT-SIG as QBPSK, fail, and drop the frame after the L-SIG duration.
  - They are **not crashed or confused**; they simply cannot receive the non-standard frame.

---

## 5. TX Modifications

### 5.1 File

`examples/mixed_mode_carrier_allocator.py`

### 5.2 Changes

1. Read the opt-in flag in `__init__`:

   ```python
   self._htsig_bpsk_fallback = (
       os.environ.get('IEEE80211_HTSIG_BPSK_FALLBACK') == '1'
   )
   ```

2. In `general_work`, conditionally apply the QBPSK rotation:

   ```python
   # HT-SIG data symbols
   if not self._htsig_bpsk_fallback:
       htsig1_bpsk48 = htsig1_bpsk48 * 1j
       htsig2_bpsk48 = htsig2_bpsk48 * 1j
       ht_sig_pilot_values = [pv * 1j for pv in self._legacy_pilot_values]
   else:
       ht_sig_pilot_values = self._legacy_pilot_values
   ```

3. Optionally log the mode once per packet for debug.

### 5.3 Lines of Code

Approximately 6 lines of Python, fully backward-compatible when the env var is unset.

---

## 6. RX Modifications

### 6.1 Files

- `lib/frame_equalizer_impl.h`
- `lib/frame_equalizer_impl.cc`

### 6.2 State Member

```cpp
bool d_htsig_bpsk_fallback;
```

Initialize in the constructor from the environment variable:

```cpp
d_htsig_bpsk_fallback = (std::getenv("IEEE80211_HTSIG_BPSK_FALLBACK") != nullptr);
```

### 6.3 HT-SIG0 Bit Extraction

Location: `frame_equalizer_impl.cc` ~line 3701

Current:

```cpp
eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0;
```

New:

```cpp
eqbits48_a[i] = d_htsig_bpsk_fallback
    ? ((eq.real() >= 0.0f) ? 1 : 0)
    : ((eq.imag() >= 0.0f) ? 1 : 0);
```

### 6.4 HT-SIG1 Bit Extraction

Location: `frame_equalizer_impl.cc` ~line 3948

Apply the same real/imag axis switch.

### 6.5 Soft-LLR Calculation

Locations: ~line 3716 and ~line 3957

Switch the sign decision from imag to real when fallback is active:

```cpp
float s = d_htsig_bpsk_fallback
    ? ((eq.real() >= 0.0f) ? 1.0f : -1.0f)
    : ((eq.imag() >= 0.0f) ? 1.0f : -1.0f);
```

### 6.6 HT-SIG1 Pilot CPE Reference

Location: `frame_equalizer_impl.cc` ~line 3857

Current pilot reference is on the imag axis:

```cpp
gr_complex ref = gr_complex(0.0f, (eq_p.imag() >= 0.0f) ? 1.0f : -1.0f);
```

Fallback reference moves to the real axis:

```cpp
gr_complex ref = d_htsig_bpsk_fallback
    ? gr_complex((eq_p.real() >= 0.0f) ? 1.0f : -1.0f, 0.0f)
    : gr_complex(0.0f, (eq_p.imag() >= 0.0f) ? 1.0f : -1.0f);
```

### 6.7 QBPSK Rotation Search

`detect_htsig_rotation()` and `vote_qbpsk_rotation()` assume a 90°-rotated constellation. In fallback mode:

- HT-SIG rotation is fixed at 0°.
- Only the 180° sign ambiguity (`invert_a` / `invert_b`) is searched, identical to L-SIG handling.
- This simplifies the receiver and removes the 45°/fine-rotation search that is irrelevant for BPSK.

Implementation: early in `decode_htsig_candidate()` (or equivalent entry point), if `d_htsig_bpsk_fallback` is true, skip the QBPSK rotation detector and set the rotation index to 0.

---

## 7. Test-Script Modifications

### 7.1 File

`test_usrp_minimal_loopback.py`

### 7.2 New Argument

```python
parser.add_argument('--htsig-bpsk-fallback', action='store_true',
                    help='Phase 143: use BPSK instead of QBPSK for HT-SIG0/HT-SIG1 '
                         '(IEEE80211_HTSIG_BPSK_FALLBACK=1, opt-in, non-standard)')
```

### 7.3 Env-Var Injection

```python
if args.htsig_bpsk_fallback:
    os.environ['IEEE80211_HTSIG_BPSK_FALLBACK'] = '1'
    print("[TEST] Phase 143 BPSK-HT-SIG fallback ENABLED", flush=True)
```

---

## 8. Verification Plan

### 8.1 T1 — Software Loopback Correctness

```bash
python test_usrp_minimal_loopback.py --htsig-bpsk-fallback --duration 5
```

**Success criterion:** `FCS_OK >= 1`.

This proves TX/RX symbol mapping, bit extraction, LLR, and CRC paths are consistent.

### 8.2 T2 — Cross-Mode Rejection

Run two back-to-back tests:

```bash
# TX standard, RX fallback → should fail HT-SIG
IEEE80211_HTSIG_BPSK_FALLBACK=1 python test_usrp_minimal_loopback.py --duration 5
# (but TX must not have the flag, so this test needs a two-process variant)
```

A simpler variant: send standard frames from one flowgraph and receive with fallback enabled; confirm `FCS_OK=0` and `HT_SIG_CAND` remains non-zero but all CRC fail. This validates that mismatched modes degrade safely.

### 8.3 T3 — USRP Realtime Breakthrough

```bash
python test_usrp_minimal_loopback.py \
  --freq 5250 --tx-gain 0 --rx-gain 31.5 --rate 20 \
  --warmup 60 --duration 30 --rx-subdev A:0 \
  --phase139-on --wiener-on --htsig-bpsk-fallback
```

**Primary success criterion:** `FCS_OK >= 1`.

**Secondary metrics:**
- `HT_SIG_CAND > 0`
- `best_metric` should drop to BPSK-typical values (≤10)
- `avg_snr_htsig` no longer the bottleneck

### 8.4 T4 — Parameter Sweep

Repeat T3 with:

- tx-gain: 0, 10, 20
- rx-gain: 20, 31.5
- `--uhd-tune` on/off
- `--wiener-on` on/off
- `--phase139-on` on/off

Compare against the same matrix **without** `--htsig-bpsk-fallback` to quantify the improvement.

---

## 9. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Incomplete RX axis switch (some QBPSK-specific path still reads imag) | T1 loopback fails immediately and pinpoints the inconsistency; all HT-SIG bit/LLR/CPE paths are gated by `d_htsig_bpsk_fallback`. |
| Pilot CPE reference axis mismatch | Explicitly switched to real-axis reference in fallback mode; T1 validates. |
| Existing fine-rotation / QBPSK detector code interferes | In fallback mode rotation is fixed to 0 and only `invert_a`/`invert_b` are tried. |
| Standard-device coexistence misunderstood | L-SIG remains standard; standard devices silently drop the frame. Documented in Section 4. |
| TX/RX env-var mismatch | RX behavior is receiver-local; TX behavior is transmitter-local. Mismatch causes CRC fail, not crash. |

---

## 10. Implementation Steps

1. **TX:** modify `examples/mixed_mode_carrier_allocator.py` (+6 lines).
2. **RX header:** add `d_htsig_bpsk_fallback` to `lib/frame_equalizer_impl.h`.
3. **RX init:** read env var in `lib/frame_equalizer_impl.cc` constructor.
4. **RX bit extraction:** switch HT-SIG0/HT-SIG1 bit decision axis.
5. **RX LLR:** switch LLR sign axis.
6. **RX CPE:** switch pilot reference axis.
7. **RX rotation:** disable QBPSK rotation search in fallback mode.
8. **Test script:** add `--htsig-bpsk-fallback` to `test_usrp_minimal_loopback.py`.
9. Build: `make && make install`.
10. T1 loopback validation.
11. T3 USRP realtime validation.
12. Write verdict note.

---

## 11. Relation to Prior Work

- Builds on **Phase 139 2-way H52** (L-SIG wall already broken).
- Builds on **Phase 141/142 Wiener** (H estimation quality improved).
- Does **not** reuse or modify Kalman/DDE/cross-frame/freq-smooth code; it attacks the problem upstream of the equalizer by removing the QBPSK constraint.
- Preserves all prior env vars as opt-in; this flag is additive.

---

## 12. Success Criteria

1. **Mandatory:** `FCS_OK >= 1` on USRP X310 + UBX-160 realtime same-board cable at 5250 MHz.
2. **Regression:** With flag unset, existing loopback and USRP behavior is unchanged.
3. **Diagnostic:** `best_metric` for HT-SIG candidates should be ≤ 10 (viterbi threshold) under fallback mode.

---

## 13. Next Step

Proceed to implementation plan via `writing-plans` skill.
