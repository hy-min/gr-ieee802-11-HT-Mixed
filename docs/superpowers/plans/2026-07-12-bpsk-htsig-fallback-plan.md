# Phase 143: BPSK-HT-SIG Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the BPSK-HT-SIG fallback architecture that replaces QBPSK with BPSK for HT-SIG0/HT-SIG1, breaking the USRP 1.77 rad per-SC phase-noise floor and achieving realtime `FCS_OK >= 1`.

**Architecture:** A single coordinated TX/RX switch (`IEEE80211_HTSIG_BPSK_FALLBACK=1`) removes the 90° QBPSK rotation at the transmitter and switches the receiver's HT-SIG bit/LLR/CPE decision axis from imaginary to real. The L-SIG and HT-DATA paths remain unchanged, preserving a standard-compatible default.

**Tech Stack:** GNU Radio 3.10, C++17, Python 3, gr-ieee802-11 PHY.

---

## File Structure

| File | Responsibility | Change Type |
|------|----------------|-------------|
| `examples/mixed_mode_carrier_allocator.py` | TX: conditionally skip the ×j QBPSK rotation for HT-SIG symbols and pilots | Modify |
| `lib/frame_equalizer_impl.h` | RX: declare the `d_htsig_bpsk_fallback` state member | Modify |
| `lib/frame_equalizer_impl.cc` | RX: read env var, switch bit/LLR/CPE axes, disable QBPSK rotation search in fallback mode | Modify |
| `test_usrp_minimal_loopback.py` | Test harness: add `--htsig-bpsk-fallback` CLI flag that sets the env var | Modify |
| `docs/superpowers/specs/2026-07-12-bpsk-htsig-fallback-design.md` | Approved design reference | Read-only |

---

## Task 1: TX — Make HT-SIG modulation selectable (BPSK vs QBPSK)

**Files:**
- Modify: `examples/mixed_mode_carrier_allocator.py:85`
- Modify: `examples/mixed_mode_carrier_allocator.py:261-267`

- [ ] **Step 1.1: Read the opt-in env var in the constructor**

Add after line 100 (after `self._n_sync = len(self._sync_words)`):

```python
        # Phase 143: BPSK-HT-SIG fallback (non-standard, TX/RX coordinated).
        # When enabled, HT-SIG0/HT-SIG1 are sent as BPSK on the real axis
        # instead of QBPSK on the imaginary axis. This doubles the angular
        # decision margin and is intended to break the USRP 1.77 rad per-SC
        # phase-noise floor.
        self._htsig_bpsk_fallback = (
            os.environ.get('IEEE80211_HTSIG_BPSK_FALLBACK') == '1'
        )
```

- [ ] **Step 1.2: Conditionally apply the QBPSK rotation**

Replace lines 261–267:

```python
            # HT-SIG uses QBPSK (90° rotation on Q-axis)
            # Rotate HT-SIG data symbols by multiplying by j
            htsig1_bpsk48 = htsig1_bpsk48 * 1j
            htsig2_bpsk48 = htsig2_bpsk48 * 1j

            # HT-SIG pilots also need 90° rotation
            ht_sig_pilot_values = [pv * 1j for pv in self._legacy_pilot_values]
```

with:

```python
            if not self._htsig_bpsk_fallback:
                # Standard QBPSK HT-SIG (90° rotation on Q-axis)
                htsig1_bpsk48 = htsig1_bpsk48 * 1j
                htsig2_bpsk48 = htsig2_bpsk48 * 1j
                ht_sig_pilot_values = [pv * 1j for pv in self._legacy_pilot_values]
            else:
                # Phase 143 fallback: keep HT-SIG as BPSK on real axis
                ht_sig_pilot_values = self._legacy_pilot_values
```

- [ ] **Step 1.3: Verify the Python file still parses**

Run:

```bash
cd /home/hy/gr-ieee802-11
python -m py_compile examples/mixed_mode_carrier_allocator.py
```

Expected: no output (success).

- [ ] **Step 1.4: Commit the TX change**

```bash
cd /home/hy/gr-ieee802-11
git add examples/mixed_mode_carrier_allocator.py
git commit -m "feat(p143): TX BPSK-HT-SIG fallback switch"
```

---

## Task 2: RX — Add fallback state member and env-var parsing

**Files:**
- Modify: `lib/frame_equalizer_impl.h:317-328`
- Modify: `lib/frame_equalizer_impl.cc:4784-4802`

- [ ] **Step 2.1: Declare the fallback flag in the header**

Add after line 328 (after `d_ht_sig_pilot_refine`):

```cpp
    // Phase 143: BPSK-HT-SIG fallback. When true, HT-SIG0/HT-SIG1 are
    // decoded as BPSK on the real axis instead of QBPSK on the imaginary
    // axis. Coordinated with TX via IEEE80211_HTSIG_BPSK_FALLBACK=1.
    // Default OFF preserves standard 802.11n behavior.
    bool d_htsig_bpsk_fallback;
```

- [ ] **Step 2.2: Initialize the flag in the constructor**

Add after line 4783 (inside the constructor body, before `d_bpsk = make_bpsk_constellation();`):

```cpp
      d_htsig_bpsk_fallback(false)
```

- [ ] **Step 2.3: Read the env var**

Add after line 4802 (after the `IEEE80211_H_LLTF1` block):

```cpp
    // Phase 143: BPSK-HT-SIG fallback (non-standard, TX/RX coordinated).
    const char* env_htsig_bpsk = std::getenv("IEEE80211_HTSIG_BPSK_FALLBACK");
    d_htsig_bpsk_fallback = (env_htsig_bpsk && env_htsig_bpsk[0] == '1');
    if (d_htsig_bpsk_fallback) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_BPSK_FALLBACK=1 (HT-SIG decoded as BPSK)\n";
    }
```

- [ ] **Step 2.4: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add lib/frame_equalizer_impl.h lib/frame_equalizer_impl.cc
git commit -m "feat(p143): add RX d_htsig_bpsk_fallback flag and env parsing"
```

---

## Task 3: RX — Switch HT-SIG0/HT-SIG1 bit-extraction axis

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:3701`
- Modify: `lib/frame_equalizer_impl.cc:3948`

- [ ] **Step 3.1: HT-SIG0 bit decision**

Replace line 3701:

```cpp
            eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0;
```

with:

```cpp
            eqbits48_a[i] = d_htsig_bpsk_fallback
                ? ((eq.real() >= 0.0f) ? 1 : 0)
                : ((eq.imag() >= 0.0f) ? 1 : 0);
```

- [ ] **Step 3.2: HT-SIG1 bit decision**

Replace line 3948:

```cpp
            eqbits48_b[i] = (eq.imag() >= 0.0f) ? 1 : 0;
```

with:

```cpp
            eqbits48_b[i] = d_htsig_bpsk_fallback
                ? ((eq.real() >= 0.0f) ? 1 : 0)
                : ((eq.imag() >= 0.0f) ? 1 : 0);
```

- [ ] **Step 3.3: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add lib/frame_equalizer_impl.cc
git commit -m "feat(p143): switch HT-SIG bit-extraction axis for BPSK fallback"
```

---

## Task 4: RX — Switch soft-LLR sign axis

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:3716`
- Modify: `lib/frame_equalizer_impl.cc:3957`

- [ ] **Step 4.1: HT-SIG0 LLR sign**

Replace lines 3714–3717:

```cpp
                    // Phase 44 formula: sign(eq.imag()) * |H|/max(|H|)
                    float conf = h_mag / max_h_a;
                    float s = (eq.imag() >= 0.0f) ? 1.0f : -1.0f;
                    llr48_a[i] = s * conf;
```

with:

```cpp
                    // Phase 44 formula: sign(eq.imag()) * |H|/max(|H|)
                    // Phase 143: BPSK fallback uses real axis.
                    float conf = h_mag / max_h_a;
                    float s = d_htsig_bpsk_fallback
                        ? ((eq.real() >= 0.0f) ? 1.0f : -1.0f)
                        : ((eq.imag() >= 0.0f) ? 1.0f : -1.0f);
                    llr48_a[i] = s * conf;
```

- [ ] **Step 4.2: HT-SIG1 LLR sign**

Replace lines 3955–3958:

```cpp
                    // Phase 44 formula: sign(eq.imag()) * |H|/max(|H|)
                    float conf = h_mag / max_h_b;
                    float s = (eq.imag() >= 0.0f) ? 1.0f : -1.0f;
                    llr48_b[i] = s * conf;
```

with:

```cpp
                    // Phase 44 formula: sign(eq.imag()) * |H|/max(|H|)
                    // Phase 143: BPSK fallback uses real axis.
                    float conf = h_mag / max_h_b;
                    float s = d_htsig_bpsk_fallback
                        ? ((eq.real() >= 0.0f) ? 1.0f : -1.0f)
                        : ((eq.imag() >= 0.0f) ? 1.0f : -1.0f);
                    llr48_b[i] = s * conf;
```

- [ ] **Step 4.3: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add lib/frame_equalizer_impl.cc
git commit -m "feat(p143): switch HT-SIG soft-LLR axis for BPSK fallback"
```

---

## Task 5: RX — Switch HT-SIG1 pilot CPE reference axis

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:3857`

- [ ] **Step 5.1: Pilot reference axis for CPE**

Replace lines 3856–3858:

```cpp
                // QBPSK: pilots are on IMAG axis, so normalize to +j (sign of imag)
                gr_complex ref = gr_complex(0.0f, (eq_p.imag() >= 0.0f) ? 1.0f : -1.0f);
                pilot_sum += eq_p / ref;
```

with:

```cpp
                // Phase 143: BPSK fallback pilots are on REAL axis.
                // Standard QBPSK pilots are on IMAG axis.
                gr_complex ref = d_htsig_bpsk_fallback
                    ? gr_complex((eq_p.real() >= 0.0f) ? 1.0f : -1.0f, 0.0f)
                    : gr_complex(0.0f, (eq_p.imag() >= 0.0f) ? 1.0f : -1.0f);
                pilot_sum += eq_p / ref;
```

- [ ] **Step 5.2: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add lib/frame_equalizer_impl.cc
git commit -m "feat(p143): switch HT-SIG pilot CPE reference axis for BPSK fallback"
```

---

## Task 6: RX — Disable QBPSK rotation search in fallback mode

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:8250-8264`
- Modify: `lib/frame_equalizer_impl.cc:8642-8654`

- [ ] **Step 6.1: Skip detect/vote in fallback mode**

Replace lines 8250–8258:

```cpp
                // Detect HT-SIG QBPSK rotation
                int detected_rot = detect_htsig_rotation(d_early_eqsym[kHtSig0Rel]);
                // Energy-based rotation verification
                int energy_rot = vote_qbpsk_rotation(d_early_eqsym[kHtSig0Rel]);

                int start_rot = 0;
                if (energy_rot != detected_rot && energy_rot == 1) {
                    start_rot = energy_rot;
                }
```

with:

```cpp
                // Detect HT-SIG QBPSK rotation (only meaningful for QBPSK).
                // Phase 143: BPSK fallback has no 90° rotation ambiguity;
                // only the 180° sign ambiguity (inv_a/inv_b) remains.
                int detected_rot = 0;
                int energy_rot = 0;
                if (!d_htsig_bpsk_fallback) {
                    detected_rot = detect_htsig_rotation(d_early_eqsym[kHtSig0Rel]);
                    energy_rot = vote_qbpsk_rotation(d_early_eqsym[kHtSig0Rel]);
                }

                int start_rot = 0;
                if (energy_rot != detected_rot && energy_rot == 1) {
                    start_rot = energy_rot;
                }
```

- [ ] **Step 6.2: Restrict rotation candidate count in fallback mode**

Replace lines 8642–8646:

```cpp
                const bool htsig_fine_rot_env =
                    getenv("IEEE80211_HTSIG_FINE_ROT") &&
                    getenv("IEEE80211_HTSIG_FINE_ROT")[0] != '\0';
                const int htsig_n_rot = htsig_fine_rot_env ? 8 : 4;
                const int htsig_step_div = htsig_fine_rot_env ? 4 : 2;
```

with:

```cpp
                const bool htsig_fine_rot_env =
                    getenv("IEEE80211_HTSIG_FINE_ROT") &&
                    getenv("IEEE80211_HTSIG_FINE_ROT")[0] != '\0';
                // Phase 143: BPSK fallback has no QBPSK rotation search;
                // rot=0 is identity, so only inv_a/inv_b are tried.
                const int htsig_n_rot = d_htsig_bpsk_fallback ? 1
                                       : (htsig_fine_rot_env ? 8 : 4);
                const int htsig_step_div = htsig_fine_rot_env ? 4 : 2;
```

- [ ] **Step 6.3: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add lib/frame_equalizer_impl.cc
git commit -m "feat(p143): disable QBPSK rotation search in BPSK fallback mode"
```

---

## Task 7: Test Harness — Add CLI flag

**Files:**
- Modify: `test_usrp_minimal_loopback.py:125-135`
- Modify: `test_usrp_minimal_loopback.py:467-475`

- [ ] **Step 7.1: Inject env var when flag is set**

Add after line 135 (after the Wiener log block):

```python
    # Phase 143: BPSK-HT-SIG fallback (non-standard, TX/RX coordinated).
    if args.htsig_bpsk_fallback:
        os.environ['IEEE80211_HTSIG_BPSK_FALLBACK'] = '1'
        print("[TEST] Phase 143 BPSK-HT-SIG fallback ENABLED", flush=True)
```

- [ ] **Step 7.2: Add the argparse flag**

Add before line 475 (before `parser.add_argument('--internal-run', ...)`):

```python
    # Phase 143: BPSK-HT-SIG fallback (opt-in, non-standard).
    # Replaces QBPSK HT-SIG0/HT-SIG1 with BPSK to double angular margin
    # against the USRP 1.77 rad per-SC phase-noise floor.
    parser.add_argument('--htsig-bpsk-fallback', action='store_true',
                        help='Phase 143: use BPSK instead of QBPSK for '
                             'HT-SIG0/HT-SIG1 (IEEE80211_HTSIG_BPSK_FALLBACK=1, '
                             'opt-in, non-standard)')
```

- [ ] **Step 7.3: Verify the test script parses**

Run:

```bash
cd /home/hy/gr-ieee802-11
python -m py_compile test_usrp_minimal_loopback.py
```

Expected: no output.

- [ ] **Step 7.4: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add test_usrp_minimal_loopback.py
git commit -m "feat(p143): add --htsig-bpsk-fallback test flag"
```

---

## Task 8: Build and Install

- [ ] **Step 8.1: Compile**

```bash
cd /home/hy/gr-ieee802-11/build
make -j$(nproc)
```

Expected: build completes with no errors.

- [ ] **Step 8.2: Install**

```bash
cd /home/hy/gr-ieee802-11/build
make install
```

Expected: install completes (required so Python loads fresh `.so`).

- [ ] **Step 8.3: Commit build meta (optional)**

No source changes to commit here. If `CLAUDE.md` needs a conventions line, add it in a separate commit; otherwise skip.

---

## Task 9: T1 — Software Loopback Validation

- [ ] **Step 9.1: Run loopback with fallback enabled**

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py --htsig-bpsk-fallback --duration 5
```

Expected output contains:

```
[TEST] Phase 143 BPSK-HT-SIG fallback ENABLED
[FRAME_EQ] IEEE80211_HTSIG_BPSK_FALLBACK=1 (HT-SIG decoded as BPSK)
*** FCS OK ***
[TEST] FCS_OK=N FCS_FAIL=M
```

where `N >= 1`.

- [ ] **Step 9.2: Run loopback with fallback disabled (regression)**

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py --duration 5
```

Expected: same baseline behavior as before this plan (FCS_OK > 0, no BPSK fallback log).

- [ ] **Step 9.3: If T1 fails, roll back and diagnose**

Rollback command:

```bash
cd /home/hy/gr-ieee802-11
git log --oneline -5
# Identify the p143 commits and revert them in reverse order:
git revert <commit-hash> --no-edit
```

Common failure modes:
- `FCS_OK=0` with fallback ON → check that both TX and RX saw `IEEE80211_HTSIG_BPSK_FALLBACK=1` in their logs.
- `HT_SIG_CAND` events but CRC fail → bit-extraction axis is still wrong; re-check Task 3.

- [ ] **Step 9.4: Commit verdict note on T1**

Create `docs/superpowers/notes/2026-07-12-phase143-t1-loopback-verdict.md` with the loopback result and commit.

---

## Task 10: T3 — USRP Realtime Validation

- [ ] **Step 10.1: Run the recommended realtime command**

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py \
  --freq 5250 --tx-gain 0 --rx-gain 31.5 --rate 20 \
  --warmup 60 --duration 30 --rx-subdev A:0 \
  --phase139-on --wiener-on --htsig-bpsk-fallback
```

Expected output contains:

```
[TEST] Phase 139 ENABLED: IEEE80211_H52_2WAY_DEFAULT=1 ...
[TEST] Phase 141 Wiener ENABLED: IEEE80211_WIENER_H52=1 ...
[TEST] Phase 143 BPSK-HT-SIG fallback ENABLED
[FRAME_EQ] IEEE80211_HTSIG_BPSK_FALLBACK=1 (HT-SIG decoded as BPSK)
*** FCS OK ***
[TEST] FCS_OK=N FCS_FAIL=M
```

where `N >= 1`.

- [ ] **Step 10.2: Baseline comparison (same config, no fallback)**

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py \
  --freq 5250 --tx-gain 0 --rx-gain 31.5 --rate 20 \
  --warmup 60 --duration 30 --rx-subdev A:0 \
  --phase139-on --wiener-on
```

Expected: `FCS_OK=0`, HT_SIG_CAND > 0, best_metric > 10.

- [ ] **Step 10.3: Parameter sweep**

Repeat Step 10.1 with:

| tx-gain | rx-gain | --uhd-tune | expected primary result |
|---------|---------|------------|-------------------------|
| 0       | 20      | off        | FCS_OK count |
| 0       | 31.5    | off        | FCS_OK count |
| 10      | 31.5    | off        | FCS_OK count |
| 20      | 31.5    | off        | FCS_OK count |
| 0       | 31.5    | on         | FCS_OK count |

Record `Sent`, `Recv`, `FCS_OK`, `HT_SIG_CAND`, `best_metric`, `avg_snr_htsig` for each.

- [ ] **Step 10.4: Write final verdict note**

Create `docs/superpowers/notes/2026-07-12-phase143-usrp-verdict.md` with:
- Config matrix and results
- Whether FCS_OK >= 1 was achieved
- Best sub-configuration
- Next steps (e.g., adaptive switching, standard-frame interleaving)

Commit the verdict.

---

## Task 11: Documentation Update

- [ ] **Step 11.1: Update CLAUDE.md conventions**

Add a new bullet under the "IEEE80211_HTSIG_EQ_DIAG=1" section (or create a new Phase 143 section):

```markdown
- **IEEE80211_HTSIG_BPSK_FALLBACK=1** — Phase 143 BPSK-HT-SIG fallback
  (opt-in, default OFF). TX/RX coordinated non-standard mode that sends
  HT-SIG0/HT-SIG1 as BPSK instead of QBPSK, doubling angular margin against
  the USRP 1.77 rad per-SC phase-noise floor. Use with
  `--htsig-bpsk-fallback` in test scripts. Standard 802.11n devices will
  silently drop these frames.
```

- [ ] **Step 11.2: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add CLAUDE.md
git commit -m "docs(p143): document IEEE80211_HTSIG_BPSK_FALLBACK convention"
```

---

## Plan Self-Review

### Spec coverage

| Spec Section | Plan Task |
|--------------|-----------|
| TX: conditional ×j rotation | Task 1 |
| RX: `d_htsig_bpsk_fallback` member + env parse | Task 2 |
| RX: bit-extraction axis switch | Task 3 |
| RX: LLR axis switch | Task 4 |
| RX: pilot CPE reference axis switch | Task 5 |
| RX: disable QBPSK rotation search | Task 6 |
| Test harness `--htsig-bpsk-fallback` | Task 7 |
| T1 loopback validation | Task 9 |
| T3 USRP realtime validation | Task 10 |
| Documentation | Task 11 |

### Placeholder scan

No TBD/TODO/"implement later"/"similar to" placeholders. Every step contains exact file paths, line numbers, code, and commands.

### Type consistency

- `d_htsig_bpsk_fallback` declared as `bool` in Task 2 and used as `bool` in Tasks 3–6.
- Env var name `IEEE80211_HTSIG_BPSK_FALLBACK` is identical in TX, RX, and test harness.
- CLI flag `--htsig-bpsk-fallback` maps to the same env var.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-12-bpsk-htsig-fallback-plan.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
