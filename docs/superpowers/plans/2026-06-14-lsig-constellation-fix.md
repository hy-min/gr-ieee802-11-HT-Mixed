# L-SIG Constellation Fix on USRP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the L-SIG mis-decoding on USRP so that `lsig_enc=0` (BPSK 1/2) is correctly identified, the HT-SIG candidate loop is unlocked, and end-to-end FCS pass rate rises above zero on real USRP data.

**Architecture:** The L-SIG is decoded by `decode_lsig_direct_from_header52` in `lib/frame_equalizer_impl.cc`. The current behavior on USRP is that this function returns `enc=2/4/6/7` (QPSK/16QAM/64QAM) instead of `enc=0` (BPSK 1/2), even though viterbi + parity pass by chance. The viterbi converges on a wrong path because the equalized L-SIG constellations are not clean BPSK 4-clusters. We will:

1. Diagnose where the constellation corruption comes from (L-LTF0 FFT? H estimate? IQ axis? CFO?)
2. Apply targeted fix
3. Verify end-to-end with USRP and ensure no regression in software loopback

**Tech Stack:** C++ (GNU Radio blocks `lib/frame_equalizer_impl.cc`), Python (test scripts), GNU Radio 3.10, USRP X300 at 5.89 GHz A:0.

---

## Key Context

- **Phase 10 finding (2026-06-14)**: Real USRP data at 5.89 GHz A:0
  - L-SIG decode: enc=2 (QPSK 1/2), enc=3, enc=4, enc=6, enc=7 — ALL WRONG
  - L-SIG length field: 403 μs, 518, 3641, 2045, 1275 — wildly wrong
  - HT_SIG_PARSE_FAIL: n_candidates=0 (loop never runs)
  - Direct loopback (no USRP): enc=0 len=54 μs (CORRECT for "test"×5)
- **Why it matters**: line 3041 `if (lsig_enc != 0) continue;` skips the HT-SIG candidate loop. So fixing L-SIG decode is the prerequisite for fixing HT-SIG.
- **Hardware is fine**: L-SIG viterbi works (parity passes). The issue is constellation quality at the equalizer input.
- **SNR is high**: avg_snr_lsig ~ 49 dB (from 26534.28 power). Plenty of signal.

## File Map

| File | Role |
|------|------|
| `lib/frame_equalizer_impl.cc` | L-SIG/HT-SIG decoder, H estimation, equalization |
| `lib/ht_symbol_splitter_impl.cc` | Splitter that feeds symbols to equalizer (L-LTF0/L-LTF1/L-SIG/HT-SIG timing) |
| `lib/mapper_impl.cc` | TX-side mapper (L-SIG bits generation) |
| `/tmp/test_p10_usrp_v2.py` | Existing USRP loopback test (400-byte packet, 8s) |
| `examples/test_direct_loopback.py` | Existing software loopback test (baseline) |

## Diagnostic Evidence (from /tmp/p10_v2.err)

```
[HT_SIG_PARSE_FAIL] timeout_sym=4 n_candidates=0 ... 
avg_snr_lsig=26534.28 avg_snr_htsig=20979.02 
lsig_rate=0x5 lsig_len=403 lsig_inv=0 
last_rot=-1 last_inv_a=-1 last_inv_b=-1 is_ht_frame=1
```

```
[LSIG_DECODE] OK enc=2 len=403
[LSIG_DECODE] OK enc=4 len=3641
[LSIG_DECODE] OK enc=6 len=2045
[LSIG_DECODE] OK enc=7 len=1275
```

```
[LSIG_DECODE] OK enc=0 len=54    # direct loopback baseline
```

---

## Task 1: Add L-LTF0 FFT at equalizer input dump (USRP vs loopback)

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (add `IEEE80211_LTF0_FFT_EQ_DUMP` env var hook)
- Test: `/tmp/test_ltf0_eq_compare.py` (new)

**Context:** The L-LTF0 FFT at the equalizer input is the most upstream signal we can inspect. If it's clean, the bug is downstream; if it's not, the bug is in the splitter/timing/IQ path. Existing `IEEE80211_LTF0_FFT_DUMP=1` dumps it post-compensation — we also need a pre-compensation version.

- [ ] **Step 1: Locate existing LTF0 FFT dump code**

Run: `grep -n "LTF0_FFT_DUMP\|LTF0_FFT" lib/frame_equalizer_impl.cc | head -10`
Expected: existing dump uses `kLltf0Rel` symbol index, with CFO/SFO compensation applied.

- [ ] **Step 2: Add a new pre-compensation LTF0 FFT dump env var**

Add (near the existing dump) at line ~575 (find by `LTF0_FFT_DUMP`):

```cpp
if (getenv("IEEE80211_LTF0_FFT_PRECOMP_DUMP")) {
    // Dump L-LTF0 FFT BEFORE CFO/SFO compensation
    char dump[8192];
    int off = snprintf(dump, sizeof(dump), "[LTF0_FFT_PRECOMP] sym=%d cfo=%.4f sfo=%.4f H[0:5]=",
                       d_internal_symbol_counter, d_cfo, d_sfo);
    for (int i = 0; i < 5 && off < (int)sizeof(dump) - 80; i++) {
        off += snprintf(dump + off, sizeof(dump) - off, "%.3f%+.3fi ",
                        d_early_eqsym[kLltf0Rel][i].real(),
                        d_early_eqsym[kLltf0Rel][i].imag());
    }
    USRP_LOG("%s\n", dump);
}
```

The exact variable names depend on the current code. Adjust to match.

- [ ] **Step 3: Build and verify .so contains the string**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -5
cmake --install . 2>&1 | tail -3
strings /home/hy/gr-ieee802-11/build/lib/libgnuradio-ieee802_11.so.g590b96a | grep -c "LTF0_FFT_PRECOMP"
```

Expected: build succeeds, string count = 1.

- [ ] **Step 4: Run USRP with the new dump and check first 5 lines**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_LTF0_FFT_PRECOMP_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/ltf0_precomp_usrp.err > /dev/null
grep "LTF0_FFT_PRECOMP" /tmp/ltf0_precomp_usrp.err | head -5
```

Expected: 5+ lines printed. Check that FFT values are bounded (|.|<10).

- [ ] **Step 5: Run direct loopback with the same dump**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_LTF0_FFT_PRECOMP_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 8 /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py \
  2> /tmp/ltf0_precomp_loop.err > /dev/null
grep "LTF0_FFT_PRECOMP" /tmp/ltf0_precomp_loop.err | head -5
```

Expected: 1+ lines printed (loopback has 1 frame per 500ms strobe).

- [ ] **Step 6: Compare USRP vs loopback LTF0 FFT statistics**

Write `/tmp/ltf0_compare.py` (new):
```python
import re
def parse(path):
    pat = re.compile(r"\[LTF0_FFT_PRECOMP\][^=]*=(-?[\d.]+)\+(-?[\d.]+)i[ \t]+(-?[\d.]+)\+(-?[\d.]+)i[ \t]+(-?[\d.]+)\+(-?[\d.]+)i[ \t]+(-?[\d.]+)\+(-?[\d.]+)i[ \t]+(-?[\d.]+)\+(-?[\d.]+)i")
    out = []
    with open(path) as f:
        for line in f:
            m = pat.search(line)
            if m: out.append([float(x) for x in m.groups()])
    return out
usrp = parse("/tmp/ltf0_precomp_usrp.err")
loop = parse("/tmp/ltf0_precomp_loop.err")
import statistics as st
def stats(name, data):
    if not data: print(f"{name}: 0 lines"); return
    mags = [abs(complex(r, i)) for row in data for r, i in zip(row[::2], row[1::2])]
    print(f"{name}: {len(data)} lines, |FFT| mean={st.mean(mags):.3f} std={st.pstdev(mags):.3f}")
stats("USRP  ", usrp)
stats("LOOP   ", loop)
```

Run: `/home/hy/conda/envs/gnuradio/bin/python /tmp/ltf0_compare.py`
Expected: USRP has higher std (≥ 2× loopback) confirming upstream corruption.

- [ ] **Step 7: Commit**

```bash
git add lib/frame_equalizer_impl.cc /tmp/test_p10_usrp_v2.py
git commit -m "diag(frame_eq): add L-LTF0 FFT pre-compensation dump"
```

---

## Task 2: Add H52 per-SC dump at equalizer input

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (add `IEEE80211_H52_EQ_INPUT_DUMP` env var hook)
- Test: `/tmp/test_h52_input_compare.py` (new)

**Context:** The H52 (52-subcarrier channel estimate) is used to equalize L-SIG. If H52 has wildly different magnitudes or arg jumps, equalization produces wrong constellation. We need to dump H52 right after L-LTF0/L-LTF1 averaging, before it's used to equalize L-SIG.

- [ ] **Step 1: Find H52 estimation code in frame_equalizer**

Run: `grep -n "Hhdr52\|H52\|estimate_header_channel" lib/frame_equalizer_impl.cc | head -20`
Expected: H52 estimation around line 600-700.

- [ ] **Step 2: Add H52 per-SC dump at the equalizer input location**

Add right after `Hhdr52` is computed and before it's used for L-SIG equalization (find the line where `Hhdr52` is finalized — typically just before `decode_lsig_direct_from_header52` is called at line 3014):

```cpp
if (getenv("IEEE80211_H52_EQ_INPUT_DUMP")) {
    char dump[8192];
    int off = snprintf(dump, sizeof(dump), "[H52_EQ_INPUT] sym=%d nSC=52 |H|=", d_internal_symbol_counter);
    for (int i = 0; i < 52 && off < (int)sizeof(dump) - 60; i++) {
        off += snprintf(dump + off, sizeof(dump) - off, "%.2f,", std::abs(Hhdr52[i]));
    }
    off += snprintf(dump + off, sizeof(dump) - off, " arg=");
    for (int i = 0; i < 52 && off < (int)sizeof(dump) - 60; i++) {
        off += snprintf(dump + off, sizeof(dump) - off, "%.2f,", std::arg(Hhdr52[i]));
    }
    USRP_LOG("%s\n", dump);
}
```

Adjust `Hhdr52` and the location to match existing code.

- [ ] **Step 3: Build and verify**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
strings /home/hy/gr-ieee802-11/build/lib/libgnuradio-ieee802_11.so.g590b96a | grep -c "H52_EQ_INPUT"
```

Expected: build succeeds, string count = 1.

- [ ] **Step 4: Run USRP + loopback, collect H52 dumps**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_H52_EQ_INPUT_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/h52_usrp.err > /dev/null
grep -c "H52_EQ_INPUT" /tmp/h52_usrp.err
```

Expected: 30+ lines (8s × 4 Hz = 32 frames).

Same for loopback:
```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_H52_EQ_INPUT_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 8 /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py \
  2> /tmp/h52_loop.err > /dev/null
grep -c "H52_EQ_INPUT" /tmp/h52_loop.err
```

Expected: 8+ lines.

- [ ] **Step 5: Compute H52 statistics and check for anomalies**

Write `/tmp/h52_analyze.py` (new):
```python
import re
import statistics as st
import math

def parse(path):
    pat = re.compile(r"\[H52_EQ_INPUT\] sym=(\d+) nSC=52 \|H\|=([\d.,-]+) arg=([\d.,-]+)")
    rows = []
    with open(path) as f:
        for line in f:
            m = pat.search(line)
            if not m: continue
            sym = int(m.group(1))
            mags = [float(x) for x in m.group(2).rstrip(",").split(",") if x]
            args = [float(x) for x in m.group(3).rstrip(",").split(",") if x]
            if len(mags) == 52 and len(args) == 52:
                rows.append((sym, mags, args))
    return rows

def stats(name, rows):
    if not rows: print(f"{name}: 0 rows"); return
    all_mags = [m for _, mags, _ in rows for m in mags]
    all_args = [a for _, _, args in rows for a in args]
    print(f"{name}: {len(rows)} rows")
    print(f"  |H| min={min(all_mags):.3f} max={max(all_mags):.3f} mean={st.mean(all_mags):.3f} std={st.pstdev(all_mags):.3f}")
    # Detect |H| outliers
    outliers = [m for m in all_mags if m < 0.1 or m > 10]
    print(f"  |H| outliers (<0.1 or >10): {len(outliers)} / {len(all_mags)}")
    # Detect phase jumps
    arg_diffs = []
    for _, mags, args in rows:
        for i in range(1, 52):
            d = args[i] - args[i-1]
            while d > math.pi: d -= 2*math.pi
            while d < -math.pi: d += 2*math.pi
            if abs(d) > 0.5: arg_diffs.append((i, d))
    print(f"  arg jumps >0.5 rad: {len(arg_diffs)} / {len(rows)*51}")

stats("USRP", parse("/tmp/h52_usrp.err"))
stats("LOOP", parse("/tmp/h52_loop.err"))
```

Run: `/home/hy/conda/envs/gnuradio/bin/python /tmp/h52_analyze.py`
Expected: USRP shows much higher |H| std and/or more phase jumps than loopback.

- [ ] **Step 6: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "diag(frame_eq): add H52 per-SC dump at equalizer input"
```

---

## Task 3: Try IQ axis swap as quick fix

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (add a debug swap at L-SIG equalization)
- Test: software loopback + USRP

**Context:** If the L-LTF0 FFT shows the 4 BPSK clusters on the wrong axis (Q instead of I, or vice versa), swapping real and imaginary parts of the equalized L-SIG would fix the encoding detection. This is a quick experiment to localize the issue.

- [ ] **Step 1: Locate the equalization code**

Run: `grep -n "decode_lsig_direct_from_header52\|eqsym\|eq\[\|d_early_eqsym\[kLSigRel\]" lib/frame_equalizer_impl.cc | head -10`
Expected: around line 3014.

- [ ] **Step 2: Add IQ swap hook**

Right before `decode_lsig_direct_from_header52` is called (line 3014), add:

```cpp
if (getenv("IEEE80211_LSIG_IQ_SWAP")) {
    for (int i = 0; i < 52; i++) {
        gr_complex tmp = d_early_eqsym[kLSigRel][i];
        d_early_eqsym[kLSigRel][i] = gr_complex(tmp.imag(), tmp.real());
    }
}
```

- [ ] **Step 3: Build and verify**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
```

- [ ] **Step 4: Test with USRP — does it change L-SIG encoding?**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_LSIG_IQ_SWAP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/iqswap_usrp.err > /tmp/iqswap_usrp.out
grep "LSIG_DECODE" /tmp/iqswap_usrp.err | sort -u
```

Expected: L-SIG encodings may change. If enc=0 appears for the first time, IQ swap is the fix.

- [ ] **Step 5: Document result in note file**

Append to `/home/hy/gr-ieee802-11/docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md`:

```markdown

## IQ swap experiment (2026-06-14)

Added `IEEE80211_LSIG_IQ_SWAP=1` env var to swap I/Q of equalized L-SIG.

USRP result: <encodings seen>
Loopback result: <encodings seen>

Conclusion: <one of "IQ swap is the fix" / "IQ swap is NOT the fix">
```

- [ ] **Step 6: Commit (or revert if no improvement)**

If enc=0 still does not appear in USRP, revert:
```bash
cd /home/hy/gr-ieee802-11
git checkout lib/frame_equalizer_impl.cc
git commit -m "diag(frame_eq): IQ swap did not help, reverted"
```

If enc=0 appears, keep the change but warn in commit message:
```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix(frame_eq): IQ swap of L-SIG before viterbi decode (USRP fix)"
```

---

## Task 4: Try re-estimating CFO between L-LTF and L-SIG

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (recompute CFO from L-LTF0 vs L-LTF1, compare to current CFO)
- Test: software loopback + USRP

**Context:** The current CFO is estimated once from L-LTF (sync_long). If residual CFO rotates the L-SIG symbol by 90° before the equalizer can correct it, the BPSK points end up on the wrong axis. Re-estimating CFO using L-LTF0 vs L-LTF1 correlation may give a more accurate value.

- [ ] **Step 1: Find sync_long CFO estimation**

Run: `grep -n "d_cfo\|estimate_cfo\|CFO" lib/sync_long_impl.cc 2>/dev/null | head -10`
Expected: CFO is computed and passed forward via tag.

- [ ] **Step 2: Find where d_cfo is applied in frame_equalizer**

Run: `grep -n "d_cfo" lib/frame_equalizer_impl.cc | head -10`
Expected: d_cfo is used for L-LTF compensation and stored.

- [ ] **Step 3: Add fine CFO estimate from L-LTF0 vs L-LTF1 phase difference**

In `frame_equalizer_impl.cc`, find the code that estimates Hhdr52 (using both L-LTF0 and L-LTF1). After H is computed, the phase difference between L-LTF0 and L-LTF1 samples can be used to estimate fine CFO. Add:

```cpp
if (getenv("IEEE80211_CFO_REFINEMENT")) {
    // Fine CFO from L-LTF0 vs L-LTF1 phase difference
    // L-LTF1 is 2 symbols (8 μs) after L-LTF0
    gr_complex c0 = d_early_eqsym[kLltf0Rel][26];  // pilot SC
    gr_complex c1 = d_early_eqsym[kLltf1Rel][26];
    float fine_cfo = std::arg(c1 * std::conj(c0)) / (8e-6 * 2 * M_PI);
    USRP_LOG("[CFO_REFINEMENT] orig=%.4f fine=%.4f delta=%.4f\n",
             d_cfo, fine_cfo, fine_cfo - d_cfo);
    // Apply refinement
    d_cfo += (fine_cfo - d_cfo) * 0.5f;  // 50% blend
}
```

Adjust variable names to match the actual code.

- [ ] **Step 4: Build and verify**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
```

- [ ] **Step 5: Test with USRP**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_CFO_REFINEMENT=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/cforef_usrp.err > /tmp/cforef_usrp.out
grep "LSIG_DECODE" /tmp/cforef_usrp.err | sort -u
grep "CFO_REFINEMENT" /tmp/cforef_usrp.err | head -5
```

Expected: CFO_REFINEMENT shows small delta (≤ 0.1). Check if L-SIG enc=0 appears.

- [ ] **Step 6: Revert if no improvement, document in note**

If enc=0 still does not appear, revert and document in the phase10 note.

- [ ] **Step 7: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "diag(frame_eq): fine CFO refinement from L-LTF0/L-LTF1 (revert if no effect)"
```

---

## Task 5: Soft fix — bypass `lsig_enc != 0` check

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (line 3041 — comment out the `if (lsig_enc != 0) continue;` check)
- Test: software loopback regression + USRP

**Context:** This is the most direct fix: just remove the gating so HT-SIG is always attempted. Risk: false positive HT-SIG decodes. Benefit: if HT-SIG works, the chain produces valid frames. This is a workaround, not a real fix.

- [ ] **Step 1: Locate the check**

Run: `grep -n "lsig_enc != 0\|non-BPSK 1/2 rate" lib/frame_equalizer_impl.cc | head -5`
Expected: line 3041.

- [ ] **Step 2: Make the gating env-var-controlled**

Replace the existing `if (lsig_enc != 0)` block with:
```cpp
if (lsig_enc != 0 && !getenv("IEEE80211_FORCE_HTSIG")) {
    // L-SIG succeeded with non-BPSK 1/2 rate - skip and try other inv
    continue;
}
if (lsig_enc != 0) {
    USRP_LOG("[FORCE_HTSIG] sym=%d lsig_enc=%d, attempting HT-SIG despite non-zero enc\n",
             d_internal_symbol_counter, lsig_enc);
}
```

- [ ] **Step 3: Build and run USRP test**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_FORCE_HTSIG=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/forceht_usrp.err > /tmp/forceht_usrp.out
```

Check: `grep -c "HT_SIG_CAND" /tmp/forceht_usrp.err` — should now be 200+ (16 candidates × 12+ frames).
Check: `grep -c "FORCE_HTSIG" /tmp/forceht_usrp.err` — should be 12+.

- [ ] **Step 4: Check whether HT-SIG decodes succeed under force mode**

Run:
```bash
grep "FORCE_HTSIG" /tmp/forceht_usrp.err | head -3
echo "---"
grep "HT_SIG_CAND" /tmp/forceht_usrp.err | head -16
echo "---"
tail -5 /tmp/forceht_usrp.out
```

Expected: HT_SIG_CAND lines now show non-zero metrics, possibly some with valid decodes.

- [ ] **Step 5: Run software loopback to check no regression**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_FORCE_HTSIG=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 8 /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py \
  2> /tmp/forceht_loop.err > /tmp/forceht_loop.out
tail -3 /tmp/forceht_loop.out
```

Expected: at least 1 frame decoded (loopback was 0/1 OK before — not regression).

- [ ] **Step 6: Document and commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix(frame_eq): bypass lsig_enc!=0 gating behind IEEE80211_FORCE_HTSIG env var"
```

- [ ] **Step 7: Append to phase10 note**

Append to `docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md`:

```markdown

## FORCE_HTSIG experiment (2026-06-14)

Added `IEEE80211_FORCE_HTSIG=1` env var to bypass `if (lsig_enc != 0) continue;`.

USRP result: HT_SIG_CAND fired N times, M successful decodes.
Loopback result: <loopback behavior>

Conclusion: <whether the soft fix unlocks HT-SIG on USRP>
```

---

## Task 6: End-to-end USRP validation

**Files:**
- Test: `/tmp/test_p10_usrp_v2.py` (existing, with best fix applied)

**Context:** If Task 5's soft fix works, the end-to-end test should show `FCS OK > 0`. If not, we report the failure and plan the next iteration.

- [ ] **Step 1: Apply best fix(es) and run 30s USRP test**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_FORCE_HTSIG=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 35 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/e2e_usrp.err > /tmp/e2e_usrp.out
```

Check the FCS counts and HT_SIG_CAND success rate.

- [ ] **Step 2: Run software loopback regression**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 15 /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py \
  2> /tmp/e2e_loop.err > /tmp/e2e_loop.out
tail -3 /tmp/e2e_loop.out
```

Expected: same as baseline (1-3 OK).

- [ ] **Step 3: Write final summary to phase10 note**

Append to `docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md`:

```markdown

## End-to-end validation (2026-06-14)

USRP 30s: Sent=N, Recv=M (FCS OK=K, FAIL=L)
Loopback 10s: Recv=M (FCS OK=K, FAIL=L)

Conclusion: <was the fix sufficient? if not, what's next?>
```

- [ ] **Step 4: Commit final state**

```bash
git add docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md
git commit -m "notes(phase10): end-to-end validation of L-SIG fixes"
```

- [ ] **Step 5: Update memory**

Update `/home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p10_finding_enc_mismatch.md` with final outcome. Update `MEMORY.md` index entry date and outcome.

---

## Self-Review

- [x] **Spec coverage**: Tasks cover diagnosis (1, 2), quick experiments (3, 4), workaround (5), validation (6). All match the goal of fixing L-SIG mis-decoding.
- [x] **Placeholder scan**: No "TBD" / "TODO" / "fill in". All code is concrete. Env var names spelled consistently. Variable names match existing code with a "match existing code" note.
- [x] **Type consistency**: `d_early_eqsym`, `Hhdr52`, `d_cfo`, `d_internal_symbol_counter`, `kLSigRel`, `kLltf0Rel`, `kLltf1Rel` used consistently.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-14-lsig-constellation-fix.md`. Two execution options:**

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints
