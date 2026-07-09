# Phase 137 — Stable-Null-Aware Masking with Alternative CPE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attack Phase 78b's 5 stable null SCs {-21,-13,-7,+7,+21} by extending the existing `IEEE80211_HTSIG_NULL_SCS` env var to mask the 4 null pilots, plus opt-in CPE fallback when all pilots are masked. Goal is HT-SIG viterbi metric ≤ 10 on at least 1 USRP run.

**Architecture:** Three-layer opt-in fix. Layer 1 (env parser) is backward-compatible — accepts old loop-position format ("12") and new signed SC values ("-21,-13,-7,7,21"). Layer 2 (pilot CPE mask) is opt-in via `IEEE80211_HTSIG_NULL_PILOT_MASK=1`. Layer 3 (data-SC CPE fallback) activates automatically when all 4 pilots are masked/invalid. All default OFF to preserve baseline.

**Tech Stack:** GNU Radio 3.10 + UHD 4.7.0.HEAD + gr-uhd 4.9.0.0 + USRP X310 + UBX-160 v2 + Python 3.10 + C++14

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `lib/frame_equalizer_impl.cc` | Modify | 3 edits: (1) env parser line 4816-4840, (2) CPE pilot skip line 3626-3646, (3) new gate flag + data-SC fallback |
| `lib/frame_equalizer_impl.h` | Modify | Add `d_apply_htsig_null_pilot_mask` member flag (1 bool) |
| `examples/test_file_replay_e2e.py` | Modify | Add `--phase137-on` arg that sets both env vars |
| `test_usrp_minimal_loopback.py` | Modify | Add `--phase137-on` arg that sets both env vars |
| `docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md` | Create | Final verdict (after T4-T5 USRP) |

---

## Task 1: Add header flag `d_apply_htsig_null_pilot_mask`

**Files:**
- Modify: `lib/frame_equalizer_impl.h` (find existing opt-in flag declarations, add new one)

- [ ] **Step 1: Locate existing opt-in flag declarations**

Read `lib/frame_equalizer_impl.h` and find a section with other `d_apply_*` flags. Search for `d_apply_htsig_per_symbol_delta` or similar — the new flag belongs near these.

- [ ] **Step 2: Add the new flag declaration**

Add this member declaration near the other `d_apply_*` opt-in flags:

```cpp
// Phase 137: opt-in flag to skip null pilots in CPE estimator.
bool d_apply_htsig_null_pilot_mask;
```

- [ ] **Step 3: Verify it compiles**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc) 2>&1 | tail -5
```

Expected: no errors related to the new member (declaration-only — initialization is in Task 2).

- [ ] **Step 4: Commit**

```bash
git add lib/frame_equalizer_impl.h
git commit -m "feat(p137): declare d_apply_htsig_null_pilot_mask flag"
```

---

## Task 2: Extend `IEEE80211_HTSIG_NULL_SCS` env parser to accept signed SC values

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:4816-4840` (existing env parser block)

- [ ] **Step 1: Read the existing parser**

Read lines 4816-4840 of `lib/frame_equalizer_impl.cc`. The existing block looks like:

```cpp
const char* env_ns = std::getenv("IEEE80211_HTSIG_NULL_SCS");
if (env_ns && env_ns[0] != '\0') {
    int n_parsed = 0;
    const char* p = env_ns;
    while (*p && n_parsed < 52) {
        int idx = 0;
        bool got_digit = false;
        while (*p >= '0' && *p <= '9') {
            idx = idx * 10 + (*p - '0');
            p++;
            got_digit = true;
        }
        if (got_digit && idx >= 0 && idx < 52) {
            d_htsig_null_sc_mask[idx] = 1;
            n_parsed++;
        }
        if (*p == ',' || *p == ' ') p++;
        else if (*p != '\0') break;
    }
    if (n_parsed > 0) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_NULL_SCS='"
                  << env_ns << "' (masked " << n_parsed << " SCs)\n";
    }
}
```

- [ ] **Step 2: Replace with extended parser**

Replace the entire `env_ns` block (lines 4816-4840) with this extended version that accepts both old loop-position format (0..51) and new signed SC value format (-26..+26):

```cpp
const char* env_ns = std::getenv("IEEE80211_HTSIG_NULL_SCS");
if (env_ns && env_ns[0] != '\0') {
    int n_parsed = 0;
    const char* p = env_ns;
    while (*p && n_parsed < 52) {
        int val = 0;
        bool got_digit = false;
        bool negative = false;
        // Phase 137: optional leading '-' for signed SC values
        if (*p == '-') {
            negative = true;
            p++;
        }
        while (*p >= '0' && *p <= '9') {
            val = val * 10 + (*p - '0');
            p++;
            got_digit = true;
        }
        if (negative) val = -val;
        if (got_digit) {
            int loop_pos = -1;
            // Phase 137: accept either loop position (0..51) or signed SC value (-26..+26)
            if (val >= 0 && val < 52) {
                loop_pos = val;  // old format (direct loop position)
            } else if (val >= -26 && val <= 26 && val != 0) {
                // New format: search kScIndex52 for matching SC value
                // kScIndex52 is declared at line 307 as a 52-element array
                for (int i = 0; i < 52; i++) {
                    if (kScIndex52[i] == val) {
                        loop_pos = i;
                        break;
                    }
                }
                if (loop_pos < 0) {
                    std::cout << "[FRAME_EQ] IEEE80211_HTSIG_NULL_SCS WARNING: "
                              << "SC value " << val << " not in kScIndex52\n";
                }
            }
            if (loop_pos >= 0 && loop_pos < 52) {
                d_htsig_null_sc_mask[loop_pos] = 1;
                n_parsed++;
            }
        }
        if (*p == ',' || *p == ' ') p++;
        else if (*p != '\0') break;
    }
    if (n_parsed > 0) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_NULL_SCS='"
                  << env_ns << "' (masked " << n_parsed << " SCs)\n";
    }
}
```

- [ ] **Step 3: Verify build**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc) 2>&1 | tail -5
```

Expected: build succeeds with no errors. The `d_apply_htsig_null_pilot_mask` member is declared but not yet initialized — that's fine (default-initialized to false), it'll be wired up in Task 3.

- [ ] **Step 4: Install**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --install . --prefix /home/hy/gr-ieee802-11/install 2>&1 | tail -3
cp -r /home/hy/gr-ieee802-11/install/lib/python3.10/site-packages/* /home/hy/conda/envs/gnuradio/lib/python3.10/site-packages/ 2>/dev/null || true
```

(Per CLAUDE.md: `make install` must run after every `make`; otherwise Python loads stale .so.)

- [ ] **Step 5: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "feat(p137): IEEE80211_HTSIG_NULL_SCS accepts signed SC values (-26..+26)"
```

---

## Task 3: Wire up `IEEE80211_HTSIG_NULL_PILOT_MASK=1` opt-in flag

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (find existing `IEEE80211_HT_PER_SYMBOL_CPE` env parser and add new flag init right after)

- [ ] **Step 1: Locate the existing `IEEE80211_HT_PER_SYMBOL_CPE` env parser block**

Search for `IEEE80211_HT_PER_SYMBOL_CPE` in `lib/frame_equalizer_impl.cc`. The env parser block looks like:

```cpp
const char* env_cpe = std::getenv("IEEE80211_HT_PER_SYMBOL_CPE");
if (env_cpe && env_cpe[0] == '1') {
    d_apply_ht_per_symbol_cpe = true;
    // ...
}
```

The exact location varies; look for a `[FRAME_EQ] IEEE80211_HT_PER_SYMBOL_CPE=1` log line.

- [ ] **Step 2: Add Phase 137 env parser immediately after**

Insert this block immediately after the `IEEE80211_HT_PER_SYMBOL_CPE` block:

```cpp
// Phase 137: opt-in flag to skip null pilots in CPE estimator.
// Requires IEEE80211_HT_PER_SYMBOL_CPE=1 to have any effect (CPE code path).
// When ON, the pilot CPE loop at line 3626 skips null pilots (positions
// 48..51) that are masked via IEEE80211_HTSIG_NULL_SCS.
const char* env_p137 = std::getenv("IEEE80211_HTSIG_NULL_PILOT_MASK");
if (env_p137 && env_p137[0] == '1') {
    d_apply_htsig_null_pilot_mask = true;
    std::cout << "[FRAME_EQ] IEEE80211_HTSIG_NULL_PILOT_MASK=1 "
              << "(Phase 137: skip null pilots in CPE)\n";
}
```

- [ ] **Step 3: Verify build**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc) 2>&1 | tail -5
```

Expected: build succeeds. The new flag is initialized to true when env is set, but the CPE pilot loop (Task 4) hasn't been modified yet, so the flag has no behavioral effect yet — that's intentional (this task is wiring only).

- [ ] **Step 4: Install**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --install . --prefix /home/hy/gr-ieee802-11/install 2>&1 | tail -3
cp -r /home/hy/gr-ieee802-11/install/lib/python3.10/site-packages/* /home/hy/conda/envs/gnuradio/lib/python3.10/site-packages/ 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "feat(p137): wire up IEEE80211_HTSIG_NULL_PILOT_MASK=1 opt-in flag"
```

---

## Task 4: Modify pilot CPE loop to skip null pilots + add data-SC fallback

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:3626-3646` (existing `IEEE80211_HT_PER_SYMBOL_CPE` block)

- [ ] **Step 1: Read the existing CPE block**

Read lines 3624-3646. The block starts with:

```cpp
gr_complex cpe_rot_b(1.0f, 0.0f);  // identity by default
if (getenv("IEEE80211_HT_PER_SYMBOL_CPE")) {
    const int pilot_sc[4] = {48, 49, 50, 51};  // -21, -7, +7, +21
    gr_complex pilot_sum(0.0f, 0.0f);
    int n_pilots = 0;
    for (int p = 0; p < 4; p++) {
        int sc = pilot_sc[p];
        float h_mag = std::abs(H52_a[sc]);
        if (h_mag >= 0.001f) {
            gr_complex eq_p = safe_div(rx52_a[sc], H52_a[sc]);
            // QBPSK: pilots are on IMAG axis, so normalize to +j (sign of imag)
            gr_complex ref = gr_complex(0.0f, (eq_p.imag() >= 0.0f) ? 1.0f : -1.0f);
            pilot_sum += eq_p / ref;
            n_pilots++;
        }
    }
    if (n_pilots > 0) {
        // Average residual phase of HT-SIG0 pilots (relative to +j axis)
        float cpe_phase_htsig0 = std::arg(pilot_sum / float(n_pilots));
        // Apply OPPOSITE rotation to HT-SIG1 to compensate
        cpe_rot_b = std::polar(1.0f, -cpe_phase_htsig0);
    }
}
```

- [ ] **Step 2: Replace with Phase 137 version (skip null pilots + data-SC fallback)**

Replace the entire `if (getenv("IEEE80211_HT_PER_SYMBOL_CPE"))` block with:

```cpp
if (getenv("IEEE80211_HT_PER_SYMBOL_CPE")) {
    const int pilot_sc[4] = {48, 49, 50, 51};  // -21, -7, +7, +21
    gr_complex pilot_sum(0.0f, 0.0f);
    int n_pilots = 0;
    for (int p = 0; p < 4; p++) {
        int sc = pilot_sc[p];
        // Phase 137: skip null pilots when opt-in flag is set AND mask is on.
        // Phase 78b stable nulls {-21,-7,+7,+21} are exactly these 4 pilots.
        if (d_apply_htsig_null_pilot_mask &&
            htsig_null_sc_mask && htsig_null_sc_mask[sc]) {
            continue;
        }
        float h_mag = std::abs(H52_a[sc]);
        if (h_mag >= 0.001f) {
            gr_complex eq_p = safe_div(rx52_a[sc], H52_a[sc]);
            // QBPSK: pilots are on IMAG axis, so normalize to +j (sign of imag)
            gr_complex ref = gr_complex(0.0f, (eq_p.imag() >= 0.0f) ? 1.0f : -1.0f);
            pilot_sum += eq_p / ref;
            n_pilots++;
        }
    }
    // Phase 137: if all 4 pilots masked/invalid, fallback to data-SC CPE.
    // Use top data SCs (skipping null + low-|H|) to estimate the residual phase.
    if (n_pilots == 0 && d_apply_htsig_null_pilot_mask && htsig_null_sc_mask) {
        gr_complex data_sum(0.0f, 0.0f);
        int n_data = 0;
        for (int i = 0; i < 48; i++) {
            if (htsig_null_sc_mask[i]) continue;  // skip null data SCs
            float h_mag = std::abs(H52_a[i]);
            if (h_mag >= 0.1f) {  // stricter threshold for data SCs
                gr_complex eq_d = safe_div(rx52_a[i], H52_a[i]);
                // QBPSK data is on imag axis (same convention as pilots)
                gr_complex ref = gr_complex(0.0f,
                                            (eq_d.imag() >= 0.0f) ? 1.0f : -1.0f);
                data_sum += eq_d / ref;
                n_data++;
            }
        }
        if (n_data > 0) {
            pilot_sum = data_sum;
            n_pilots = n_data;
            std::cout << "[FRAME_EQ] Phase 137 data-SC CPE fallback: "
                      << "n_pilots=" << n_data << "\n";
        }
    }
    if (n_pilots > 0) {
        // Average residual phase of HT-SIG0 pilots (relative to +j axis)
        float cpe_phase_htsig0 = std::arg(pilot_sum / float(n_pilots));
        // Apply OPPOSITE rotation to HT-SIG1 to compensate
        cpe_rot_b = std::polar(1.0f, -cpe_phase_htsig0);
    }
}
```

- [ ] **Step 3: Verify build**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc) 2>&1 | tail -5
```

Expected: build succeeds with no errors.

- [ ] **Step 4: Install**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --install . --prefix /home/hy/gr-ieee802-11/install 2>&1 | tail -3
cp -r /home/hy/gr-ieee802-11/install/lib/python3.10/site-packages/* /home/hy/conda/envs/gnuradio/lib/python3.10/site-packages/ 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "feat(p137): skip null pilots in CPE + data-SC fallback when all masked"
```

---

## Task 5: Add `--phase137-on` arg to test_file_replay_e2e.py

**Files:**
- Modify: `examples/test_file_replay_e2e.py` (find existing `--diag` arg, add `--phase137-on` arg)

- [ ] **Step 1: Locate existing argparse setup**

Search for `add_argument` in `examples/test_file_replay_e2e.py`. The existing `--diag` arg looks like:

```python
parser.add_argument('--diag', type=str, default=None, help='...')
```

- [ ] **Step 2: Add `--phase137-on` arg**

Add this arg immediately after the `--diag` line:

```python
parser.add_argument('--phase137-on', action='store_true',
                    help='Phase 137: enable stable-null-aware masking '
                         '(IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 + '
                         'IEEE80211_HTSIG_NULL_PILOT_MASK=1)')
```

- [ ] **Step 3: Add env injection**

Find the section near the top that sets `DEFAULT_ENV` (line 30-37 in current file) and add a Phase 137 block AFTER it:

```python
# Phase 137: stable-null-aware masking (opt-in via --phase137-on).
# Default OFF preserves baseline.
if args.phase137_on:
    os.environ['IEEE80211_HTSIG_NULL_SCS'] = '-21,-13,-7,7,21'
    os.environ['IEEE80211_HTSIG_NULL_PILOT_MASK'] = '1'
    os.environ['IEEE80211_HT_PER_SYMBOL_CPE'] = '1'  # required for pilot CPE code path
    print(f"[TEST] Phase 137 ENABLED: "
          "IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 "
          "IEEE80211_HTSIG_NULL_PILOT_MASK=1", flush=True)
```

- [ ] **Step 4: Smoke-test file-replay (T1 baseline regression)**

```bash
cd /home/hy/gr-ieee802-11
python examples/test_file_replay_e2e.py --loop 5 2>&1 | tail -10
```

Expected: `FCS_OK=1/1` (loopback preserved, no env vars set → baseline behavior).

- [ ] **Step 5: Run T2 (file-replay with Phase 137 ON)**

```bash
cd /home/hy/gr-ieee802-11
python examples/test_file_replay_e2e.py --loop 5 --phase137-on 2>&1 | tee /tmp/p137_t2_filereplay.log | tail -10
grep -E "masked 5 SCs|Phase 137 data-SC CPE fallback" /tmp/p137_t2_filereplay.log
```

Expected:
- `FCS_OK=1/1` (file-replay is CLEAN → Phase 137 should not regress)
- Log shows `IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21' (masked 5 SCs)`
- Log shows `IEEE80211_HTSIG_NULL_PILOT_MASK=1 (Phase 137: skip null pilots in CPE)`

If regression: STOP. Investigate why Phase 137 breaks clean file-replay (likely the data-SC fallback has a bug — set `IEEE80211_HTSIG_NULL_PILOT_MASK_DEBUG=1` or print intermediate values).

- [ ] **Step 6: Commit**

```bash
git add examples/test_file_replay_e2e.py
git commit -m "feat(p137): add --phase137-on arg to test_file_replay_e2e.py"
```

---

## Task 6: Add `--phase137-on` arg to test_usrp_minimal_loopback.py

**Files:**
- Modify: `test_usrp_minimal_loopback.py` (find existing `--uhd-tune` arg block, add `--phase137-on` after)

- [ ] **Step 1: Locate existing `--uhd-tune` arg**

Search for `--uhd-tune` in `test_usrp_minimal_loopback.py`. The arg definition is at the top of the file (argparse section).

- [ ] **Step 2: Add `--phase137-on` arg**

Add this arg immediately after the `--uhd-tune` (or similar existing opt-in) arg:

```python
parser.add_argument('--phase137-on', action='store_true',
                    help='Phase 137: enable stable-null-aware masking '
                         '(IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 + '
                         'IEEE80211_HTSIG_NULL_PILOT_MASK=1)')
```

- [ ] **Step 3: Add env injection in `internal_run()`**

Find the existing `if args.uhd_tune:` block in `internal_run()` (around line 61-73) and add this Phase 137 block immediately after it:

```python
    # Phase 137: stable-null-aware masking (opt-in via --phase137-on).
    # Default OFF preserves baseline.
    if args.phase137_on:
        os.environ['IEEE80211_HTSIG_NULL_SCS'] = '-21,-13,-7,7,21'
        os.environ['IEEE80211_HTSIG_NULL_PILOT_MASK'] = '1'
        os.environ['IEEE80211_HT_PER_SYMBOL_CPE'] = '1'  # required for pilot CPE code path
        print(f"[TEST] Phase 137 ENABLED: "
              "IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 "
              "IEEE80211_HTSIG_NULL_PILOT_MASK=1", flush=True)
```

- [ ] **Step 4: Commit**

```bash
git add test_usrp_minimal_loopback.py
git commit -m "feat(p137): add --phase137-on arg to test_usrp_minimal_loopback.py"
```

---

## Task 7: USRP T4 — 5250 MHz cable single-run validation

**Files:**
- Modify: `/tmp/p137_t4_usrp.log` (test log only — no code changes)

- [ ] **Step 1: Run USRP 5250 cable with Phase 137**

Standard config (per CLAUDE.md Phase 82+):
- Same-board A:0 TX → A:0 RX2 (no cross-board)
- `--freq 5250`
- `--tx-gain 0` (HW risk warning: bare cable, ≤5 cable runs total)
- `--rate 20`
- `--warmup 60`

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20 \
    --warmup 60 --rx-subdev A:0 --phase137-on \
    --duration 30 2>&1 | tee /tmp/p137_t4_usrp.log | tail -15
```

- [ ] **Step 2: Check log for Phase 137 markers**

```bash
grep -E "IEEE80211_HTSIG_NULL_SCS|IEEE80211_HTSIG_NULL_PILOT_MASK|Phase 137 data-SC CPE fallback|HT_SIG_CAND|LTF_SCORE|HTSIG_CRC" /tmp/p137_t4_usrp.log | head -30
```

Expected:
- `IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21' (masked 5 SCs)` present
- `IEEE80211_HTSIG_NULL_PILOT_MASK=1 (Phase 137: skip null pilots in CPE)` present
- Optionally `Phase 137 data-SC CPE fallback: n_pilots=N` if all 4 pilots were masked

- [ ] **Step 3: Analyze metric distribution**

```bash
grep -E "metric=|HT_SIG_CAND|HTSIG_CRC|LSIG_DECODE" /tmp/p137_t4_usrp.log | head -20
```

Compare metric values vs baseline. Per Phase 100 verdict, baseline USRP produces metric 13-15. Target: metric ≤ 10 in at least 1 occurrence.

- [ ] **Step 4: Decision gate**

If `metric ≤ 10` observed: proceed to Task 8 (multi-run validation).
If metric unchanged (still 13-15): STOP. Document finding in verdict (Task 9). Phase 137 root cause hypothesis is WRONG → return to Phase 100 alternatives.

- [ ] **Step 5: Save log to disk (no commit — logs go in /tmp)**

```bash
ls -la /tmp/p137_t4_usrp.log
```

(No commit needed for logs.)

---

## Task 8: USRP T5 — Multi-run validation (3-5 runs)

**Files:**
- Modify: `/tmp/p137_t5_run{1,2,3,4,5}_usrp.log` (test logs)

- [ ] **Step 1: Run #1**

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20 \
    --warmup 60 --rx-subdev A:0 --phase137-on \
    --duration 30 2>&1 | tee /tmp/p137_t5_run1_usrp.log | tail -5
```

- [ ] **Step 2: Run #2 (same config, different seed)**

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20 \
    --warmup 60 --rx-subdev A:0 --phase137-on \
    --duration 30 2>&1 | tee /tmp/p137_t5_run2_usrp.log | tail -5
```

- [ ] **Step 3: Run #3 (same config)**

```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20 \
    --warmup 60 --rx-subdev A:0 --phase137-on \
    --duration 30 2>&1 | tee /tmp/p137_t5_run3_usrp.log | tail -5
```

(Optionally: 4-5 runs if cable budget allows. Per CLAUDE.md Phase 82+: ≤5 total cable runs until 30 dB attenuator arrives. Phase 137 T1-T3 used file-replay (no cable runs); T4 used 1 cable run; T5 uses 3-5 more. Total ≤6 cable runs. If user has 30 dB attenuator by T5, can extend to 5 runs.)

- [ ] **Step 4: Aggregate metric distribution across runs**

```bash
for i in 1 2 3; do
    echo "=== Run $i ==="
    grep -oE "metric=[0-9]+" /tmp/p137_t5_run${i}_usrp.log | sort | uniq -c | sort -rn
done
```

Expected: at least one run with at least one `metric ≤ 10` occurrence.

- [ ] **Step 5: Decision gate**

If 1+ run has metric ≤ 10: SUCCESS. Proceed to Task 9 (verdict).
If no run shows metric improvement: REFUTED. Document in verdict, propose alternative attack direction.

---

## Task 9: Write verdict + update CLAUDE.md + MEMORY

**Files:**
- Create: `docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md`
- Modify: `CLAUDE.md` (add Phase 137 section)
- Modify: `~/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md` (add Phase 137 entry)

- [ ] **Step 1: Write verdict file**

Create `docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md` with this template (fill in actual numbers from T4-T5):

```markdown
# Phase 137: Stable-Null-Aware Masking with Alternative CPE (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: [✅ SUCCESS / 🔴 REFUTED / 🟡 PARTIAL]

## TL;DR

[2-3 sentence summary of outcome]

## T1-T3 File-Replay Results

| Test | Config | Result |
|------|--------|--------|
| T1 | Baseline (no env) | 1/1 FCS_OK |
| T2 | Phase 137 full mask | 1/1 FCS_OK |
| T3 | Partial pilot mask | 1/1 FCS_OK |

## T4-T5 USRP Results (5250 MHz cable)

| Run | Config | HT_SIG_CAND | Metric Distribution | Verdict |
|-----|--------|-------------|---------------------|---------|
| T4 | Phase 137 | [N] | [histogram] | [decision] |
| T5 #1 | Phase 137 | [N] | [histogram] | [decision] |
| T5 #2 | Phase 137 | [N] | [histogram] | [decision] |
| T5 #3 | Phase 137 | [N] | [histogram] | [decision] |

## Conclusion

[1 paragraph: did Phase 137 confirm/refute the root cause? what does this mean for next phase?]

## What's Next

[1-2 paragraphs: per HARD CONSTRAINT, what's the next attack direction?]
```

- [ ] **Step 2: Update CLAUDE.md**

Find the most recent Phase 136 entry in CLAUDE.md (search for "Phase 136") and add a Phase 137 entry after it. Use this template:

```markdown
- **Phase 137 stable-null-aware masking (NEW 2026-07-09)** — 3-layer opt-in
  fix targeting Phase 78b's 5 stable null SCs {-21,-13,-7,+7,+21}:
  - `IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21'` — extended format accepts
    signed SC values (backward-compat with old loop-position format "12")
  - `IEEE80211_HTSIG_NULL_PILOT_MASK=1` — opt-in: skip null pilots in CPE
  - Data-SC CPE fallback when all 4 pilots masked (auto, no env)
  Default OFF. Verdict: `docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md`.
```

- [ ] **Step 3: Add MEMORY.md entry**

Append to `~/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md`:

```markdown
- [Phase 137 Stable-Null Mask 2026-07-09](project_p137_stable_null_mask.md) — **2026-07-09** — [outcome summary from T4-T5]. 3-layer opt-in attack on Phase 78b 5 stable null SCs. Verdict: `docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md`.
```

- [ ] **Step 4: Commit verdict + docs**

```bash
git add docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md CLAUDE.md
git commit -m "docs(p137): T4-T5 USRP 5250 validation + final verdict"
```

(Note: MEMORY.md is not in git; it's in `~/.claude/projects/`. No commit for that.)

---

## Self-Review

**1. Spec coverage:**
- ✅ L1 parser (Task 2)
- ✅ L2 CPE mask (Task 3 + Task 4)
- ✅ L3 data-SC fallback (Task 4)
- ✅ T1 file-replay baseline regression (Task 5 step 4)
- ✅ T2 file-replay full mask (Task 5 step 5)
- ✅ T4 USRP single-run (Task 7)
- ✅ T5 USRP multi-run (Task 8)
- ✅ Verdict + CLAUDE.md + MEMORY updates (Task 9)
- ✅ Failure modes documented (Task 7 step 4 + Task 8 step 5 decision gates)

**2. Placeholder scan:** No TBD/TODO. Every step has concrete code/commands.

**3. Type consistency:** `d_apply_htsig_null_pilot_mask` declared in Task 1, initialized in Task 3, read in Task 4. Env var names consistent across tasks (`IEEE80211_HTSIG_NULL_PILOT_MASK`).

**4. Scope check:** Plan focuses on Phase 137 attack only. Does not modify L-SIG, viterbi, soft LLR, cross-frame H tracking — out of scope per spec section 6.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-phase137-stable-null-mask.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review

Which approach?