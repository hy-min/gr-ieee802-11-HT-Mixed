#!/usr/bin/env python
"""
Phase 30 final verdict: generate JSON from analysis results.
"""
import json

verdict = {
    'phase': 'Phase 30: Per-SC SNR detection and null-subcarrier rejection',
    'date': '2026-06-17',
    'hypothesis': (
        "90% pathological frames (avg_snr_lsig >> 10) are due to one or more "
        "subcarriers having |H|² near 0 (null). When that SC is divided, the "
        "equalized symbol becomes huge (|eq|² >> 1), blowing up the average. "
        "If we identify and reject frames with weak subcarriers, OR drop the "
        "bad subcarriers, we may get cleaner viterbi decode."
    ),

    'finding': 'REFUTED with REFINED diagnosis',

    'reason': (
        "1. Inspection of frame_equalizer_impl.cc:3594-3612 shows the equalizer "
        "ALREADY excludes null SCs (|H| < 0.001) from avg_snr_lsig computation "
        "via the cnt guard. safe_div (line 96-103) returns 0 for |H|² < 1e-12. "
        "Null SCs cannot directly cause pathological avg_snr_lsig. "
        "2. Reproduction test: injecting null at SC 11 in software loopback "
        "produced avg_snr_lsig=3303.47 (matching USRP values of 2317, 3031). "
        "BUT the resulting H52 estimate was globally corrupted: "
        "  - 36/52 SCs had |H| < 0.5 (instead of 8.875 baseline) "
        "  - std|H| = 7.084 (vs 0.0 baseline) "
        "  - mean|H| = 3.801 (vs 8.875 baseline) "
        "The null at one SC caused a CASCADING H52 estimation failure, NOT a "
        "single-SC pathology."
    ),

    'avg_snr_lsig_distribution_from_logs': {
        'p29_3_e2e.log': {
            'n_samples': 16,
            'min': 4.28, 'max': 8.34,
            'comment': '8x repeated, 2 unique values (4.28, 8.34) — in range [0.1, 10]'
        },
        'p29_e2e_full.log': {
            'n_samples': 24,
            'min': 31.24, 'max': 3031.18,
            'comment': '8x each of {31.24, 2317.58, 3031.18} — bimodal: clean (31) vs pathological (2k+)'
        },
        'bimodal_observation': (
            'avg_snr_lsig is BIMODAL: clean frames cluster around 1-30, '
            'pathological frames cluster around 2k-3k. This pattern is '
            'consistent with H52 estimation total failure on some frames.'
        ),
    },

    'reproduction_test': {
        'method': (
            'Software loopback with controlled null injection: zero FFT bin 11 '
            '(data subcarrier) in 64-sample blocks of the TX-generated frame.'
        ),
        'baseline_no_null': {
            'mean_H': 8.875, 'std_H': 0.0,
            'avg_snr_lsig_clean': 1.00, 'avg_snr_htsig_clean': 1.00,
            'FCS_OK': 1
        },
        'null_at_SC11': {
            'mean_H': 3.801, 'std_H': 7.084,
            'min_H': 0.042, 'max_H': 18.600,
            'n_scs_below_0.5': 36, 'n_scs_total': 52,
            'avg_snr_lsig_pathological': 3303.47,
            'avg_snr_htsig_pathological': 3600.45,
            'FCS_OK': 0,
            'LSIG_PARSE_FAIL': 'viterbi_fail'
        },
    },

    'synthetic_drop_experiment': {
        'method': (
            'Simulated 1000 frames with 5% null-SC injection, tested drop '
            'thresholds in {0.0, 0.001, 0.01, 0.1, 1.0, 10.0}.'
        ),
        'drop_0.001': '99.1% in range [0.1, 10] (matches equalizer default)',
        'drop_0.01':  '100% in range, mean=1.10, std=0.41',
        'drop_0.1':   '100% in range, mean=1.05, std=0.05 — TIGHT',
        'drop_1.0':   '100% in range (51% of frames keep any SCs), mean=1.02',
        'conclusion': (
            'If null SCs were the ONLY cause of pathology, drop threshold '
            '0.1 would bring 100% of frames in range. But in real USRP data, '
            'the pathology is upstream — it is H52 estimation failure, not '
            'a single null SC.'
        ),
    },

    'synthetic_stress_test': {
        'method': 'Simulated 500 frames with 0-100% null-SC rate.',
        'null_rate_0%':   'mean=1.05, max=1.19, 100% in range',
        'null_rate_10%':  'mean=50.2, max=1161, 23.8% in range',
        'null_rate_30%':  'mean=149, max=1740, 0% in range',
        'null_rate_50%':  'mean=234, max=2329, 0% in range',
        'null_rate_100%': 'mean=481, max=1963, 0% in range',
        'conclusion': (
            'When even 30% of SCs are nulled, avg_snr_lsig goes pathological '
            '(mean=149, max=1740). This matches the USRP bimodal pattern '
            'where frames are either clean (~1) or catastrophic (2k+).'
        ),
    },

    'root_cause': (
        "The USRP capture has frames where MANY subcarriers (not just one) "
        "are simultaneously corrupted in the H estimate. This causes: "
        "  (a) |H| varies wildly (std=7 vs 0 baseline) "
        "  (b) per-SC equalization produces scattered |eq|² values "
        "  (c) avg_snr_lsig blows up to 2k-3k "
        "  (d) viterbi decoder fails on the L-SIG (BPSK symbols are scattered) "
        "The 'drop null SCs' strategy is irrelevant because: "
        "  - null SCs are already dropped by safe_div "
        "  - the pathology is in non-null SCs that have severely wrong H estimates"
    ),

    'recommendation': (
        "PIVOT: Do NOT add per-SC drop logic. The pathology is in H52 ESTIMATION "
        "QUALITY, not in per-SC equalization. Next investigation should: "
        "  (1) Examine L-LTF0/L-LTF1 sample extraction — is the FFT window "
        "      landing at correct sample offset for the pathological frames? "
        "  (2) Check H52 pre/post median filter effectiveness on USRP captures "
        "  (3) Investigate sync_short frame_start detection — is it 1-2 samples "
        "      off in the pathological frames, causing L-LTF0 to land mid-symbol? "
        "Per-SC SNR detection is INSUFFICIENT for the H estimation failure mode."
    ),

    'verdict_save_path': '/tmp/p30_per_sc_verdict.json',
}

with open('/tmp/p30_per_sc_verdict.json', 'w') as f:
    json.dump(verdict, f, indent=2)

print("Verdict saved to /tmp/p30_per_sc_verdict.json")
print()
print(json.dumps(verdict, indent=2))
