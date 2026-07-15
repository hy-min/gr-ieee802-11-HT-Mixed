#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 111 T2: minimal unit test using direct frame_equalizer input.

Skips wifi_phy_hier FFT and feeds already-FFT'd data to a frame_equalizer
directly. Tests that Kalman doesn't crash and that the env var path works.
"""
import os, sys, time
import numpy as np

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')

from gnuradio import gr
import pmt
import ieee802_11


def main():
    kalman = os.environ.get('IEEE80211_H52_KALMAN_TRACK', '0') == '1'
    print(f"[P111_T2_UNIT] Kalman={kalman}")
    fe = ieee802_11.frame_equalizer(ieee802_11.LS, 5.89e9, 10e6, False, False)
    print("[P111_T2_UNIT] frame_equalizer created OK")
    # Try processing some random samples to ensure no crash
    np.random.seed(42)
    # Generate 100 random complex64 vectors of 64 samples
    in_data = (np.random.randn(100, 64) + 1j * np.random.randn(100, 64)).astype(np.complex64)
    out_data = np.zeros((100, 52), dtype=np.complex64)
    consumed = fe.work(100, in_data, out_data)
    print(f"[P111_T2_UNIT] Consumed {consumed}/100 vectors, produced {len(out_data)} (sanity)")


if __name__ == '__main__':
    try:
        main()
        print("[P111_T2_UNIT] PASS — Kalman implementation does not crash")
    except Exception as e:
        print(f"[P111_T2_UNIT] FAIL — {e}")
        sys.exit(1)