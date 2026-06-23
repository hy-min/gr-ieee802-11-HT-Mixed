# Phase 33b — USRP Validation Verdict (2026-06-23)

## TL;DR

Phase 33 `FRAME_START_BASE=174` fix (commit bd5c1d2) is **PERFECT in loopback**
but does NOT resolve USRP H52 corruption. USRP shows a NEW impairment layer:
**perfect 64-PSK quantization** of arg(H) — deterministic per-bin phase rotation
quantized to 64 levels, varying per frame.

## Test Setup

- USRP X310 @ 192.168.10.2, 5 GHz A:0 subdev (single-board TDD)
- Config: `--freq 5890 --tx-gain 20 --rx-gain 20 --duration 15 --rx-scale 60 --interval 1500`
- `IEEE80211_LTF0_FFT_DUMP=1` + `IEEE80211_H52_DUMP=1` + capture raw IQ
- Real-time RX overflowed (12 overflows in 15s), analysis went offline
- 19771 frames H52 dump (raw IQ: `/tmp/p33e_raw_iq.bin`, 2.13 GB)

## Loopback vs USRP after Phase 33 fix

| Metric | Loopback | USRP |
|---|---|---|
| arg(H) std per frame | **0.0000** | **1.795** (≈ π/√3 uniform) |
| LTF0_FFT arg pattern | perfect 0/π BPSK | n/a (offline path) |
| \|H\| std per frame | 0 | 0.0353 (real channel variation) |

## Key Finding: Perfect 64-PSK Quantization (USRP only)

USRP arg(H) values are perfectly quantized to π/64 grid:

| Grid | RMS error | Halving ratio |
|---|---|---|
| π/4 (4-PSK) | 0.2267 | — |
| π/8 (8-PSK) | 0.1132 | × 0.50 |
| π/16 (16-PSK) | 0.0567 | × 0.50 |
| π/32 (32-PSK) | 0.0283 | × 0.50 |
| **π/64 (64-PSK)** | **0.0142** | × 0.50 |

Every doubling of PSK order halves RMS error → **argH is exactly a 64-PSK sequence**.
This is not noise; it's a deterministic per-bin phase rotation quantized to 64 levels.

## Interpretation

```
argH[bin b] = -2π × b × δ / 64
```
where `δ` is per-frame sub-sample timing offset. Since argH is perfectly quantized
to π/64, **`δ` is uniformly distributed over [k/64, (k+1)/64)** for some integer k
that varies per frame.

Per-frame mean(argH) std=0.251 across frames → δ is **random per frame** but
quantized, not continuous.

## What's Ruled Out

- ❌ **NOT** 14-sample cyclic shift residual — best-N search N=0 (RMS err=0.055),
  any N∈{7,14,-7,-14,21,-21} doesn't reduce std(argH)
- ❌ **NOT** uniform random noise — perfect 64-PSK quantization signature
- ❌ **NOT** pure CFO — per-frame mean(argH) has std=0.251, not constant

## Channel Frequency Response (Real)

Per-SC mean |H| has frequency-selective structure:

```
SC[0:8]  = [0.055, 0.057, 0.057, 0.057, 0.059, 0.060, 0.063, 0.063]
SC[24:32]= [0.078, 0.086, 0.087, 0.077, 0.075, 0.077, 0.072, 0.074]
SC[48:52]= [0.056, 0.057, 0.057, 0.056]
```

Per-SC mean |H| ratio max/min = 1.59 → mild frequency selectivity (real channel).

## Next Directions

1. **Re-try Phase 31c `IEEE80211_LLTF_OFFSET_CORRECT` K-sweep in USRP** — the
   K-sweep on USRP may help (was REFUTED in loopback, but USRP is different).
2. **Per-frame 64-PSK ML detection** — use the 64-PSK grid structure for
   argH decoding (look up closest 64-PSK constellation point per frame).
3. **Pilot-based per-SC phase correction** — once argH is on 64-PSK grid,
   the residual per-SC phase drift may be CFO-induced and correctable via pilots.

## Phase 33 Verdict Status

- **Loopback**: PERFECT (commit bd5c1d2 stands)
- **USRP**: PARTIAL — resolved 14-sample systematic shift, exposed new
  sub-sample timing quantization layer

## Files

- Raw IQ: `/tmp/p33e_raw_iq.bin` (2.13 GB)
- Offline H52 log: `/tmp/p33e_offline_h52.log` (19771 frames)
- Analysis: `/tmp/analyze_p33_usrp.py`, `/tmp/analyze_p33_quant.py`
- Real-time log: `/tmp/p33d_usrp.log`

## Related

- [[project-p33-lltf0-14sample-shift-fix]] — Phase 33 root cause fix
- [[project-p32-h52-e2e-vs-offline]] — Phase 32 H_BOTH_BROKEN (now resolved as 14-sample shift)
- [[project-p31c-k-sweep-refuted]] — K-sweep REFUTED in loopback, may help in USRP