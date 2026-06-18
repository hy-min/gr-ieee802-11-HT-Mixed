#!/home/hy/conda/envs/gnuradio/bin/python
"""
Offline H52 estimation from raw USRP IQ capture.

Mirrors what frame_equalizer_impl.cc does at H52 compute site:
  1. Find frame start via sync_short auto-correlation (16-sample delayed conjugate product)
  2. Locate L-LTF0 at frame_start + 160 (data at +176, skip CP)
  3. Locate L-LTF1 at LTS0 + 80
  4. FFT(64) both LTSs, pick active 52 SCs
  5. H52 = (F0a + F1a) / 2

Emits [H52_DUMP] lines compatible with examples/test_h52_compare.py.

Usage:
    python examples/analyze_h52_offline.py /tmp/p32_raw.bin > /tmp/p32_h52_offline.log
"""
import sys
import numpy as np

# 802.11n HT-Mixed 20 MHz: 64 FFT, 52 active subcarriers
# SC index mapping (per IEEE 802.11-2016 17.3.5.3 / 18.3.5.3)
ACTIVE_SC = list(range(-26, 0)) + list(range(1, 27))  # 52 SCs, [-26..-1, 1..26]
# In FFT order (k = 0..63): negative freqs are at k=64-26..64-1=38..63, positive at 1..26
ACTIVE_SC_FFT_ORDER = [k % 64 for k in ACTIVE_SC]
assert len(ACTIVE_SC_FFT_ORDER) == 52


def read_fc32(path):
    return np.fromfile(path, dtype=np.complex64)


def find_frame_starts(samples, sample_rate=20e6, threshold=0.5):
    """Locate frame starts via sync_short 16-sample delayed correlation.

    Mirrors analyze_raw_iq.py:35-50. Returns list of sample indices (frame_start).
    """
    delayed = samples[16:]
    conj_prod = samples[:-16] * np.conj(delayed)
    # 48-sample moving average (sync_short window)
    kernel = np.ones(48) / 48.0
    ma_corr = np.abs(np.convolve(conj_prod, kernel, mode='valid'))
    ma_pwr_a = np.convolve(np.abs(samples[:-16])**2, kernel, mode='valid')
    ma_pwr_b = np.convolve(np.abs(delayed)**2, kernel, mode='valid')
    norm_corr = ma_corr / (np.sqrt(ma_pwr_a * ma_pwr_b) + 1e-12)

    # Find peaks above threshold with min distance
    starts = []
    i = 0
    while i < len(norm_corr):
        if norm_corr[i] > threshold:
            starts.append(i)
            i += 200  # skip ahead (a frame is ~ several thousand samples)
        else:
            i += 1
    return starts, norm_corr


def estimate_h52_frame(samples, frame_start):
    """Compute H52 from one frame. Returns H52 complex array of 52 SCs.

    Mirrors p28_3b_diagnose.py:65-99.
    """
    # L-LTF structure: GI2(16) + LTS0(64) + LTS1(64) (no GI between LTS0 and LTS1)
    # L-LTF0 = GI2 + LTS0, L-LTF1 = LTS1
    # After frame_start, the L-LTF0 GI2 starts at frame_start + 160 - 16 = frame_start + 144
    # LTS0 data at frame_start + 160 (skip GI2)
    lts0_data_start = frame_start + 160
    lts1_data_start = lts0_data_start + 80  # LTS1 immediately after LTS0 (no GI)

    if lts1_data_start + 64 > len(samples):
        return None

    lts0 = samples[lts0_data_start:lts0_data_start + 64]
    lts1 = samples[lts1_data_start:lts1_data_start + 64]

    F0 = np.fft.fft(lts0, 64)
    F1 = np.fft.fft(lts1, 64)

    F0a = F0[ACTIVE_SC_FFT_ORDER]
    F1a = F1[ACTIVE_SC_FFT_ORDER]

    H52 = (F0a + F1a) / 2.0
    return H52


def emit_dump(counter, H52):
    """Emit [H52_DUMP] line compatible with test_h52_compare.py parser."""
    mag = np.abs(H52)
    arg = np.angle(H52)
    mag_str = ",".join(f"{x:.4f}" for x in mag)
    arg_str = ",".join(f"{x:.4f}" for x in arg)
    mean_mag = float(np.mean(mag))
    std_mag = float(np.std(mag))
    mean_arg = float(np.mean(arg))
    std_arg = float(np.std(arg))
    print(f"[H52_DUMP] counter={counter} |H|={mag_str} arg(H)={arg_str} "
          f"mean|H|={mean_mag:.4f} std|H|={std_mag:.4f} "
          f"mean(argH)={mean_arg:.4f} std(argH)={std_arg:.4f}")


def main():
    if len(sys.argv) < 2:
        print("Usage: analyze_h52_offline.py <raw_iq.bin>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    samples = read_fc32(path)
    print(f"[OFFLINE] Loaded {len(samples)} samples from {path}", file=sys.stderr)

    starts, _ = find_frame_starts(samples, threshold=0.5)
    print(f"[OFFLINE] Found {len(starts)} frame starts (sync_short corr > 0.5)", file=sys.stderr)

    counter = 0
    for fs in starts:
        H52 = estimate_h52_frame(samples, fs)
        if H52 is not None:
            emit_dump(counter, H52)
            counter += 1

    print(f"[OFFLINE] Emitted {counter} [H52_DUMP] lines", file=sys.stderr)


if __name__ == "__main__":
    main()
