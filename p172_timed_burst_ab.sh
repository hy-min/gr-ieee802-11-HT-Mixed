#!/usr/bin/env bash
# p172_timed_burst_ab.sh — H4: timed-burst TX interleaved OFF/ON/OFF/ON.
# Pre-registered primary endpoint: A-family tear rate + hole position per arm
# (hole scan). Secondary: DECODE_SUCCESS, late-time errors on ON arms.
set -u
cd /home/hy/gr-ieee802-11

run_once() {  # $1=tag $2=envval
  CAP=/home/hy/captures/p172_tb_$1.fc32
  OUT=/home/hy/captures/p172_tb_$1.out
  ERR=/home/hy/captures/p172_tb_$1.err
  unset LD_LIBRARY_PATH
  if [ "$2" = "1" ]; then export IEEE80211_TX_TIMED_BURST=1; else unset IEEE80211_TX_TIMED_BURST; fi
  LD_PRELOAD=./wrap_rpc2.so PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python test_usrp_rxonly_instrumented.py \
    --freq 5250 --tx-gain 0 --tx-scale 0.1 --rx-gain 31.5 --rx-scale 40 \
    --interval 100 --warmup 20 --run 30 --scales 40,40 \
    --capture "$CAP" >"$OUT" 2>"$ERR"
  return $?
}

run_arm() {  # $1=tag $2=envval
  free=$(df --output=avail -BG /home/hy | tail -1 | tr -dc 0-9); [ "$free" -lt 20 ] && { echo "[FAIL] disk ${free}G<20G"; exit 1; }
  echo "=== ARM $1 timed_burst=$2 ==="
  run_once "$1" "$2"; rc=$?
  if [ $rc -ne 0 ] && grep -aq "rfnoc_graph" /home/hy/captures/p172_tb_$1.err; then
    echo "[P152] init crash, nudge + retry once"
    timeout 40 uhd_usrp_probe --args addr=192.168.10.2 >/dev/null 2>&1
    sleep 3
    run_once "$1" "$2"; rc=$?
  fi
  ERR=/home/hy/captures/p172_tb_$1.err
  echo "arm $1 rc=$rc  marker: $(grep -ac 'P172' $ERR)  late/timeErr: $(grep -acE 'TimeError|late|LATE' $ERR)"
  echo "  DECODE_SUCCESS: $(grep -ac DECODE_SUCCESS $ERR)   LDPC FCS error: $(grep -ac 'LDPC FCS error' $ERR)"
  grep -a "RESULT" /home/hy/captures/p172_tb_$1.out | sed 's/^/  /'
}

run_arm g1 0
run_arm h1 1
run_arm g2 0
run_arm h2 1
echo "=== ALL ARMS DONE ==="
