#!/usr/bin/env bash
# p172_mtu_ab2.sh — H1 resume: ABBA-mirrored arms b1,b2 @9000 (current MTU), then a2 @1500.
set -u
cd /home/hy/gr-ieee802-11
PW='qjwzlss'
IF=enp4s0

restore() { echo "$PW" | sudo -S ip link set dev $IF mtu 1500 2>/dev/null; echo "[CLEANUP] MTU restored 1500"; }
trap restore EXIT

set_mtu() {  # $1=mtu — set, settle 5s, probe-verify (race-proof per discrimination test)
  echo "$PW" | sudo -S ip link set dev $IF mtu "$1" 2>/dev/null
  sleep 5
  timeout 40 uhd_usrp_probe --args addr=192.168.10.2 >/dev/null 2>&1 \
    && echo "[OK] probe mtu=$1" || { echo "[FAIL] probe mtu=$1"; return 1; }
}

run_arm() {  # $1=mtu $2=armtag
  echo "=== ARM $2 mtu=$1 ==="
  CAP=/home/hy/captures/p172_mtu$1_$2.fc32
  OUT=/home/hy/captures/p172_mtu$1_$2.out
  ERR=/home/hy/captures/p172_mtu$1_$2.err
  unset LD_LIBRARY_PATH
  LD_PRELOAD=./wrap_rpc2.so PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python test_usrp_rxonly_instrumented.py \
    --freq 5250 --tx-gain 0 --tx-scale 0.1 --rx-gain 31.5 --rx-scale 40 \
    --interval 100 --warmup 20 --run 30 --scales 40,40 \
    --capture "$CAP" >"$OUT" 2>"$ERR"
  echo "arm $2 mtu=$1 harness rc=$?"
  echo "  DECODE_SUCCESS: $(grep -ac DECODE_SUCCESS "$ERR")   LDPC FCS error: $(grep -ac 'LDPC FCS error' "$ERR")"
  grep -a "RESULT" "$OUT" | sed 's/^/  /'
  grep -a "Maximum frame size" "$ERR" | head -1 | sed 's/^/  [uhd] /'
}

set_mtu 9000 || exit 1
run_arm 9000 b1
run_arm 9000 b2
set_mtu 1500 || exit 1
run_arm 1500 a2
echo "=== ALL ARMS DONE ==="
