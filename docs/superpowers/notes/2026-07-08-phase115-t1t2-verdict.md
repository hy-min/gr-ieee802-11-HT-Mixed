# Phase 115 T1+T2: 3-way averaging bug fix (2026-07-08)

**Branch**: TEST1
**Status**: 🟢 **PASS** — 3-way code path now actually fires (was dead code in Phase 114)

## TL;DR

The Phase 114 root cause (saved_htltf_52 populated at `extract_call_count==6`
in `extract_header52_from_sym64`, but `estimate_header_channel_from_lltf52`
called at `d_internal_symbol_counter==3` and `>=4` so `htltf_52_saved` was
always false at the 3-way check) is fixed by:

1. **T1**: Keep the original save site at `extract_call_count==6` (T1 originally
   tried to move it, but reverted — original is correct since the diagnostic
   shows `htltf_52_saved=1` after extract_call_pre=6)
2. **T2**: Add a NEW call to `estimate_header_channel_from_lltf52` at
   `d_internal_symbol_counter==6` in `general_work` (after CFO/SFO compensation
   on d_early_eqsym[6]). This new call runs AFTER the extract save, so
   `htltf_52_saved=true` is observed and 3-way fires.
3. **T3**: Override `d_H52_tx_order` with the 3-way H52 and set
   `d_H52_tx_order_valid=true`, so the existing DATA path's lazy
   `compute_H52_tx_order` is skipped and 3-way H is used. Also call
   `d_equalizer->set_H(h_eq_3way)` for the `d_equalizer->equalize()` path.

## Verification (loopback, no USRP)

`test_usrp_minimal_loopback.py --uhd-tune --htltf-avg` with
`IEEE80211_HTLTF_AVG_DEBUG=1`:

```
[H52_3WAY_AVG] wt0=27.4856 wt1=29.4837 wt_ht=28.2022 ratio_ltf01=0.932 ratio_ltf0ht=0.975
[T4D_DIAG] extract_call_count=7 (post-increment) g_htltf_avg=1 htltf_52_saved=1 extract_call_count_pre=6
[T4D_DIAG] 3way check: g_htltf_avg=1 htltf_52_saved=1
[T4D_DIAG] 3way stored: counter=6 d_H52_tx_order_valid=1 |H3way[-26]|=0.268938 |H3way[0]|=0.397724 |H3way[+26]|=0.0285726
```

- `H52_3WAY_AVG` log fires (was absent in Phase 114)
- `d_H52_tx_order_valid=1` (T3 path active)
- 3-way weights are ~balanced (0.93-1.10 ratios) → 3 sources contributing

## Why the fix is structurally correct

The original save at `extract_call_count==6` fires when
`d_internal_symbol_counter==6` (HT-LTF arrival) because both increment in
lockstep (verified by diagnostic: pre=6 → counter=6). The new H52 estimate
call at `d_internal_symbol_counter==6` runs AFTER the save (since
`extract_call_count++` happens at line 986 inside the function, returning
control to `general_work` before the new call at line ~5210). So the
`htltf_52_saved` static is true at the new call.

The 3-way blending inside `estimate_header_channel_from_lltf52` (lines
1155-1213) reads the static `saved_htltf_52` and computes H_HTLTF, then blends
with H_LTS0 and H_LTS1 using |H|-weighted averaging (Phase 77c scheme).

## Files Modified

- `lib/frame_equalizer_impl.cc:5194-5258` — new counter=6 H52 estimate block
  (T2 + T3 combined)
- `lib/frame_equalizer_impl.cc:963-984` — T1 reverted (original save kept)
- `lib/frame_equalizer_impl.cc:4013-4014` — `IEEE80211_HTLTF_AVG_DEBUG=1`
  (already added in Phase 114)
- `lib/frame_equalizer_impl.cc:5251-5257` — T4D_DIAG log gated by debug flag

## Loopback (no USRP) Status

- Sent=15, Recv=0 (no USRP connected, expected)
- 3-way H52 stored in `d_H52_tx_order` and used for DATA path
- `d_equalizer->set_H(h_eq_3way)` called for the equalizer path

## USRP Verification (pending)

Need to run on USRP 5250 cable 60s to verify:
- `H52_3WAY_AVG` log appears (was 0 in Phase 114)
- HT_SIG_CAND metric floor drops (Phase 114: 14-17, target ≤10)
- FCS_OK may improve (was 0, target ≥1)

The fix does NOT change Phase 113 baseline behavior because all new code is
gated by `d_apply_htltf_avg` (env var `IEEE80211_HTLTF_AVG=1`, default OFF).

## Phase 115 Next Steps

1. USRP 5250 cable 60s trace to verify metric floor drop
2. If metric ≤10 → potentially unblock HT-SIG viterbi
3. New equalizer architectures per user directive (DD / Kalman / alt H)
4. If metric still >10 → 3-way alone insufficient, R1 1.77 rad ceiling
   confirmed (see Phase 112 R1 verdict)

## Default OFF

- `IEEE80211_HTLTF_AVG=1` opt-in (default unset)
- `IEEE80211_H52_SNR_WEIGHTED=1` opt-in (default unset; required for 3-way)
- All new code paths gated on `d_apply_htltf_avg`
- `IEEE80211_HTLTF_AVG_DEBUG=1` for diagnostic logging (default OFF)

## Related

- Phase 114 stack verdict: `docs/superpowers/notes/2026-07-08-phase114-stack-verdict.md`
- Phase 114 root cause: `extract_call_count=7 (post) htltf_52_saved=1` but
  earlier calls at counter=3/4 had `htltf_52_saved=0`
- Phase 77c: 2-way SNR-weighted baseline (still working alongside 3-way)
- Phase 112 R1: 1.77 rad per-SC phase noise ceiling (still applies)
