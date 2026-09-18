#!/usr/bin/env python3
"""
p193_psdu_analyze.py — locate the FIRST wrong byte in a failing frame's PSDU.

Why this and not another statistic
----------------------------------
P192's probes (symbol energy, BPSK phase, across-frame variance) all came back
flat and could NOT separate len200 (62% decode) from len400 (0%), even though
the same arms differ hugely in outcome.  What DID separate them:

    MAC header bytes 0-15 correct   98.6% (L200)  vs  88.5% (L400)
    payload tail correct            98.0% (L200)  vs   0.0% (L400)

So the failure is confined to the payload region.  The transmitter's payload is
a KNOWN CONSTANT (`'x' * len` -> 0x78 repeated; the harness builds a fixed
24-byte MAC header in front of it and appends a 4-byte FCS), so with the whole
PSDU dumped we can say, per frame, exactly where the bytes stop matching --
which is a far sharper localiser than any aggregate.

Usage: python3 p193_psdu_analyze.py <arm.out|arm.err> [...]
"""
import re
import sys
from collections import Counter

PSDU_RE = re.compile(r'^\[FAIL_PSDU\] len=(\d+) head=([0-9a-f]+)\s*$')

HDR_LEN = 24          # 802.11 MAC header the harness emits (FC..seq)
FCS_LEN = 4
PAYLOAD_BYTE = 0x78   # 'x'


def first_wrong(psdu):
    """Index of the first payload byte that is not 0x78, or None."""
    for i in range(HDR_LEN, len(psdu) - FCS_LEN):
        if psdu[i] != PAYLOAD_BYTE:
            return i
    return None


def main():
    for path in sys.argv[1:]:
        frames = []
        with open(path, errors='replace') as fh:
            for line in fh:
                m = PSDU_RE.match(line.strip())
                if not m:
                    continue
                n = int(m.group(1))
                h = m.group(2)
                if len(h) < 2 * n:        # truncated by our own buffer guard
                    continue
                frames.append(bytes.fromhex(h[:2 * n]))
        if not frames:
            print(f'{path}: 没有 [FAIL_PSDU] 行')
            continue

        lens = Counter(len(f) for f in frames)
        print(f'=== {path} ===')
        print(f'  帧数={len(frames)}  长度分布={dict(lens)}')

        fw = Counter()
        n_clean = 0
        for f in frames:
            i = first_wrong(f)
            if i is None:
                n_clean += 1
            else:
                fw[i] += 1
        print(f'  payload 全程正确的帧: {n_clean} / {len(frames)}')
        if fw:
            print('  首个错字节的位置分布:')
            for k, c in sorted(fw.items())[:20]:
                print(f'    byte[{k:<4}] {c} 帧')
            if len(fw) > 20:
                print(f'    …共 {len(fw)} 个不同位置')

            # how badly wrong is the region past the first error?
            k0 = min(fw)
            f0 = next(f for f in frames
                      if first_wrong(f) == k0)
            tail = f0[k0:k0 + 24]
            print(f'  从 byte[{k0}] 起的 24 字节样例: '
                  f'{tail.hex()}')
        # header correctness, as a sanity channel
        hdr_ok = sum(1 for f in frames
                     if f[:16] == frames[0][:16])
        print(f'  前 16 字节与首帧相同: {hdr_ok} / {len(frames)}')


if __name__ == '__main__':
    main()
