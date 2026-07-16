#!/usr/bin/env bash
# usrp_realtime_validate.sh — ONE-COMMAND realtime USRP FCS_OK validation + regression gate.
#
# Solidified working path (Phase 150): RX-only decode chain (no idle-TX scheduler stall),
# 145c winning decoder config, underflow fix (governor=performance + 2.4MB UHD buffers,
# persisted via /etc/sysctl.d/99-gr-ieee80211-uhd.conf + systemd gr-cpu-performance.service).
#
# Usage:
#   ./usrp_realtime_validate.sh [--threshold N] [--windows K] [--run SECS]
# Exit 0 = PASS (FCS_OK ground-truth >= threshold), 1 = FAIL (path regressed).
set -u
cd "$(dirname "$0")"

THRESHOLD=15          # ground-truth DECODE_SUCCESS across all windows (PASS floor)
WINDOWS=3             # measurement windows
RUN=15                # seconds per window (3x15 = 45s total, matches Phase 147 baseline)
FREQ=5250             # antenna (air path), quietest 5 GHz band
TXGAIN=0              # Phase 147 config (rx_gain 31.5 gives ~26% ADC, no clipping)
RXGAIN=31.5
RXSCALE=40
INTERVAL=100          # 100 ms -> ~10 frames/s strobe

while [ $# -gt 0 ]; do
  case "$1" in
    --threshold) THRESHOLD="$2"; shift 2;;
    --windows)   WINDOWS="$2"; shift 2;;
    --run)       RUN="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

SCALES=$(python3 -c "print(','.join(['$RXSCALE']*$WINDOWS))")
EST_SENT=$(( WINDOWS * RUN * 1000 / INTERVAL ))
OUT=/tmp/rt_validate.out
ERR=/tmp/rt_validate.err

echo "==================================================================="
echo "[RTV] realtime USRP FCS_OK validation"
echo "[RTV] config: freq=$FREQ tx-gain=$TXGAIN rx-gain=$RXGAIN rx-scale=$RXSCALE interval=${INTERVAL}ms"
echo "[RTV] windows=$WINDOWS x ${RUN}s (est_sent~$EST_SENT frames)  threshold(DECODE_SUCCESS)>=$THRESHOLD"
echo "[RTV] system: governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) wmem_max=$(sysctl -n net.core.wmem_max 2>/dev/null)"
if [ "$(sysctl -n net.core.wmem_max 2>/dev/null)" != "2453333" ]; then
  echo "[RTV] WARN: wmem_max != 2453333 (underflow fix not active; run sudo sysctl --system)" >&2
fi
if [ "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)" != "performance" ]; then
  echo "[RTV] WARN: governor != performance (run: sudo systemctl start gr-cpu-performance.service)" >&2
fi
echo "==================================================================="

unset LD_LIBRARY_PATH
LD_PRELOAD=./wrap_rpc2.so PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python test_usrp_rxonly_instrumented.py \
    --freq "$FREQ" --tx-gain "$TXGAIN" --rx-gain "$RXGAIN" --rx-scale "$RXSCALE" \
    --interval "$INTERVAL" --warmup 20 --run "$RUN" --scales "$SCALES" \
    >"$OUT" 2>"$ERR"
rc=$?

PDU_OK=$(grep -ac "FCS_OK" "$OUT" 2>/dev/null | head -1; )
GT_OK=$(grep -ac "DECODE_SUCCESS" "$ERR" 2>/dev/null)
GT_FAIL=$(grep -ac "LDPC FCS error" "$ERR" 2>/dev/null)
UF=$(grep -ac "underflow" "$ERR" 2>/dev/null)
OF=$(grep -ac " overflow" "$ERR" 2>/dev/null)
PDU_SUM=$(awk '/total PDU FCS_OK/{print $NF}' "$OUT" 2>/dev/null)

echo "==================================================================="
echo "[RTV] per-window PDU results:"
grep -a "RESULT" "$OUT" 2>/dev/null | sed 's/^/   /'
echo "-------------------------------------------------------------------"
echo "[RTV] FCS_OK (PDU msg-queue)      = ${PDU_SUM:-?}   (undercounts; see ground truth)"
echo "[RTV] DECODE_SUCCESS (ground truth) = $GT_OK   <- regression metric"
echo "[RTV] DECODE_FAIL (LDPC terminal)   = $GT_FAIL"
echo "[RTV] arrival (est)  = $GT_OK / ~$EST_SENT = $(python3 -c "print(f'{$GT_OK/$EST_SENT*100:.1f}%')")"
echo "[RTV] TX underflow = $UF   RX overflow = $OF   (both should be ~0)"
echo "==================================================================="

if [ "$rc" -ne 0 ]; then
  echo "[RTV] FAIL: harness exited rc=$rc (crash/hang?)"; exit 1
fi
if [ "$GT_OK" -ge "$THRESHOLD" ]; then
  echo "[RTV] PASS: DECODE_SUCCESS=$GT_OK >= $THRESHOLD  (realtime path WORKS)"
  exit 0
else
  echo "[RTV] FAIL: DECODE_SUCCESS=$GT_OK < $THRESHOLD  (path REGRESSED)"
  exit 1
fi
