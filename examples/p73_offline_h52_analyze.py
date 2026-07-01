#!/usr/bin/env python3
"""
Phase 73 多帧 H52 离线分析器。
读取 p68_replay_offline.py 生成的日志，统计每帧的:
  - LSIG_DECODE OK 次数与 avg_snr 分布
  - LSIG_PARSE_FAIL 次数
  - is_ht_frame=1 出现次数
  - HT_SIG_CAND / HT_SIG_PARSE_FAIL 计数
  - H60_NULL pre-clean 后的 n_nulls 分布（每帧）
  - HTSIG_VITERBI_DIAG 详细帧

Usage:
  python examples/p73_offline_h52_analyze.py <log_path> [--threshold 0.15]
  python examples/p73_offline_h52_analyze.py /tmp/p73_combo_loop5.log --show-frame-detail
"""
import sys
import re
import argparse
import numpy as np


def parse_args():
    p = argparse.ArgumentParser(
        description='Phase 73 multi-frame H52 offline analyzer'
    )
    p.add_argument('log_path', help='Path to p68_replay_offline.py log file')
    p.add_argument('--threshold', type=float, default=0.15,
                   help='|H| threshold for null detection (informational only)')
    p.add_argument('--show-frame-detail', action='store_true',
                   help='Show first 5 HTSIG_VITERBI_DIAG detail lines')
    return p.parse_args()


def main():
    args = parse_args()

    print(f"[P73-ANALYZE] File: {args.log_path}", flush=True)
    print(f"[P73-ANALYZE] |H| threshold for null: {args.threshold} (informational)", flush=True)

    # Statistics accumulators
    lsig_ok_snr_values = []  # avg_snr per LSIG_DECODE OK
    lsig_fail_count = 0
    is_ht_frame_count = 0
    htsig_cand_count = 0
    htsig_parse_fail_count = 0
    htsig_viterbi_diag_lines = []  # full lines

    # H60_NULL pre-clean stats
    n_nulls_after_list = []
    n_nulls_before_list = []

    # Per-loop aggregation
    loop_rx_counts = []
    loop_done_count = 0

    with open(args.log_path) as f:
        for line in f:
            # LSIG_DECODE OK with avg_snr
            m = re.search(r'\[LSIG_DECODE OK\].*?avg_snr=([\d.]+)', line)
            if m:
                lsig_ok_snr_values.append(float(m.group(1)))
                continue

            # LSIG_PARSE_FAIL
            if 'LSIG_PARSE_FAIL' in line:
                lsig_fail_count += 1
                continue

            # is_ht_frame=1
            if re.search(r'is_ht_frame=1', line):
                is_ht_frame_count += 1
                continue

            # HT_SIG_CAND (but not HT_SIG_CANDIDATE_WIN)
            if 'HT_SIG_CAND' in line and 'HT_SIG_CANDIDATE_WIN' not in line:
                htsig_cand_count += 1
                continue

            # HT_SIG_PARSE_FAIL
            if 'HT_SIG_PARSE_FAIL' in line:
                htsig_parse_fail_count += 1
                continue

            # HTSIG_VITERBI_DIAG full line
            if 'HTSIG_VITERBI_DIAG' in line:
                htsig_viterbi_diag_lines.append(line.strip())
                continue

            # H60_NULL pre-clean stats
            # Format: [H60_NULL] ... n_nulls_before=X n_nulls_after=Y ...
            m = re.search(r'\[H60_NULL\].*?n_nulls_before=(\d+).*?n_nulls_after=(\d+)', line)
            if m:
                n_nulls_before_list.append(int(m.group(1)))
                n_nulls_after_list.append(int(m.group(2)))
                continue

            # Loop DONE line: "[REPLAY][loop1] ===== Loop 1 DONE: RX=0 ..."
            m = re.search(r'Loop\s+(\d+)\s+DONE', line)
            if m:
                loop_done_count += 1
                continue

    # Print summary
    print(f"\n[P73-ANALYZE] === LSIG / HT-SIG 汇总 ===", flush=True)
    print(f"Loop DONE 计数: {loop_done_count}", flush=True)
    print(f"LSIG_DECODE OK count: {len(lsig_ok_snr_values)}", flush=True)
    if lsig_ok_snr_values:
        arr = np.array(lsig_ok_snr_values)
        print(f"  avg_snr 分布:", flush=True)
        print(f"    mean={arr.mean():.2f}, std={arr.std():.2f}, "
              f"min={arr.min():.2f}, max={arr.max():.2f}, median={np.median(arr):.2f}", flush=True)
        print(f"  帧 avg_snr >= 6 dB 比例: {(arr >= 6.0).mean() * 100:.1f}%", flush=True)
        print(f"  帧 avg_snr >= 10 dB 比例: {(arr >= 10.0).mean() * 100:.1f}%", flush=True)
    print(f"LSIG_PARSE_FAIL count: {lsig_fail_count}", flush=True)
    print(f"is_ht_frame=1 count: {is_ht_frame_count}", flush=True)
    print(f"HT_SIG_CAND count: {htsig_cand_count}", flush=True)
    print(f"HT_SIG_PARSE_FAIL count: {htsig_parse_fail_count}", flush=True)
    print(f"HTSIG_VITERBI_DIAG summary lines: {len(htsig_viterbi_diag_lines)}", flush=True)

    # H60_NULL pre-clean stats
    print(f"\n[P73-ANALYZE] === H60_NULL Pre-Clean 统计 ===", flush=True)
    if n_nulls_after_list:
        before = np.array(n_nulls_before_list)
        after = np.array(n_nulls_after_list)
        print(f"H60_NULL fire count: {len(after)}", flush=True)
        print(f"  n_nulls_before: mean={before.mean():.2f}, median={np.median(before):.2f}, "
              f"min={before.min()}, max={before.max()}", flush=True)
        print(f"  n_nulls_after:  mean={after.mean():.2f}, median={np.median(after):.2f}, "
              f"min={after.min()}, max={after.max()}", flush=True)
        print(f"  帧 n_nulls_after <= 2 比例: {(after <= 2).mean() * 100:.1f}%", flush=True)
        print(f"  帧 n_nulls_after <= 4 比例: {(after <= 4).mean() * 100:.1f}%", flush=True)
        print(f"  帧 n_nulls_after <= 8 比例: {(after <= 8).mean() * 100:.1f}%", flush=True)
    else:
        print(f"未发现 H60_NULL 行（IEEE80211_H60_NULL_DUMP=1 未启用？）", flush=True)

    # Frame detail
    if args.show_frame_detail and htsig_viterbi_diag_lines:
        print(f"\n[P73-ANALYZE] === HTSIG_VITERBI_DIAG 前 5 帧 ===", flush=True)
        for line in htsig_viterbi_diag_lines[:5]:
            print(f"  {line}", flush=True)


if __name__ == "__main__":
    main()