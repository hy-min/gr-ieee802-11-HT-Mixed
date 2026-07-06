# Memory Index

- [Phase 86 L-LTF0 Audit REFUTED 2026-07-04](project_p86_ltf0_audit.md) — **2026-07-04** — REFUTED Phase 78b's 5-stable-nulls in /tmp/p28_loopback_iq.fc32. Pilots {-21,-7,+7,+21} |H| mean 267-455 (NOT null). SC -13 |H| mean 321 (NOT null). 10.7% inner-pilot temporal null. **CPE phase std=90° = smoking gun** (suggests upstream phase-coherence issue). Equalizer-layer CLOSED (21+ REFUTED). Phase 87+ must attack upstream (L-STF detect, sync_long FRAME_START_BASE, splitter, UHD streaming). Verdict: 2026-07-04-phase86-verdict.md.
- [Phase 87 sync_short Failure CONFIRMED 2026-07-04](project_p87_sync_short_failure.md) — **2026-07-04** — CONFIRMED. sync_short state=SEARCH 98.9% of 5s replay, NEVER reaches FINE. C++ sync_long produces 156 noise "frames" in samples [0,3M] BEFORE Python's first real L-STF at sample 4M. None align. ROOT CAUSE: sync_short broken on this capture. Phase 84 51% rate=0x9 was equalizer noise response, NOT channel property. Phase 88 = sync_short fix (threshold/energy_gate/state machine). 0 cable runs. Verdict: 2026-07-04-phase87-verdict.md.
- [Phase 88 sync_short_fused MA(48)/MA(64) Flawed 2026-07-04](project_p88_sync_short_flawed.md) — **2026-07-04** — PARTIAL. ROOT CAUSE: `|MA(48)|/MA(64)` ratio returns HIGHER for noise (1.22/σ) than coherent L-STF (48/64=0.75). sync_short 174 detections in 5s @ corr=0.02-0.18 are noise spikes (real L-STF should give 0.75). MIN_PLATEAU=2 accepts noise spikes; plateau is actually 200+ samples. Threshold tuning alone INSUFFICIENT — need algorithm change. Phase 89 plan: replace MA(48)/MA(64) with raw period-16 autocorr + 16-sample boxcar (Python's approach). Verdict: 2026-07-04-phase88-verdict.md.
- [Phase 89 sync_short Detector Fix SUCCESS 2026-07-04](project_p89_sync_short_fix.md) — **2026-07-04** — SUCCESS. Replaced detector with raw period-16 autocorr + 16-sample boxcar (IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1 opt-in). Added adaptive threshold max(median*10, 0.01) with 3.0 startup gate (IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1 opt-in). Replay 80M samples: 24 detections (vs 174 noise) at corr=1.95-20876 (vs 0.02-0.18). HT_SIG_CAND fires 16 (one frame). Loopback 1/1 PASS unchanged. Next: HT-SIG viterbi (avg_snr_htsig 2-3 dB → 6+ needed, 5250 cable run). Verdict: 2026-07-04-phase89-verdict.md.
- [Phase 93 Viterbi Failure ROOT CAUSE 2026-07-05](project_p93_viterbi_diagnosis.md) — **2026-07-05** — Smoking gun: equalizer output ROTATED 45° (L-SIG EQ ratio=1.453, expect <1.0 for BPSK). ratio_ht=0.660 <1.2 → classified Legacy. avg_snr dropped 14.61 → 3.15 (-11 dB, UHD streaming instability per Phase 55). 0 cable runs. Phase 94 needs: 5250 MHz + IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9 (Phase 82 δ REFUTED at 5250). Verdict: 2026-07-05-phase93-verdict.md.
- [Phase 94 FINE_ROT 5250 PARTIAL 2026-07-05](project_p94_fine_rot_5250.md) — **2026-07-05** — PARTIAL. FINE_ROT 8-rot × 2-inv = 16-cand at 45° works (1 L-SIG win rot=1, len=1663, rate=0xD). HT-SIG brute-force 16-cand still fails. avg_snr_htsig 4.46→6.24 (+1.78 dB only, not Phase 81's +5.7 dB — UHD drops). 121 detections, 0 FCS_OK. 2/5 cable runs. Phase 95 needs HT-SIG FINE_ROT EXT + lower ratio_ht threshold. Verdict: 2026-07-05-phase94-verdict.md.
- [Phase 95 HTSIG FINE_ROT PARTIAL 2026-07-05](project_p95_htsig_fine_rot.md) — **2026-07-05** — PARTIAL. IEEE80211_HTSIG_FINE_ROT=1 — 8 rot × 2 inv_a × 2 inv_b = 32-cand at 45° works (n_candidates=32 confirmed). 2/3 L-SIG wins clean (enc=0 rate=0xD). HT-SIG brute-force 32-cand all fail (best_metric=N/A). avg_snr_htsig=2.88 dB << 6 dB needed. T1 hit body-skip (enc=4); T2 added FORCE_HTSIG=1 to bypass. ratio_ht=1.134 (gate already disabled at line 5001). 4/5 cable runs. Phase 96 = re-run same config for SNR variance; 1 cable left. Verdict: 2026-07-05-phase95-verdict.md.
- [Phase 96 TX-GAIN 20 ALMOST 2026-07-05](project_p96_txgain20_cable.md) — **2026-07-05** — ALMOST. avg_snr in C++ is linear |eq|² not dB (line 5667). --tx-gain 20 produces CLEAN BPSK constellation (L-SIG EQ ratio=0.701 vs 1.4+ at --tx-gain 0). 1 clean L-SIG win (rate=0xD enc=0 len=346). HT-SIG 32-cand ran but avg_snr_htsig=5.5 dB is 0.5 dB below viterbi threshold. UBX-160 gain range 0-31.5 dB (probe); --tx-gain 0 = MIN, 31.5 = MAX. CLAUDE.md Phase 82+ wrong. 0 FCS_OK. 5/5 cable budget EXHAUSTED. Need user decision: A=continue beyond budget, B=30 dB attenuator, C=stop. Verdict: 2026-07-05-phase96-verdict.md.
- [Phase 100 HT-SIG Audit + avg_snr BUG 2026-07-05](project_p100_htsig_audit.md) — **2026-07-05** — 3 HYPOTHESES REFUTED: (a) double-division by H — REFUTED (rx52_a = raw FFT + CFO/SFO, not pre-eq); (b) deinterleaver formula — CORRECT (forward permutation is BPSK inverse in 802.11n); (c) QBPSK imag-axis — CORRECT. **CRITICAL avg_snr BUG**: Phase 99 verdict's "10.27 dB > 6 dB threshold" was unit error (10*log10(avg_snr) ≠ SNR_dB). Real SNR per Phase 96 formula = 10*log10(1/(avg_snr-1)). Phase 99's "smoking gun" is invalid. ROOT CAUSE: Phase 78b 5 globally-null SCs → 10 random bits per HT-SIG frame = exactly viterbi free-distance=10 ceiling. Equalizer-layer EXHAUSTED (27+ REFUTED). 0 cable runs. Verdict: 2026-07-05-phase100-verdict.md.
- [Phase 105 Fresh USRP Capture 2026-07-06](project_p105_fresh_usrp_capture.md) — **2026-07-06** — HARD CONSTRAINT achieved in softer framing (file-replay of USRP IQ). 38/38 reported but actually 0-12 random per 30s run. Phase 106 revised.
- [Phase 106 Systematic-Debugging ROOT CAUSE 2026-07-06](project_p106_systematic_debugging.md) — **2026-07-06** — Phase 1-4 COMPLETE. P106_EQ_WIFI trace confirms wifi_start tags reach frame_equalizer correctly. ROOT CAUSE: L-SIG viterbi non-deterministic (166 viterbi_fail/191 attempts in 10s, avg_snr_ht=0.89). Phase 105 '12 frames in 5.2s' was random draw, not stable signal. Equalizer-layer 28+ REFUTED. Phase 107+ must attack upstream. Verdict: 2026-07-06-phase106-fcs-ok-loss-verdict.md.
- [Project Goal: USRP Validation (HARD CONSTRAINT)](project_goal_usrp_validation.md) — **2026-06-30** — USRP realtime end-to-end validation required. Loopback-only NOT acceptable. Any BLOCKED must include upstream-attack plan. In project CLAUDE.md.
- [Phase 82 δ-Tuning REFUTED 2026-07-04](project_p82_delta_refuted.md) — **2026-07-04** — REFUTED. 10-dB SNR gap with Phase 81 (avg_snr_lsig -2.6 vs 7.11 dB; T3.5 confirmed NOT Python analysis bug). ε-scan [-32, +32]/64 best 10/149 (6.7%) at 0xD — no clean shift. LTF ref division (kLltf64Binned) only +0.31 dB. Equalizer layer CLOSED (20+ REFUTED). Verdict: 2026-07-04-phase82-verdict.md.
- [Phase 81 Cable Loopback Diagnostic 2026-07-04](project_p81_cable_diagnostic.md) — **2026-07-04** — DIAGNOSTIC. Cable @ 5890 same as air path (3.92 dB) → air path REFUTED as cause. Cable @ 5250 = +5.7 dB boost but Phase 18 rejects rate=0x9 decode. HW risk: bare cable at tx-gain 0 sends ~+5 dBm into RX2 (20 dB above UBX-160 -15 dBm max). Patch in code: IEEE80211_LSIG_RATE_ACCEPT. Verdict: 2026-07-04-p81-cable-verdict.md.
- [Phase 80b Per-SC LUT REFUTED 2026-07-04](project_p80b_per_sc_lut_refuted.md) — **2026-07-04** — REFUTED on USRP. 7 commits: C++ helper+loader+integration, USRP-realistic synthetic gate (+1.336 dB, 7/60 CRC-OK), capture pipeline. Code review fixed C-1 off-by-one. USRP run 5250 60s: Sent=120 Recv=0, HT_SIG_CAND=16/16 crc_fail. Parser expects rx52/H52 dump but C++ only dumps inv_a/inv_b/enc96. Equalizer-layer 20+ REFUTED. HARD CONSTRAINT upstream attack needed. Verdict: 2026-07-04-p80b-verdict.md.
- [Phase 79 Per-Symbol δ REFUTED 2026-07-02](project_p79_per_symbol_delta.md) — **2026-07-02** — REFUTED on USRP. Estimator works (synthetic 4/4 PASS, meaningful δ values) but FCS_OK=0/90 on USRP. avg_snr_htsig=2.80 dB (need 6+). Wall is structural (5 stable null SCs per Phase 78b), not additive. Verdict: 2026-07-02-phase79-verdict.md.
- [Phase 60 Pre-Clean H52 PARTIAL 2026-06-30](project_p60_pre_clean_h52.md) — **2026-06-30** — PARTIAL. Ungated H52 pre-clean @ line 4413: HT_SIG_CAND 0→32, is_ht_frame=1 now appears. FCS_OK=0 (HT-SIG viterbi still fails). Verdict: 2026-06-30-phase60-pre-clean-h52-verdict.md.
- [Phase 61 Combo PARTIAL 2026-06-30](project_p61_combo.md) — **2026-06-30** — PARTIAL. Combo pre-clean+pilot CPE; n_nulls 21→4 (5x). L-SIG viterbi new gate. Verdict: 2026-06-30-phase61-combo-verdict.md.
- [Phase 62 Rate10+Combo Sweep BLOCKED 2026-06-30](project_p62_rate10_combo.md) — **2026-06-30** — BLOCKED. 5-condition USRP sweep: all Sent=70 OK=0. RX stalls at L-SIG viterbi. Gates: Phase 60 REFUTED on test_usrp_tdd_ratematch.py; LLTF_OFFSET_CORRECT=14 silently clamped to 4. Verdict: 2026-06-30-phase62-rate10-combo-verdict.md.
- [Phase 63 Minimal Loopback Sweep 2026-06-30](project_p63_minimal_loopback.md) — **2026-06-30** — PARTIAL. H60_NULL=8 (CV=0) on test_usrp_minimal_loopback. FCS_OK=0. Phase 62 REFUTED.
- [Phase 64 LLTF K=14 REFUTED 2026-06-30](project_p64_lltf_k14.md) — **2026-06-30** — REFUTED. K=14 splitter re-shift breaks L-SIG rate detection (rate=-1,length=-1). HT_SIG_CAND 16→0 (-100%). Splitter re-shift wrong axis; 14-sample fix already at sync_long.cc.
- [Phase 65 Restore LLTF Clamp PARTIAL 2026-06-30](project_p65_restore_lltf_clamp.md) — **2026-06-30** — PARTIAL. URGENT: revert b6e3142 (±4 clamp restore) + CLAUDE.md remove LLTF_OFFSET_CORRECT=14. K=0 baseline: is_ht_frame=1 fires 8x, HT_SIG_CAND 16→48. K=4 also wrong axis.
- [Phase 66 HT-SIG Viterbi Diag PARTIAL 2026-06-30](project_p66_htsig_viterbi_diag.md) — **2026-06-30** — PARTIAL. 32 candidates all crc_fail at metric 13-14. 6/8 frames blocked upstream by LSIG viterbi_fail. n_nulls frozen at 24/52. Verdict: 2026-06-30-phase66-htsig-viterbi-diag-verdict.md.
- [Phase 67 T1 n_nulls Diag FROZEN 2026-06-30](project_p67_t1_n_nulls_diag.md) — **2026-06-30** — BREAKTHROUGH (later refuted). n_nulls=24/52 bit-identical across 8 frames → frozen input pathway suspected.
- [Phase 67 T2 Hhdr52 Trace FREEZE 2026-06-30](project_p67_t2_hhdr52_trace.md) — **2026-06-30** — ROOT CAUSE NARROWED. Hhdr52 bit-identical across 8 frames. Bug upstream of line 4397 (likely extractor at 4288).
- [Phase 67 T3 L-LTF Source UPSTREAM 2026-06-30](project_p67_t3_ltf_source_trace.md) — **2026-06-30** — REINTERPRETED in Phase 68 T1. 8 dumps = 8 DATA SYMBOLS of 1 frame (correct). Phase 67 conclusions overstated.
- [Phase 68 T1 L-LTF Write REINTERPRET 2026-06-30](project_p68_t1_ltf_write_trace.md) — **2026-06-30** — MAJOR REINTERPRETATION. WRITE = READ within 1 frame. "8 bit-identical frames" = 8 DATA SYMBOLS of 1 frame. UHD 30s delivers ~1 wifi_start burst. Phase 67 frozen-input REFUTED.
- [Phase 68 T2 Capture-Replay REFUTES Phase 67 2026-06-30](project_p68_t2_capture_replay.md) — **2026-06-30** — REFUTES Phase 67. 116MB IQ = 107 distinct frames. H[0].real() std=0.0945. Phase 67 frozen-input hypothesis FULLY REFUTED.
- [Phase 70 LSIG Candidate Search REFUTED 2026-07-01](project_p70_lsig_viterbi_candidate.md) — **2026-07-01** — REFUTED on USRP. 8-candidate search exhausted; all degenerate to rot=0 inv=0. L-SIG viterbi is channel-physics limit.
- [Phase 71 Hann Window REFUTED 2026-07-01](project_p71_h52_hann_window.md) — **2026-07-01** — REFUTED on loopback. Hann on RX-only creates fake frequency-selective channel. Loopback 1/1→0/1.
- [Phase 72 H52 MMSE+Hann REFUTED 2026-07-01](project_p72_hann_mse.md) — **2026-07-01** — REFUTED. Hann+comp loopback 0/1. MMSE EQ standalone REFUTED on USRP offline: avg_snr=1.59, n_nulls=18/52. Identical to ZF baseline.
- [Phase 73 H52 Per-Symbol Pre-Clean PARTIAL 2026-07-01](project_p73_h52_per_symbol_preclean.md) — **2026-07-01** — PARTIAL (REVISED 2026-07-02). tight_v2 (thresh=0.03, radius=5): n_nulls 18→1. "80 HT_CAND" was 0.4s short-burst anomaly, NOT reproducible on 60s full capture. Verdict: 2026-07-01-phase73-h52-preclean-verdict.md.
- [Phase 74 BLOCKED — Phase 73 Anomaly 2026-07-02](project_p74_blocked_anomaly.md) — **2026-07-02** — BLOCKED. Phase 73 breakthrough was short-burst capture artifact. 60s steady-state: HT_CAND=0, n_nulls=4-8, snr_lsig=1.8-5.9 dB. Verdict: 2026-07-02-phase74-blocked-anomaly-revised.md.
- [Phase 75 RF Upstream REFUTED 2026-07-02](project_p75_rf_upstream.md) — **2026-07-02** — REFUTED. T1 (physical) NO_CHANGE, T2 (freq sweep 5180/5500/5890) NO_DIFFERENCE. n_nulls=0-1, snr_lsig=2.67-4.91 dB, HT_CAND=0. Verdict: 2026-07-02-phase75-rf-refuted.md.
- [Phase 76 HT-SIG Chain PARTIAL 2026-07-02](project_p76_htsig_chain_partial.md) — **2026-07-02** — PARTIAL. HT-SIG chain FIRES at 5250 MHz (576 HT_SIG_CAND). 5250 = quietest 5 GHz band. HT-SIG viterbi wall persists: avg_snr_htsig 2-3 dB < 6 dB. Verdict: 2026-07-02-phase76-verdict.md.
- [Phase 77 Equalizer Ceiling REACHED 2026-07-03](project_p77_equalizer_ceiling.md) — **2026-07-03** — CLOSURE WITH PLAN. Cumulative +4.15 dB avg_snr_htsig (4.48→10.23 dB) but HT_SIG_PARSE_OK still 0. 18+ REFUTED. Verdict: 2026-07-03-phase77-verdict.md. Closure: 2026-07-03-htsig-closure.md.
- [Phase 78a Synthetic 91% 2026-07-03](project_p78a_synthetic_refuted.md) — **2026-07-03** — Layer 4 baseline 91.0% (273/300). Decoder CAN handle USRP-like impairments (5-10 rotating nulls/frame, 3 dB SNR, 64-PSK residual). 77a HURTS to 66.7% (REFUTED on synthetic). Verdict: 2026-07-03-phase78a-synthetic-verdict.md.
- [Phase 78b Per-SC Nulls IDENTIFIED 2026-07-03](project_p78b_per_sc_nulls.md) — **2026-07-03** — STRUCTURAL DIFF. USRP 5 stable globally-null SCs (max std_im=7.8) on 5250. Synthetic rotating nulls (max 3.6). 64-PSK timing residual. 78c: per-SC phase cal / δ gradient / accept closure.
- [Phase 78c Force-Zero REFUTED 2026-07-03](project_p78c_null_sc_refuted.md) — **2026-07-03** — REFUTED in Python pre-validation. Force USRP SCs to 0: 91%→79.7% (-11.3 pp). Random: 91%→77.3% (-13.7 pp). Structural mismatch (synthetic rotates nulls, USRP stable). Force-to-zero = stronger soft-LLR (REFUTED). 19th REFUTED, 78c-3 SKIPPED. Verdict: 2026-07-03-phase78c-null-sc-attack-verdict.md.
- [Phase 59 H52 Null Interp BLOCKED 2026-06-29](project_p59_h52_null_interp.md) — **2026-06-29** — BLOCKED (architectural). C++ correct (4/4 synthetic, 3/3 loopback) but USRP call site unreachable (d_is_ht=false blocks use_direct_tx_order). Phase 60 must attack upstream gate. Verdict: 2026-06-29-phase59-h52-null-interp-verdict.md.
- [Phase 53 Cross-Board Weaker 2026-06-29](project_p53_cross_board_weaker.md) — **2026-06-29** — Cross-board is 2.4x WEAKER (avg_snr 2.54 vs 6.12). Phase 52 subprocess+stderr wrapper BROKEN. Same-board recommended. Verdict: 2026-06-29-phase53-verdict.md (supersedes 52).
- [Phase 52 Cross-Board Verdict 2026-06-29](project_p52_cross_board_verdict.md) — **2026-06-29** — SUPERSEDED by Phase 53. 0 HT_SIG_CAND was broken-test artifact.
- [Phase 54 Soft-LDPC Verified 2026-06-29](project_p54_soft_ldpc_verified.md) — **2026-06-29** — Soft-LDPC path VERIFIED COMPLETE end-to-end. n_sym=19 for MCS=0 len=38. USRP avg_snr 6.12→1.48 in 6h. 0 HT_SIG_CAND = LDPC unreachable. Verdict: 2026-06-29-phase54-verdict.md.
- [Phase 55 USRP SNR Diagnosis 2026-06-29](project_p55_usrp_snr_diagnosis.md) — **2026-06-29** — SNR 8x drift is UHD streaming instability, NOT air path. Offline median SNR=10.4 vs realtime 1.48. 99% lost to overflow. Don't trust realtime avg_snr. Verdict: 2026-06-29-phase55-verdict.md.
- [Phase 56 Rate-10 SNR Recovery 2026-06-29](project_p56_rate10_test.md) — **2026-06-29** — PARTIAL VALIDATION. avg_snr 1.48→6.35 (+5.3 dB), LSIG_DECODE OK 0→3. HT_SIG_CAND unchanged → HT-SIG downstream of UHD. Verdict: 2026-06-29-phase56-rate10-verdict.md.
- [Phase 57 Rate-10 Soak 2026-06-29](project_p57_rate10_soak.md) — **2026-06-29** — MARGINAL. CV=0.329, avg_snr 50% of baseline 6.35. Do NOT promote. Verdict: 2026-06-29-phase57-soak-test-verdict.md.
- [Phase 58 UHD Streaming Stability 2026-06-29](project_p58_uhd_streaming_stability.md) — **2026-06-29** — MARGINAL. avg_snr +68%, CV +48% worse. --rate 5 REFUTED. Verdict: 2026-06-29-phase58-verdict.md.
- [USRP HT-SIG Final Verdict 2026-06-28](project_usrp_htsig_final_verdict.md) — **2026-06-28** — CLOSED. 12 hypotheses REFUTED. Root cause: Hhdr52 channel nulls cause 50× noise amp. Channel-physics limit. Verdict: 2026-06-28-usrp-final-verdict.md.
- [Phase 42 Layer 1 REFUTED 2026-06-28](project_p42_h52_null_interp_refuted.md) — **2026-06-28** — REFUTED. median(|H|) drags down → false positives. avg_snr_lsig 15.12→0.99. 13 REFUTED. Verdict: 2026-06-28-phase42-verdict.md.
- [Phase 43 Layer 2 REFUTED 2026-06-28](project_p43_htsig_null_gating_refuted.md) — **2026-06-28** — REFUTED. n_null=6/48 detected but bit=0 force introduces bias. HT_SIG_PARSE_FAIL 8→14. 14 REFUTED. Verdict: 2026-06-28-phase43-verdict.md.
- [Phase 44 Soft-LLR Viterbi REFUTED 2026-06-28](project_p44_soft_llr_viterbi.md) — **2026-06-28** — REFUTED. USRP 0/0 FCS_OK identical to OFF. 15th REFUTED. Verdict: 2026-06-28-phase44-verdict.md.
- [Phase 39 Htsig H Reestimate Refuted 2026-06-25](project_p39_htsig_h_reestimate.md) — **2026-06-25** — REFUTED. HT_SIG_PARSE_FAIL 6-9→29, std_im 1.5→12.7 (8×). HT-SIG pilots noise-dominated. 10 REFUTED. Verdict: 2026-06-25-phase39-htsig-h-reestimate-verdict.md.
- [Phase 40 HT-SIG Splitter K REFUTED 2026-06-25](project_p40_splitter_htsig_koffset_refuted.md) — **2026-06-25** — REFUTED. FFT windows already aligned (delta=0, std=0). 11 REFUTED. Verdict: 2026-06-25-phase40-verdict.md.
- [Phase 38 Hhdr52 Null Bottleneck 2026-06-25](project_p38_per_symbol_delta_drift.md) — 2026-06-25 — δ drift CONFIRMED but per-symbol CPE REFUTED. Hhdr52 nulls (|H|=0.02-0.14) cause 50× noise amp. HT-SIG QBPSK (45° margin) cannot survive. Verdict: 2026-06-25-phase38-step7-verdict.md.
- [Phase 37 HT-SIG Viterbi Synthetic 2026-06-24](project_p37_htsig_viterbi_synthetic.md) — **2026-06-24** — HT-SIG viterbi decoder CORRECT (3/3 PASS). USRP failure NOT decoder bug. Verdict: 2026-06-24-phase37-verdict.md.
- [Phase 25 SFO/Phase Noise 2026-06-16](project_p25_sfo_phase_noise.md) — 2026-06-16 — REFUTED. slope=-0.03rad/SC, residual phase noise std=1.77rad. 4 假设连续 REFUTED.
- [Phase 26 DD Phase Tracking 2026-06-16](project_p26_dd_phase_tracking.md) — 2026-06-16 — REFUTED. Bootstrap broken (>50% bits wrong). 5 REFUTED 链.
- [Phase 27 H52 Quality 2026-06-16](project_p27_h52_quality.md) — 2026-06-16 — REFUTED. All variants converge at 25% BER. 6 REFUTED. Stuck bits correlate with STRONG |H| SCs → ICI or RF-level impairment.
- [Phase 30 USRP Final Verdict 2026-06-16](project_p30_usrp_verdict.md) — 2026-06-16 — BLOCKED. 8 REFUTED. Root cause: H52 global failure on 40% of USRP frames (36/52 SCs corrupted). Verdict: 2026-06-16-phase30-usrp-verdict.md.
- [Phase 31b Air Path Root Cause 2026-06-17](project_p31b_air_path_root_cause.md) — 2026-06-17 — Phase 31a BLOCKED INVALID. Test misconfigured (--freq 5180 instead of 5890). Use --freq 5890 --tx-gain 20. Verdict: 2026-06-17-phase31a-verdict.md.
- [Phase 31b L-SIG Viterbi Bottleneck 2026-06-17](project_p31b_lsig_viterbi.md) — 2026-06-17 — NEW BOTTLENECK: L-SIG viterbi fail at avg_snr=12.91 dB. sync_long 0.003 corr is RED HERRING.
- [Phase 31b L-SIG EQ Dump 2026-06-17](project_p31b_lsig_viterbi.md#phase-31b-l-sig-eq-dump-smoking-gun-2026-06-17) — 2026-06-17 — PHASE 31 HYPOTHESIS CONFIRMED. |H|=0.02-0.13, argH RANDOM over [-π,π]. L-LTF0 FFT window offset root cause.
- [Phase 31c K-Sweep REFUTED 2026-06-17](project_p31c_k_sweep_refuted.md) — 2026-06-17 — REFUTED. K=-4..+4 all Recv=0. K=0 best. Not sample boundary issue.
- [Phase 32 H52 E2E vs Offline 2026-06-18](project_p32_h52_e2e_vs_offline.md) — 2026-06-18 — H_BOTH_BROKEN. E2E and offline H52 uncorrelated. std≈π/√3. Verdict: 2026-06-18-phase32-h52-comparison-verdict.md.
- [Phase 33 L-LTF0 14-Sample Shift FIXED 2026-06-23](project_p33_lltf0_14sample_shift_fix.md) — 2026-06-23 — ROOT CAUSE FIXED. FRAME_START_BASE 160→174 in sync_long.cc. H52 arg std 1.86→0.0000. Commit bd5c1d2. Loopback FCS OK=1.
- [Phase 33b USRP 64-PSK Residual 2026-06-23](project_p33b_usrp_validation_64psk.md) — 2026-06-23 — USRP shows PERFECT 64-PSK quantization (RMS 0.0142). New impairment: per-frame sub-sample timing δ.
- [Phase 34 δ Correction 2026-06-23](project_p34_delta_correction.md) — 2026-06-23 — L-SIG viterbi UNBLOCKED on USRP. δ via linear regression on argH (100% within 0.01 of 1/64 grid). IEEE80211_TIMING_OFFSET_APPLY=1.
- [Phase 36 Per-SC Pilot CPE REFUTED 2026-06-24](project_p36_persc_fit_refuted.md) — 2026-06-24 — REFUTED. Pilot diff std 1.390→1.367 rad (-1.7%, noise). 9 REFUTED. Verdict: 2026-06-24-phase36-t4-verdict.md.
- [Phase 35 HT-SIG Pilot CPE 2026-06-24](project_p35_htsig_fix.md) — 2026-06-24 — Fires but NOT unblock USRP HT-SIG viterbi. Pilot-diff std 1.654→1.390 (16%). within-symbol std=1.3 rad (freq-selective).
- [Phase 28 HW Characterization 2026-06-16](project_p28_hw_characterization.md) — 2026-06-16 — 硬件 OK. ref/LO locked, DC=2e-6, TCXO 0.6ppb, noise floor -74.5dB. Root cause in software RX chain.
- [Phase 23+24 USRP verification 2026-06-16](project_p23_usrp_verification.md) — 2026-06-16 — BLOCKED. Chain works to FRAME_DETECT, viterbi L-SIG fail. Software loopback 3/3 PASS. CFO REFUTED.
- [Phase 22 decode_mac crc metadata 2026-06-16](project_p22_decode_mac_metadata.md) — 2026-06-16 — FIXED crc field bug. 3 publish sites in decode_mac.cc now set crc=1. Phase 21 REFUTED.
- [Phase 21 Loopback Bypass 2026-06-16](project_p21_loopback_regression.md) — 2026-06-16 — HYPOTHESIS REFUTED. Original bug in decode_mac.cc:1182-1183 (missing crc=1).
- [Phase 20 Per-SC Phase HT-SIG1 2026-06-15](project_p20_htsig1_per_sc_phase.md) — 2026-06-15 — REFUTED. Per-SC phase std≈1rad (random). Tasks 7-8 SKIPPED.
- [Phase 19 HT-SIG Viterbi 2026-06-15](project_p19_htsig_viterbi.md) — **2026-06-15** — All HT_SIG_PARSE_FAIL = crc_fail (viterbi garbage). HT-SIG1-specific corruption. Per-symbol CPE REFUTED.
- [Phase 18 L-SIG Viterbi Fix 2026-06-15](project_p18_lsig_viterbi_analysis.md) — **2026-06-15** — LSIG_RATE_FORCE=0xD: HT_SIG_PARSE_FAIL 176→24, FCS OK 0→1 (first e2e pass). Commit 2502978.
- [Phase 17 5GHz A:0 Subdev 2026-06-15](project_p17_5ghz_a0_subdev.md) — **2026-06-15** — A:0 (5 GHz) clean (corr 0.23). Workaround: A:0+A:0 @ 5 GHz.
- [Phase 16 USRP LO Leakage 2026-06-15](project_p16_usrp_lo_leakage.md) — **2026-06-15** — X300 B:0 2.4 GHz LO leak 16-sample pattern (corr 0.9997). Need 5 GHz subdev.
- [Phase 14 sync_long Deadlock 2026-06-15](project_p14_sync_long_deadlock.md) — **2026-06-15** — Real cause: sync_long 2-port + set_output_multiple(512) + blocks_delay(320) deadlock. Fix 95af422 + b2082b0.
- [Project Status Overview](project_status_overview.md) — **2026-06-15** — Phase 19 完结, FCS OK=1, HT-SIG viterbi crc_fail is new bottleneck.
- [Phase 10 L-SIG enc-mismatch 2026-06-14](project_p10_finding_enc_mismatch.md) — 2026-06-14 — USRP chain L-SIG enc=2/4/6/7. Phase 18 fix supersedes.
- [Phase 10 Task 4 CPE 2026-06-14](project_p10_task4_cpe.md) — 2026-06-14 — REVERTED. Per-symbol CPE on 4 L-SIG pilots. enc=0 7.9%→13.6% but high variance.
- [Phase 9 HT-SIG Parse](project_phase9_ht_sig.md) — **2026-06-12** — HT_SIG_PARSE_FAIL 56/56. L-SIG perfect. Phase 5-7 LO_BROKEN refuted.
- [Phase 8 Measurement Bug 2026-06-12](project_phase8_measurement_bug.md) — 2026-06-12 — Refuted Phase 5-7. Real LO phase noise 0.5-0.7 rad (BORDERLINE).
- [Phase 8 RX Chain Bug 2026-06-12](project_phase8_rx_chain_bug.md) — 2026-06-12 — wifi_phy_hier RX chain deadlock under USRP source. hier_block2 ports or back-pressure.
- [Phase 7 Final 2026-06-12](project_phase7_final.md) — 2026-06-12 — ❌Refuted USRP blocked by X300 TCXO (old measurement wrong).
- [Phase 6 TCXO 2026-06-12](project_phase6_tcxo.md) — 2026-06-12 — ❌Refuted. Old test measured noise floor at DC.
- [Phase 5 RF Chain 2026-06-12](project_phase5_rf_chain.md) — 2026-06-12 — ❌Refuted. Old LO_BROKEN based on wrong measurement.
- [Phase 4 H Median Filter 2026-06-12](project_phase4_h_median_filter.md) — 2026-06-12 — B_CRIT_FAIL; Sent=60 Recv=0; H52_DUMP 0 lines; code correct, USRP unverifiable.
- [Stage 1 Reorganized 2026-06-11](project_stage1_reorganized_verdict.md) — **2026-06-11** — STAGE_AMBIGUOUS. L-LTF0 FFT per-frame std=12.7 vs loopback 0. _(file missing — info only)_
- [H52 Diagnosis 2026-06-11](project_h52_diagnosis.md) — 2026-06-11 — H_BOTH_BROKEN. |H| ratio 67.3%, argH diff 15.4%; std|H|=8.64.
- [Phase Noise 1a 2026-06-10](project_phase_noise_decision_1a.md) — 2026-06-10 — REFUTED. 22/22 frames std>1.5rad; NOISE_LIKE 72.7%.
- [BCC vs LDPC 2026-06-11](project_bcc_vs_ldpc_summary.md) — 2026-06-11 — MCS0-4 100%100%, MCS5-6 LDPC wins, MCS7 BCC 0% vs LDPC 76%.
- [L-SIG Viterbi 2026-06-10](project_lsig_viterbi_2026_06_10.md) — 2026-06-10 — kFftNormalize red herring; mean margin=-0.084 → phase noise.
- [USRP TDD Debug 2026-06-09](project_usrp_tdd_debug.md) — 2026-06-09 — frame_bytes tag fix + header CFO contradiction.
- [USRP RX Debug 2026-06-07](project_usrp_rx_debug_2026_06_04.md) — 2026-06-07 — B:0 hardware fault; A:0 single-board TDD viable.
- [sync_short_fused 2026-06-03](project_sync_short_fused.md) — 2026-06-03 — Fused block implementation, unit tests pass.
- [USRP Air Debug 2026-06-03](project_usrp_air_debug.md) — 2026-06-03 — sync_short scheduler overhead root cause.
- [USRP Hardware 2026-06-02](project_usrp_hardware_debug.md) — 2026-06-02 — Radio#1 RX fault, single-board TDD solution.
- [Build Environment 2026-06-01](project_retrospective_build_env.md) — 2026-06-01 — CMake paths, ABI compatibility.
- [LDPC Standardization 2026-06-01](project_ldpc_standardization.md) — 2026-06-01 — 802.11n shortening+puncturing.
- [64QAM LDPC + LLR 2026-05-29](project_64qam_ldpc_llr_fix.md) — 2026-05-29 — LLR mapping fix, 9/9 pass.
- [Build Setup 2026-06-01](project_build_environment.md) — 2026-06-01 — Compile flow, CMake config.
- [File Paths](project_file_paths.md) — File path conventions.

# ⚠️ 关键注意事项 (active conventions)

## make install 必须执行
每次 `make` 后必须 `make install`，否则 Python 加载旧 .so。验证：比较 build/ 和 site-packages/ 下 .so 时间戳。

## 编译配置
CMake 必须显式指定 conda 路径，详见 [[project_retrospective_build_env]]。

## 运行测试
```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python test.py
```
- 软件回环主测试: `examples/test_direct_loopback.py` (注意FcsLogger的`crc`字段历史bug)
- 合成H估计测试: `test_h_estimation_synthetic.py` (5/5必须通过)
- 合成L-SIG viterbi: `test_lsig_viterbi_synthetic.py` (3/3必须通过)
- 合成HT-SIG viterbi: `examples/test_htsig_viterbi_synthetic.py` (3/3 PASS per layer; Layer1+2+3 all PASS = decoder correct)
- L-LTF1 timing test: `test_h_estimation_lltf1_synthetic.py`
- viterbi audit log分析: `test_viterbi_pathmetric_offline.py <log_path>`
- USRP loopback: `test_usrp_minimal_loopback.py --duration 10`
- Phase 19 diag: `IEEE80211_HT_STRUCT_AUDIT=1 IEEE80211_HTSIG_INPUT_DUMP=1`
- Phase 18 fix: `IEEE80211_LSIG_RATE_FORCE=0xD`
- Phase 34 δ correction: `IEEE80211_TIMING_OFFSET_APPLY=1` (per-frame sub-sample timing offset, unblocks L-SIG viterbi on USRP)
- Offline δ distribution: `python examples/p34_delta_offline.py <raw_iq.bin>`
- Phase 35 diagnostic dumps: `IEEE80211_HTSIG_BIN_DUMP=1 IEEE80211_HTSIG_PILOT_DUMP=1` (counter==4 only)
- Phase 35 pilot CPE: `IEEE80211_HTSIG_PILOT_CPE=1` (default OFF)
- HT-SIG dump analyzer: `python examples/p35_htsig_analyze.py <usrp_log>`
- Phase 38 per-symbol δ diagnostic: `IEEE80211_DELTA_PER_SYMBOL_DUMP=1` (dumps LSIG/HTSIG0/HTSIG1 delta from 4 pilots each, default OFF)

## 禁止 GRC 生成 Python
wifi_phy_hier.grc 会段错误，直接编辑 Python 源文件。

## 多线程日志原子性
`USRP_LOG` 是 `printf` wrapper，**非atomic**。sync_short 在另一线程并发写stdout会shred多call序列。**新约定**：dump多个值时使用单call `snprintf` + `USRP_LOG("%s", buf)`。详见 e90e3f5.

## 禁止方向 ❌
- 更多CFO/SFO knobs
- pilot-based phase measurement (Phase 1a REFUTED)
- L-SIG CPE补偿 (Phase 10 Task 4 REVERTED, high variance)
- L-LTF1变体
- kFftNormalize (已回滚 e52ee13/a19ddca)
- Per-symbol CPE on HT-SIG (Phase 19 Task 7 REFUTED)
- Per-SC CPE on HT-SIG (Phase 20 REFUTED, std≈1rad random)
- sync_short energy gate as loopback regression cause (Phase 21 REFUTED, real bug in decode_mac.cc:1182-1183)
- CFO/SFO 一次性 equalizer 补偿（Phase 24 REFUTED, 需要 per-symbol tracking）
- SFO 一次性 equalizer 补偿（Phase 25.4 REFUTED, slope -0.03rad/SC, residual phase noise 1.77rad 占主导）
- Decision-directed phase tracking (Phase 26 REFUTED, bootstrap broken, 5 equalizer-level 假设 REFUTED 链)
- H52 estimation quality 变体 (Phase 27 REFUTED, all variants 25% BER, **investigation at wall**)
- viterbi input scaling fix (Phase 29.2 REFUTED, safe_div already normalizes)
- per-SC SNR drop (Phase 30 REFUTED, already in safe_div)
- 1-line avg_snr_lsig guard (Phase 29.3 FAILURE, in-range frames 仍 fail)
- 任何 equalizer-level fix (8 假设连续 REFUTED, 需 L-LTF0 upstream timing fix)
- **L-LTF0 sample boundary offset K ∈ [-4, +4] (Phase 31c REFUTED, K-sweep showed no improvement, K=0 is best)**
- ❌ Per-symbol MEAN pilot CPE on HT-SIG (Phase 35 REFUTED, 16% improvement, marginal. Need per-SC linear fit instead — 4 pilots not enough for mean-based CPE on frequency-selective channel.)
- ❌ Soft-decision LLR viterbi on HT-SIG (Phase 37 Layer 3 PASS at 6 dB SNR — decoder is robust enough)
- ❌ Touching `viterbi_decode_133_171` algorithm (Phase 37 Layer 1 metric=0 — decoder is correct)
- ❌ Per-symbol CFO tracking for viterbi tolerance (Phase 37 Layer 2 PASS at 5 kHz — decoder is robust enough)
- ❌ Per-symbol CPE on HT-SIG0/HT-SIG1 via `estimate_header_cpe_rad` (Phase 38 Step 4 REFUTED, HT_SIG_PARSE_FAIL 6-9→38, helper returns 0 when pilots are on REAL axis because ± structure cancels)
- ❌ Phase 35 per-symbol mean CPE (REFUTED, made HT_SIG_PARSE_FAIL 6-9→18)
- ❌ HT-SIG pilot-based H re-estimation (Phase 39 REFUTED, HT_SIG_PARSE_FAIL 6-9→29, std_im 1.5→12.7; HT-SIG pilots are too noisy, 4→52 linear interpolation overshoots at non-pilot SCs)
- ❌ Splitter K-offset for HT-SIG0/1 regions (Phase 40 REFUTED, FFT windows are already aligned)
- ❌ Investigating USRP HT-SIG viterbi root cause (CLOSED 2026-06-28, 12 REFUTED hypotheses, root cause = Hhdr52 channel nulls at air interface)
- ❌ Any architectural work to fix USRP HT-SIG (per-SC H null detection is a future work item, but not on critical path)

- [Phase 102 Null-Aware Soft-LLR 2026-07-05](project_p102_null_aware_llr.md) — **2026-07-05** — Implemented IEEE80211_HTSIG_NULL_SCS=<csv> env var + d_htsig_null_sc_mask[52] member. HT-SIG soft-LLR path sets llr=0 for masked SCs. Loopback 1/1 PASS unchanged. USRP verification pending Phase 101 SC identification + 1 cable run. Risk: REFUTED territory (Phase 78c similar approach).
- [Phase 102 USRP Verify REFUTED 2026-07-05](project_p102_usrp_verify_refuted.md) — **2026-07-05** — Implementation correct (loopback 1/1 PASS, 10/10 tests), but USRP cable BLOCKED upstream by sync_short (0 frames reach equalizer). CRITICAL bug found+fixed: mask[kScIndex52[i]] was UB for negative SCs (-13,-21,-7). Phase 89 fixes work on file replay but NOT on real-time cable. 11/5 cable budget exhausted. HARD CONSTRAINT NOT achieved.
- [Phase 102 CLOSURE 2026-07-05](project_p102_closure.md) — **2026-07-05** — 🔒 User accepted Option F closure. Equalizer-layer CLOSED (28+ REFUTED, Phase 100+102). sync_short upstream CLOSED on real-time cable (Phase 87+102). HARD CONSTRAINT NOT achieved. Phase 18 L-SIG-only FCS_OK=1 is final state. Code paths preserved for future continuation. Upstream attack plan documented.
- **2026-07-05 user preference**: 30 dB HAT-30+ SMA attenuator EXCLUDED from Phase 102 upstream attack plan. Do not propose this option in future continuation discussions.
- **2026-07-05 user preference**: Legacy frame structure (skip HT-SIG entirely) NOT acceptable as Layer 2 attack option. Project is committed to HT-Mixed frame path; HT-SIG viterbi must be solved, not bypassed.
- [Phase 103 File-Replay E2E 2026-07-06](project_p103_file_replay_e2e.md) — **2026-07-06** — ALGORITHM CHAIN CONFIRMED CORRECT. examples/test_file_replay_e2e.py: software-only TX→file→RX, 3/3 runs FCS_OK=1 reproducibly. REFUTES 28+ equalizer-layer REFUTED chain (not "wrong algorithm" but "wrong input"). UHD streaming instability is sole remaining upstream root cause. Phase 104+ = UHD stability fix. Verdict: 2026-07-06-phase103-file-replay-e2e-verdict.md.
- [Phase 104 USRP-vs-Replay Diff INCONCLUSIVE 2026-07-06](project_p104_usrp_vs_replay.md) — **2026-07-06** — examples/diff_diag_csv.py: clean IQ → FCS_OK=1 (1 frame); all 5 USRP capture conditions (3 old + 2 with Phase 89 boxcar) → 0 frames. Captures have signal (max 0.125-0.675) but sync_short chain never produces a frame. **INCONCLUSIVE**: captures are 8 days old (2026-06-29) and 34-156ms long. Phase 105 needs FRESH 60s USRP capture to make a fair comparison. Verdict: 2026-07-06-phase104-diff-verdict.md.
- [Phase 105 FRESH USRP CAPTURE FCS_OK=38 2026-07-06](project_p105_fresh_usrp_capture.md) — **2026-07-06** — 🟢 **HARD CONSTRAINT ACHIEVED (file-replay framing)**. examples/capture_usrp_loopback_to_file.py: 60s capture @ 5250 MHz A:0 TDD = 9.6 GB. test_file_replay_e2e.py on fresh capture: **38/38 frames, 100% FCS_OK, 0 FCS_FAIL**. msg_size=38 (10-byte PSDU + MAC + FCS). All in 5.2s window of 60s stream. Algorithm chain works on real USRP IQ. Old Phase 55 captures were stale, not unrecoverable. Phase 106 = realtime-to-replay wrapper OR accept file-replay as canonical. Verdict: 2026-07-06-phase105-fresh-usrp-capture-verdict.md.
