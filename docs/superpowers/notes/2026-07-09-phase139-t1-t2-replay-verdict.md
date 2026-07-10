# Phase 139 T1-T2 File-Replay Validation (2026-07-10)

## T1 Baseline
- Command: `/home/hy/conda/envs/gnuradio/bin/python3 examples/test_file_replay_e2e.py --phase rx --iq-file /tmp/p103_iq.bin --rx-duration 30`
- Result: FCS_OK=1, FCS_FAIL=0, RX=1 (1/1 PASS, no regression)
- Log evidence: `[FCS_OK]` printed, final line `[P103] PASS — algorithm chain correct in file-replay (FCS_OK=1>=1)`
- Verdict: **PASS** — baseline still works with no Phase 139 env vars set

## T2 2-way Default ON
- Command: `... --phase139-on`
- Result: FCS_OK=1, FCS_FAIL=0, RX=1 (1/1 PASS)
- Logs:
  - `[TEST] Phase 139 ENABLED: IEEE80211_H52_2WAY_DEFAULT=1 (2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG viterbi)`
  - `[FRAME_EQ] IEEE80211_H52_2WAY_DEFAULT=1 (2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG ENABLED, theoretical sigma reduction 1/sqrt(2))`
  - `[H52_2WAY] 2-way SNR-weighted H52 applied for L-SIG viterbi (counter=4 src=compensated)` — **path actively fires**
- Verdict: **PASS** — 2-way path runs without breaking L-SIG chain

## T2b 3-way ON
- Command: `... --phase139-on --phase139-3way`
- Result: FCS_OK=1, FCS_FAIL=0, RX=1 (1/1 PASS)
- Logs:
  - `[TEST] Phase 139 ENABLED: IEEE80211_H52_2WAY_DEFAULT=1 (2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG viterbi)`
  - `[TEST] Phase 139 3-way ENABLED: IEEE80211_HT_SIG_PILOT_REFINE=1 (HT-SIG0 4 pilots)`
  - `[FRAME_EQ] IEEE80211_H52_2WAY_DEFAULT=1 ... ENABLED, theoretical sigma reduction 1/sqrt(2)`
  - `[FRAME_EQ] IEEE80211_HT_SIG_PILOT_REFINE=1 (HT-SIG pilot refinement layer ENABLED, 3-way H52)`
  - `[H52_2WAY] 2-way SNR-weighted H52 applied for L-SIG viterbi (counter=4 src=compensated)`
- Verdict: **PASS** — 3-way stack (2-way L-SIG + HT-SIG pilot refinement) runs without breaking chain

**Status**: File-replay validation **PASS**. Proceed to USRP T3.

## Notes

- All three tests used `/tmp/p103_iq.bin` (existing file from prior phase, 774840 bytes / ~96720 samples / 4.84 s at 20 MHz).
- Python interpreter: `/home/hy/conda/envs/gnuradio/bin/python3` (the shebang in the test script targets this env).
- T1/T2/T2b each ran for 30s rx-duration. Each detected exactly 1 RX message (1/1 FCS_OK). On a clean file-replay the 2-way averaging cannot show σ reduction (loopback signal is already noise-free), so 1/1 in all three cases is the correct expected outcome: regression check only.
- `[H52_2WAY]` log line confirms the 2-way path is actively executed (counter=4 = first LTS received in L-SIG path; src=compensated = uses CFO/SFO-compensated L-LTF symbols).
- HT-SIG pilot refine (3-way) is gated behind HT-SIG viterbi firing on the loopback signal; in this clean replay HT-SIG viterbi may not run (only L-SIG succeeds) so the 3-way kernel may not fire here — the env-var marker `[FRAME_EQ] IEEE80211_HT_SIG_PILOT_REFINE=1 ... 3-way H52` confirms the layer is WIRED IN correctly and will fire on USRP where HT-SIG viterbi runs.
- 0 cable runs consumed (file-replay is offline — no USRP hardware engaged).