#!/usr/bin/env python3
"""
p190_validity_audit.py — retroactive validity audit of every SIR-sweep arm.

Motivation (Phase 190)
----------------------
Every instrument added during P188/P189 invalidated arms that the PREVIOUS
instrument set had already accepted:

    P188 analysis   (no gates)          -> all accepted
    P189 palindrome (per-point repro)   -> A=31.5 -60.4 pp refuted
    b_life / b_died                     -> 2 x A=18 "no effect" refuted, +2 arms
    [CAPTURE] count                     -> P188 idx00 flagged

That is the signature of an INCOMPLETE instrument, not of occasional bad luck:
~14% of arms proved invalid, and the instrument that condemned them did not
exist when they were accepted.  So before running anything new, apply every
gate that CAN be computed from surviving data to every arm at once, and
re-derive the curve from survivors only.

Gates computable from surviving data
------------------------------------
  infra_ok        the victim produced a full set of windows
  attempts        retries are allowed, but the arm must still be complete
  intf_died       "WARNING: interferer died mid-point" in the batch console log
  capture         [decode_mac][CAPTURE] line count in the victim stdout

`b_life` is NOT recomputable: the interferer logs were deleted during the
ENOSPC cleanup, before the root-cause investigation.  That deletion is itself
recorded as a process failure in the P189 verdict.

The capture gate
----------------
Calibrated on arms whose interferer liveness is independently known:

    silent arms        12,980 -  20,636   (victim's own frames only)
    normal  max arms  890,342 - 1,033,315
    P188 idx00  max      318,205   <- anomalous, and the arm proved by b_life
    P189 idx02  max       49,165   <- to be interferer-starved (B up 55 s of 125 s)

5x separation, no overlap.  A max arm below --min-capture is flagged.
"""
import argparse
import re
import subprocess
from pathlib import Path

CAP_ROOT = Path('/home/hy/captures')
REPO = Path('/home/hy/gr-ieee802-11')

RESULT_RE = re.compile(
    r'\[RESULT\]\s+scale=\S+\s+rx_gain=\S+\s+cap=\S+\s+window=\S+s\s+'
    r'est_sent~(\d+)\s+PDU=(\d+)\s+FCS_OK=(\d+)\s+FCS_FAIL=(\d+)')
ARMNAME_RE = re.compile(r'idx(\d+)_a([\d.]+)_(silent|max)_b(on|off)_attempt(\d+)')

BATCHES = [
    ('P188', 'p180_sir_sweep_20260917_180144_sirthr', 'p188_sirthr.log'),
    ('P189', 'p180_sir_sweep_20260917_182710_sirpal', 'p189_sirpal.log'),
    ('P189b', 'p180_sir_sweep_20260918_101404_sirpal_fill', 'p189b_fill.log'),
    ('P189c', 'p180_sir_sweep_20260918_102949_sirpal_fix', 'p189c_fix.log'),
    ('P189d', 'p180_sir_sweep_20260918_111232_sirpal_315', 'p189d_315.log'),
    ('P191', 'p180_sir_sweep_20260918_114210_sirpal_disc', 'p191_disc.log'),
]


def is_collapse(wins):
    """Link starts healthy then dies mid-arm.

    The `wins[0] >= 80` guard is load-bearing: "uniformly dead" also trends to
    zero, and A=0's max arms ([1,0,0,0,0]) are the most reproducible
    measurement in the project -- a bare trend gate would throw them away.
    Calibrated over 67 arms: fires on 5, all A=31.5 collapses; the lowest
    healthy first-window drop is 0.63, so 0.4 leaves margin both ways.
    Kept in sync with the live gate in p180_sir_sweep.py.
    """
    return (len(wins) == 5 and wins[0] >= 80 and wins[-1] < 0.4 * wins[0])


def count_lines(path, pattern):
    """grep -c via subprocess: these .out files reach 130 MB."""
    try:
        r = subprocess.run(['grep', '-c', '-F', pattern, str(path)],
                           capture_output=True, text=True, timeout=300)
        return int((r.stdout or '0').strip() or 0)
    except Exception:
        return -1


def distinct_seq(path, cap=400000):
    """Number of distinct frame_seq values = frames the decoder was fed."""
    try:
        r = subprocess.run(['grep', '-o', '-E', r'frame_seq=[0-9]+', str(path)],
                           capture_output=True, text=True, timeout=300)
    except Exception:
        return -1
    if not r.stdout:
        return 0
    return len(set(r.stdout.split()))


def parse_console(logpath):
    """attempts / infra_ok / interferer-death warnings, per arm, from the log."""
    info = {'died_idx': set(), 'abort': False}
    if not logpath.exists():
        return info
    txt = logpath.read_text(errors='replace')
    info['abort'] = 'ABORT' in txt
    for m in re.finditer(r'WARNING: interferer died mid-point', txt):
        info['died_idx'].add('*')          # positional match below is unreliable
    # The console prints arms in order; the warning follows the arm it belongs to.
    seq = []
    cur = None
    for line in txt.splitlines():
        mm = re.match(r'\[SIR\] --- idx (\d+): A_gain=([\d.]+) \((\w+)\) '
                      r'arm=(\w+) ---', line)
        if mm:
            cur = (int(mm.group(1)), float(mm.group(2)), mm.group(3),
                   mm.group(4))
            continue
        if cur and 'interferer died mid-point' in line:
            info['died_idx'].add(cur)
            cur = None
    return info


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--min-capture', type=int, default=700000,
                   help='max arms below this [CAPTURE] count are INVALID. '
                        'CALIBRATED FOR THE len1000/10ms B CONFIG ONLY: normal '
                        '840k-1.03M, A-degraded 115k/318k/547k, B-absent '
                        '20k/49k.  The 2026-09-14 neargeo batch used another '
                        'config and ran 38k-47k on GOOD arms -- the count '
                        'scales with B frame length x rate.')
    p.add_argument('--max-silent-capture', type=int, default=60000,
                   help='silent arms above this are suspicious (normal 13k-21k)')
    a = p.parse_args()

    arms = []
    for tag, d, logname in BATCHES:
        ddir = CAP_ROOT / d
        if not ddir.exists():
            continue
        console = parse_console(CAP_ROOT / logname)
        for f in sorted(ddir.glob('idx*_a*_b*_attempt*.out')):
            m = ARMNAME_RE.match(f.stem)
            if not m:
                continue
            idx, gain, arm, bstate, attempt = (
                int(m.group(1)), float(m.group(2)), m.group(3),
                m.group(4), int(m.group(5)))
            if not f.stat().st_size:
                continue
            txt = f.read_text(errors='replace') if f.stat().st_size < 4e6 else ''
            results = RESULT_RE.findall(txt) if txt else []
            if not results:   # large files: stream only the RESULT lines
                r = subprocess.run(['grep', '-E', r'^\[RESULT\]', str(f)],
                                   capture_output=True, text=True, timeout=300)
                results = RESULT_RE.findall(r.stdout or '')
            wins = [int(x[2]) for x in results]
            ok = sum(wins)
            est = sum(int(x[0]) for x in results)
            cap = count_lines(f, 'decode_mac][CAPTURE]')
            nseq = distinct_seq(f) if (f.stat().st_size < 4e6) else -1
            died = (idx, gain,
                    'asc' if idx < 99 else '', arm) in console['died_idx']
            arms.append(dict(tag=tag, idx=idx, gain=gain, arm=arm,
                             attempt=attempt, ok=ok, est=est,
                             nwin=len(results), cap=cap, died=died,
                             wins=wins))
    print(f'{"批次":<7}{"idx":<5}{"A_gain":<8}{"臂":<8}{"窗":<4}'
          f'{"FCS_OK%":<9}{"CAPTURE":<11}{"判定"}')
    print('-' * 78)
    for r in sorted(arms, key=lambda x: (x['tag'], x['idx'], x['arm'])):
        valid, why = True, []
        if r['nwin'] < 5:
            valid, why = False, why + [f'窗{r["nwin"]}/5']
        if r['died']:
            valid, why = False, why + ['干扰机中途死亡']
        if r['arm'] == 'max' and r['cap'] >= 0 and r['cap'] < a.min_capture:
            valid, why = False, why + [f'CAPTURE {r["cap"]}']
        if r['arm'] == 'silent' and r['cap'] > a.max_silent_capture:
            valid, why = False, why + [f'CAPTURE {r["cap"]}']
        if is_collapse(r['wins']):
            valid, why = False, why + [f'臂内崩塌 {r["wins"]}']
        pct = r['ok'] * 100.0 / r['est'] if r['est'] else 0.0
        print(f'{r["tag"]:<7}{r["idx"]:<5}{r["gain"]:<8g}{r["arm"]:<8}'
              f'{r["nwin"]:<4}{pct:<9.1f}{r["cap"]:<11}'
              f'{"OK" if valid else "INVALID: " + ", ".join(why)}')

    # ---- curve from survivors only ----
    pairs = {}
    for r in arms:
        valid = (r['nwin'] >= 5 and not r['died'] and
                 not is_collapse(r['wins']) and
                 (r['cap'] >= 0 and
                  ((r['arm'] == 'max' and r['cap'] >= a.min_capture) or
                   (r['arm'] == 'silent' and r['cap'] <= a.max_silent_capture))))
        key = (r['tag'], r['idx'], r['gain'])
        pairs.setdefault(key, {})[r['arm']] = (r['ok'], r['est'], valid)

    print('\n========== 曲线（只含双臂均有效的点）==========')
    print(f'{"A_gain":<8}{"SIR(P187)":<12}{"差值":<10}来源')
    by_gain = {}
    for (tag, idx, gain), d in sorted(pairs.items(), key=lambda kv: kv[0][2]):
        s, m = d.get('silent'), d.get('max')
        if not (s and m and s[2] and m[2]):
            continue
        sr = s[0] * 100.0 / s[1]
        mr = m[0] * 100.0 / m[1]
        by_gain.setdefault(gain, []).append(mr - sr)
        print(f'{gain:<8g}{gain - 15.5:<+12.1f}{mr - sr:<+10.2f}'
              f'{tag}#{idx}')
    print('\n----- 每点汇总 -----')
    print(f'{"A_gain":<8}{"SIR":<10}{"n":<4}{"均值":<10}{"极差"}')
    for gain in sorted(by_gain, reverse=True):
        v = by_gain[gain]
        print(f'{gain:<8g}{gain - 15.5:<+10.1f}{len(v):<4}'
              f'{sum(v) / len(v):<+10.2f}{max(v) - min(v):.2f}')


if __name__ == '__main__':
    main()
