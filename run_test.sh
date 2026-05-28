#!/bin/bash
# WiFi test wrapper - auto sets LD_PRELOAD

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="/home/hy/conda/envs/gnuradio/bin/python"

# Ensure libs are up to date
cp "${SCRIPT_DIR}/build/lib/libgnuradio-ieee802_11.so"* /home/hy/conda/envs/gnuradio/lib/ 2>/dev/null
cp "${SCRIPT_DIR}/build/python/bindings/ieee802_11_python.cpython-38-x86_64-linux-gnu.so" \
   /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/ 2>/dev/null

# Run with LD_PRELOAD
export LD_PRELOAD="${SCRIPT_DIR}/wrap_rpc2.so"
exec "${PYTHON}" "$@"
