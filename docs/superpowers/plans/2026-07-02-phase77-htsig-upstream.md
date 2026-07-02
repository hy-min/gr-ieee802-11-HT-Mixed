# Phase 77 HT-SIG Viterbi Upstream Attack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 3-4 dB SNR gap on HT-SIG viterbi to achieve convergence at 5250 MHz (the only known quiet 5 GHz band).

**Architecture:** Phase 76 proved HT-SIG chain fires on USRP at 5250 MHz with tight_v2 (576 candidates). The wall is HT-SIG viterbi channel-physics. Phase 77 attacks upstream of viterbi: per-symbol L-SIG CPE (77a), HT-SIG LLR soft viterbi (77b), per-frame H52 refinement (77c).

**Tech Stack:** GNU Radio 3.10, C++ frame_equalizer_impl.cc, USRP X310 + UBX-160, 5250 MHz (quiet band per Phase 76 T3).

---

## Context

**Phase 76 PARTIAL**:
- 5250 MHz = quietest 5 GHz band (0 LSIG in 30s no-TX)
- tight_v2 (THRESH=0.03, RADIUS=5, PILOT_CPE=1) achieves n_nulls=0/52
- HT-SIG chain fires: 576 HT_SIG_CAND at 5250 with self-TX
- HT-SIG viterbi fails: avg_snr_htsig 2-3 dB (need ≥6 dB), std_im 0.77-1.88 (need ≤0.3)
- 36 HT_SIG_PARSE_FAIL all at metric 12-18 (~50% BER; need ≤4)

**Wall**: HT-SIG viterbi convergence on USRP requires ≥6 dB avg_snr_htsig. Current 2-3 dB is 3-4 dB short.

**Phase 77 attacks**:
- 77a: Per-symbol L-SIG CPE (different from REFUTED Phase 19/20 HT-SIG CPE)
- 77b: HT-SIG LLR-based soft viterbi (re-test Phase 44 on clean 5250 channel)
- 77c: Per-frame H52 refinement (incremental on tight_v2)
- 77d: Accept HT-SIG closure (per Phase 41) if 77a-77c fail

---

## File Structure

- `lib/frame_equalizer_impl.cc` — main changes for 77a, 77b, 77c
- `lib/decoder_impl.cc` — viterbi implementation (77b)
- `docs/superpowers/notes/2026-07-03-phase77-*.md` — per-task findings

---

## Task 1: 77a — Per-symbol L-SIG CPE

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (add L-SIG per-symbol CPE, like Phase 19/20 HT-SIG CPE pattern)

**Goal:** Apply per-symbol CPE on L-SIG using L-SIG's 4 pilot SCs (indices -21, -7, +7, +21). Each L-SIG symbol's phase error is estimated and corrected before HT-SIG demodulation.

- [ ] **Step 1: Read Phase 19/20 HT-SIG CPE implementation**

```bash
grep -n "HTSIG_PILOT_CPE\|pilot_cpe\|pilot_diff\|per_symbol_cpe" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -20
```

- [ ] **Step 2: Identify L-SIG pilot SCs in code**

```bash
grep -n "lsig.*pilot\|pilot.*lsig\|-21.*pilot\|+21.*pilot" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -10
```

- [ ] **Step 3: Design L-SIG per-symbol CPE**

Modify HT_SIG_CAND to also try per-L-SIG-symbol CPE. Apply θ_lsig = arg(mean(Y_pilot * X_pilot^*)) per L-SIG symbol, then re-equalize HT-SIG with corrected L-SIG CPE.

- [ ] **Step 4: Add IEEE80211_LSIG_PER_SYMBOL_CPE=1 env var**

Default OFF.

- [ ] **Step 5: Test on 5250 self-TX capture**

```bash
cd /home/hy/gr-ieee802-11
unset IEEE80211_H52_NULL_COMBO
export IEEE80211_H52_NULL_INTERP=1
export IEEE80211_H52_NULL_THRESH=0.03
export IEEE80211_H52_INTERP_RADIUS=5
export IEEE80211_HTSIG_PILOT_CPE=1
export IEEE80211_LSIG_RATE_FORCE=0xD
export IEEE80211_TIMING_OFFSET_APPLY=1
export IEEE80211_LSIG_PER_SYMBOL_CPE=1

python examples/p68_replay_offline.py \
    --in /tmp/p76_selftx_5250.bin --loop 5 \
    --out-log /tmp/p77a_lsig_cpe_5250.log

grep -c "HT_SIG_PARSE_OK\|HT_SIG_CAND\|FCS_OK\|LSIG_DECODE OK" /tmp/p77a_lsig_cpe_5250.log
grep "HT_SIG_PARSE" /tmp/p77a_lsig_cpe_5250.log | sort -u | head -10
```

- [ ] **Step 6: Compare with Phase 76 baseline**

- avg_snr_htsig: was 2-3 dB, target ≥6 dB
- HT_SIG_EQ std_im: was 0.77-1.88, target ≤0.3
- HT_SIG_CAND count: was 576, should be similar or more

- [ ] **Step 7: Commit**

```bash
git add lib/frame_equalizer_impl.cc docs/superpowers/notes/2026-07-03-phase77-task1-lsig-cpe.md
git commit -m "feat(p77a): per-symbol L-SIG CPE on USRP at 5250 MHz"
```

---

## Task 2: 77b — HT-SIG LLR-based soft viterbi

**Files:**
- Modify: `lib/decoder_impl.cc` (HT-SIG viterbi branch metric to soft-LLR)
- Modify: `lib/frame_equalizer_impl.cc` (pass |H52| weighting to viterbi)

**Goal:** Convert HT-SIG viterbi branch metric from hard-bit Hamming distance to soft-LLR weighted by |H52|. Per Phase 44 analog but with 5250 MHz clean channel.

- [ ] **Step 1: Read Phase 44 soft-LLR implementation**

```bash
grep -n "SOFT_LLR\|soft_llr\|branch_metric" /home/hy/gr-ieee802-11/lib/decoder_impl.cc | head -20
```

- [ ] **Step 2: Verify Phase 44 is still opt-in**

```bash
grep -n "IEEE80211_SOFT_LLR_VITERBI" /home/hy/gr-ieee802-11/ -r | head -5
```

- [ ] **Step 3: Re-enable soft-LLR for HT-SIG specifically**

Modify decoder_impl.cc to use soft-LLR when `IEEE80211_HT_SIG_SOFT_LLR=1` (new env var).

- [ ] **Step 4: Test on 5250 self-TX**

```bash
export IEEE80211_HT_SIG_SOFT_LLR=1
export IEEE80211_H52_NULL_INTERP=1
export IEEE80211_H52_NULL_THRESH=0.03
export IEEE80211_H52_INTERP_RADIUS=5
export IEEE80211_HTSIG_PILOT_CPE=1
export IEEE80211_LSIG_RATE_FORCE=0xD
export IEEE80211_TIMING_OFFSET_APPLY=1

python examples/p68_replay_offline.py \
    --in /tmp/p76_selftx_5250.bin --loop 5 \
    --out-log /tmp/p77b_htsig_soft_5250.log

grep -c "HT_SIG_PARSE_OK\|HT_SIG_CAND\|FCS_OK" /tmp/p77b_htsig_soft_5250.log
```

- [ ] **Step 5: Commit**

```bash
git add lib/decoder_impl.cc lib/frame_equalizer_impl.cc docs/superpowers/notes/2026-07-03-phase77-task2-htsig-soft.md
git commit -m "feat(p77b): HT-SIG soft-LLR viterbi re-test on 5250 MHz"
```

---

## Task 3: 77c — Per-frame H52 refinement

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (apply L-LTF0+L-LTF1 jointly with weighting)

**Goal:** Improve H52 estimation by jointly using L-LTF0 and L-LTF1 with SNR-weighted averaging (current default uses LTS0/1/avg/median per Phase 27 REFUTED).

- [ ] **Step 1: Read current H52 estimation code**

```bash
grep -n "estimate_header_channel_from_lltf52\|H52.*LTS\|H52.*median\|H52.*average" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -20
```

- [ ] **Step 2: Design SNR-weighted H52**

Use weighted average where weights are |H52_LTS0| and |H52_LTS1|.

- [ ] **Step 3: Test on 5250 self-TX**

- [ ] **Step 4: Commit**

```bash
git add lib/frame_equalizer_impl.cc docs/superpowers/notes/2026-07-03-phase77-task3-h52-refine.md
git commit -m "feat(p77c): SNR-weighted H52 estimation refinement"
```

---

## Task 4: 77d — Accept HT-SIG closure (if 77a-77c REFUTED)

**Files:**
- Create: `docs/superpowers/notes/2026-07-03-phase77-closure.md`

**Goal:** If 77a-77c all REFUTED, accept HT-SIG closure per Phase 41 reaffirmation with documented HT-SIG chain visibility (576 candidates fire at 5250).

- [ ] **Step 1: Verify all 77a-77c REFUTED on USRP 5250**

- [ ] **Step 2: Document Phase 41 reaffirmation**

HT-SIG chain fires (576 candidates), but viterbi SNR insufficient. 17+ REFUTED hypotheses total. Software loopback 3/3 PASS is decoder validation path.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/notes/2026-07-03-phase77-closure.md
git commit -m "docs(p77): accept HT-SIG closure per Phase 41 reaffirmation"
```

---

## Self-Review

**1. Spec coverage:** Phase 77 attacks HT-SIG viterbi upstream (77a L-SIG CPE, 77b soft-LLR, 77c H52 refinement, 77d closure). All paths produce USRP end-to-end evidence at 5250 MHz. ✓

**2. Placeholder scan:** No "TBD" placeholders. Each task has concrete code locations and env vars. ✓

**3. Type consistency:** Env var names match between tasks (`IEEE80211_LSIG_PER_SYMBOL_CPE`, `IEEE80211_HT_SIG_SOFT_LLR`). ✓

**Notes:**
- Tasks 1-3 require rebuild (`make && make install`) after C++ changes
- Task 4 only requires documentation if upstream tasks fail
- All tasks use 5250 MHz self-TX capture (`/tmp/p76_selftx_5250.bin`)
