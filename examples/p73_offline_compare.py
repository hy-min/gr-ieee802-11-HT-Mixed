#!/usr/bin/env python3
"""Phase 73 多帧离线比较工具。
比较多个 log 文件的 LSIG_OK/HT_SIG_CAND/n_nulls/avg_snr 指标。

Usage: python examples/p73_offline_compare.py log1.log log2.log log3.log
"""
import sys
import re
import numpy as np


def analyze_log(path):
    """返回 {lsig_ok, lsig_fail, is_ht, htsig_cand, htsig_parse_fail, n_nulls_after, avg_snr_lsig_list}"""
    result = {
        'lsig_ok': 0,
        'lsig_fail': 0,
        'is_ht': 0,
        'htsig_cand': 0,
        'htsig_parse_fail': 0,
        'n_nulls_after': [],
        'avg_snr_lsig_list': [],
        'avg_snr_htsig_list': [],
    }
    with open(path) as f:
        for line in f:
            if '[LSIG_DECODE OK]' in line:
                result['lsig_ok'] += 1
                m = re.search(r'avg_snr=([\d.]+)', line)
                if m:
                    result['avg_snr_lsig_list'].append(float(m.group(1)))
                continue
            if 'LSIG_PARSE_FAIL' in line:
                result['lsig_fail'] += 1
                m = re.search(r'avg_snr=([\d.]+)', line)
                if m:
                    result['avg_snr_lsig_list'].append(float(m.group(1)))
                continue
            if 'is_ht_frame=1' in line:
                result['is_ht'] += 1
                continue
            if 'HT_SIG_CAND' in line and 'HT_SIG_CANDIDATE_WIN' not in line:
                result['htsig_cand'] += 1
                continue
            if 'HT_SIG_PARSE_FAIL' in line:
                result['htsig_parse_fail'] += 1
                m = re.search(r'avg_snr_htsig=([\d.]+)', line)
                if m:
                    result['avg_snr_htsig_list'].append(float(m.group(1)))
                continue
            m = re.search(r'\[H60_NULL_PER_FRAME\].*?n_nulls=(\d+)/(\d+)', line)
            if m:
                result['n_nulls_after'].append(int(m.group(1)))
                continue
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python p73_offline_compare.py log1.log log2.log ...", file=sys.stderr)
        sys.exit(1)

    paths = sys.argv[1:]
    header = (
        f"{'log_file':<40} {'LSIG_OK':<8} {'LSIG_FAIL':<9} "
        f"{'HT_CAND':<8} {'HT_FAIL':<8} "
        f"{'n_nulls_med':<11} {'snr_lsig_med':<13} {'snr_ht_med':<11}"
    )
    print(header, flush=True)
    print("-" * len(header), flush=True)

    summaries = []
    for path in paths:
        r = analyze_log(path)
        n_nulls_med = np.median(r['n_nulls_after']) if r['n_nulls_after'] else float('nan')
        n_nulls_str = f"{n_nulls_med:.1f}" if not np.isnan(n_nulls_med) else "N/A"

        snr_lsig_med = np.median(r['avg_snr_lsig_list']) if r['avg_snr_lsig_list'] else float('nan')
        snr_lsig_str = f"{snr_lsig_med:.2f}" if not np.isnan(snr_lsig_med) else "N/A"

        snr_ht_med = np.median(r['avg_snr_htsig_list']) if r['avg_snr_htsig_list'] else float('nan')
        snr_ht_str = f"{snr_ht_med:.2f}" if not np.isnan(snr_ht_med) else "N/A"

        # Shorten path for table
        short = path.replace('/tmp/p73_', '').replace('.log', '')
        print(
            f"{short:<40} "
            f"{r['lsig_ok']:<8} {r['lsig_fail']:<9} "
            f"{r['htsig_cand']:<8} {r['htsig_parse_fail']:<8} "
            f"{n_nulls_str:<11} {snr_lsig_str:<13} {snr_ht_str:<11}",
            flush=True,
        )
        summaries.append((path, r))

    print("\n=== 决策建议 ===", flush=True)
    if summaries:
        # 最高 LSIG_OK
        best_lsig = max(summaries, key=lambda x: x[1]['lsig_ok'])
        print(
            f"最高 LSIG_OK: {best_lsig[0]} ({best_lsig[1]['lsig_ok']} 次)",
            flush=True,
        )
        # 最低 n_nulls
        best_nulls = min(
            summaries,
            key=lambda x: np.median(x[1]['n_nulls_after']) if x[1]['n_nulls_after'] else float('inf'),
        )
        n_med = np.median(best_nulls[1]['n_nulls_after'])
        print(
            f"最低 n_nulls median: {best_nulls[0]} ({n_med:.1f})",
            flush=True,
        )
        # 最高 HT_SIG_CAND
        best_ht = max(summaries, key=lambda x: x[1]['htsig_cand'])
        print(
            f"最高 HT_SIG_CAND: {best_ht[0]} ({best_ht[1]['htsig_cand']} 次)",
            flush=True,
        )


if __name__ == "__main__":
    main()