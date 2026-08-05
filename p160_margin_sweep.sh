#!/usr/bin/env bash
# Phase 160: margin re-sweep on the delta-OFF baseline.
# 4 margins x 4 rounds, round-robin interleaved (drift cancellation).
# DS is now a direct proxy for detection rate (chain ~98% post P159b).
set -u
cd "$(dirname "$0")"
OUT=batch_results/p160_margin_sweep/$(date +%Y%m%d_%H%M%S)
mkdir -p "$OUT"
echo "[SWEEP] out=$OUT margins=1.0,1.5,2.0,2.5 rounds=4"
for round in 1 2 3 4; do
  for m in 1.0 1.5 2.0 2.5; do
    ok=0
    for attempt in 1 2 3; do
      IEEE80211_SYNC_SHORT_TRIGGER_MARGIN=$m ./usrp_realtime_validate.sh \
        > "$OUT/r${round}_m${m}.log" 2> "$OUT/r${round}_m${m}.err"
      rc=$?
      cp /tmp/rt_validate.err "$OUT/r${round}_m${m}.rt.err" 2>/dev/null
      if grep -qE "Failure to create rfnoc_graph|RfnocError|Management operation failed" "$OUT/r${round}_m${m}.err" "$OUT/r${round}_m${m}.log" 2>/dev/null && [ $rc -ne 0 ]; then
        echo "[SWEEP] r${round} m${m} attempt${attempt} UHD init fail; probe+retry"
        uhd_usrp_probe --args addr=192.168.10.2 >/dev/null 2>&1; sleep 5; continue
      fi
      ok=1; break
    done
    ds=$(grep -oP 'DECODE_SUCCESS \(ground truth\) = \K\d+' "$OUT/r${round}_m${m}.log" || echo 0)
    arr=$(grep -a "LSIG_DECODE" "$OUT/r${round}_m${m}.rt.err" 2>/dev/null | grep -ac "enc=0 len=72" || echo 0)
    echo "[SWEEP] round=${round} margin=${m} DS=${ds} arrival=${arr} attempts=${attempt}"
  done
done
echo "[SWEEP] DONE"
