"""Phase 84 replay metric log parser.

Parses per-frame `avg_snr_lsig` and decoded `rate` field from C++
frame_equalizer USRP_LOG output. Pure-stdlib regex parser; no GNU Radio.

Usage:
    # As a script
    python examples/p84_replay_metric_log.py /path/to/usrp_replay.log

    # As a module
    from p84_replay_metric_log import parse_replay_log
    snrs, rates, n = parse_replay_log('/path/to/usrp_replay.log')
"""
import re
import sys


_RE_SNR = re.compile(r'\[FRAME_EQ\][^\n]*avg_snr_lsig=(-?[0-9.]+)')
_RE_RATE = re.compile(r'\[FRAME_EQ\][^\n]*rate=0x([0-9A-Fa-f]+)')


def parse_replay_log(log_path):
    """Parse a C++ USRP_LOG replay log and extract per-frame SNR + rate.

    Parameters
    ----------
    log_path : str
        Path to log file produced by `p68_replay_offline.py` with
        `IEEE80211_HT_STRUCT_AUDIT=1` (or similar) enabled.

    Returns
    -------
    snrs : list of float
        Per-frame avg_snr_lsig values (dB).
    rates : list of int
        Per-frame decoded L-SIG rate (0x9, 0xD, etc.).
    n : int
        Min(len(snrs), len(rates)) — number of frames with both values.
    """
    snrs, rates = [], []
    with open(log_path) as f:
        for line in f:
            m_snr = _RE_SNR.search(line)
            if m_snr:
                try:
                    snrs.append(float(m_snr.group(1)))
                    continue
                except ValueError:
                    pass
            m_rate = _RE_RATE.search(line)
            if m_rate:
                try:
                    rates.append(int(m_rate.group(1), 16))
                except ValueError:
                    pass
    return snrs, rates, min(len(snrs), len(rates))


def test_parse_replay_log_extracts_snrs_and_rates():
    import tempfile
    import os
    sample = (
        "[FRAME_EQ] frame=1 sym_idx=0 avg_snr_lsig=7.11 is_ht=true\n"
        "[FRAME_EQ] frame=1 L-SIG rate=0x9 metric=4\n"
        "[FRAME_EQ] frame=2 sym_idx=0 avg_snr_lsig=6.5 is_ht=true\n"
        "[FRAME_EQ] frame=2 L-SIG rate=0xD metric=3\n"
        "Garbage line should be ignored\n"
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
        f.write(sample)
        path = f.name
    try:
        snrs, rates, n = parse_replay_log(path)
        assert n == 2, f"expected 2 frames, got {n}"
        assert snrs == [7.11, 6.5], f"snrs={snrs}"
        assert rates == [9, 13], f"rates={rates}"
        print(f"OK: parsed snrs={snrs} rates={rates}")
    finally:
        os.unlink(path)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        snrs, rates, n = parse_replay_log(sys.argv[1])
        if n == 0:
            print(f"frames=0  (no [FRAME_EQ] lines found in {sys.argv[1]})")
        else:
            mean_snr = sum(snrs) / len(snrs) if snrs else 0.0
            rate_dist = {}
            for r in rates:
                rate_dist[f"0x{r:X}"] = rate_dist.get(f"0x{r:X}", 0) + 1
            print(f"frames={n}  mean_snr={mean_snr:.2f} dB  rate_dist={rate_dist}")
    else:
        test_parse_replay_log_extracts_snrs_and_rates()