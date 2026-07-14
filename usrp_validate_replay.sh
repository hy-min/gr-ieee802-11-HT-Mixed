#!/usr/bin/env bash
# Phase 145c: Standardized USRP validation via capture + file replay.
#
# Usage:
#   ./usrp_validate_replay.sh [capture_seconds] [freq_mhz] [tx_gain]
#
# Example:
#   ./usrp_validate_replay.sh 30 5250 0
#
# This script:
#   1. Captures USRP IQ using capture_usrp_txrx.py (TX enabled, no wifi_phy_rx
#      in capture flowgraph — avoids realtime RX chain blocking USRP source)
#   2. Replays it through examples/test_file_replay_e2e.py with the
#      Phase 145c winning configuration
#   3. Reports FCS_OK / FCS_FAIL and frame-level decode quality

set -euo pipefail

DURATION="${1:-30}"
FREQ="${2:-5250}"
TX_GAIN="${3:-0}"
CAPTURE="/tmp/usrp_validate_$(date +%Y%m%d_%H%M%S).fc32"

echo "=== Phase 145c USRP Validation ==="
echo "Duration: ${DURATION}s"
echo "Freq: ${FREQ} MHz"
echo "TX gain: ${TX_GAIN} dB"
echo "Capture: ${CAPTURE}"
echo ""

# Step 1: Capture (no wifi_phy_rx in flowgraph)
echo "[1/2] Capturing USRP IQ (TX + RX capture, no RX decode chain) ..."
/home/hy/conda/envs/gnuradio/bin/python3 capture_usrp_txrx.py \
    --freq "${FREQ}" --tx-gain "${TX_GAIN}" --rate 20 \
    --rx-gain 31.5 --rx-subdev A:0 --rx-scale 40.0 \
    --interval 100 --len 38 \
    --duration "${DURATION}" --capture "${CAPTURE}"

if [ ! -f "${CAPTURE}" ]; then
    echo "ERROR: Capture file not created: ${CAPTURE}"
    exit 1
fi

SIZE=$(stat -c%s "${CAPTURE}")
NSAMP=$((SIZE / 8))
DUR_S=$(echo "scale=3; ${NSAMP} / 20000000" | bc)
echo "Capture size: ${SIZE} bytes, ${NSAMP} samples, ${DUR_S}s @ 20MHz"

if [ "${NSAMP}" -lt 100000 ]; then
    echo "WARNING: Capture is very short (${DUR_S}s). Realtime streaming may be unstable."
fi

echo ""

# Step 2: Replay
echo "[2/2] Replaying through RX chain ..."
/home/hy/conda/envs/gnuradio/bin/python3 examples/test_file_replay_e2e.py \
    --iq-file "${CAPTURE}" \
    --rx-duration 30 \
    --loop 1 \
    --phase rx

echo ""
echo "=== Validation complete ==="
echo "Capture saved to: ${CAPTURE}"
