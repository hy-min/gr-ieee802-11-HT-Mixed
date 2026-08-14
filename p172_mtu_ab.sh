#!/usr/bin/env bash
# p172_mtu_ab.sh — H1 mechanism test: interleaved MTU 1500/9000, 60s capture per arm.
# Primary endpoint (pre-registered): A-family tear rate + hole POSITION
#   (per-packet mechanism predicts hole start moves 364 -> ~2200 with jumbo).
# Secondary: underflow count, DECODE_SUCCESS/FAIL per arm.
set -u
cd /home/hy/gr-ieee802-11
PW='qjwzlss'
IF=enp4s0

restore() { echo "$PW" | sudo -S ip link set dev $IF mtu 1500 2>/dev/null; echo "[CLEANUP] MTU restored 1500"; }
trap restore EXIT

run_arm() {  # $1=mtu $2=armtag
  echo "=== ARM $2 mtu=$1 ==="
  echo "$PW" | sudo -S ip link set dev $IF mtu "$1" || { echo "[FAIL] mtu set $1"; return 1; }
  ip -o link show $IF | grep -o 'mtu [0-9]*'
  if ! timeout 40 uhd_usrp_probe --args addr=192.168.10.2 >/dev/null 2>&1; then
    echo "[FAIL] probe mtu=$1"; return 1
  fi
  echo "[OK] probe mtu=$1"
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
  echo "  underflows: $(grep -ac underflow "$ERR")   overflows: $(grep -ac ' overflow' "$ERR")"
  echo "  DECODE_SUCCESS: $(grep -ac DECODE_SUCCESS "$ERR")   LDPC FCS error: $(grep -ac 'LDPC FCS error' "$ERR")"
  grep -a "RESULT" "$OUT" | sed 's/^/  /'
  grep -ai "mtu\|frame.size\|jumbo" "$ERR" | head -3 | sed 's/^/  [uhd] /'
}

run_arm 1500 a1
run_arm 9000 b1
run_arm 1500 a2
run_arm 9000 b2
echo "=== ALL ARMS DONE ==="
