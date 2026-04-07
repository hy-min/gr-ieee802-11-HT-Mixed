#!/usr/bin/env python3
import os
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

import sys
from gnuradio import gr

# Try to disable RPC manager if possible
try:
    import gnuradio.rpcmanager as rpc
    rpc.setup(False)
    print("Disabled RPC manager via rpcmanager.setup(False)")
except Exception as e:
    print(f"Could not disable RPC via rpcmanager: {e}")

# Try alternative
try:
    gr.rpcmanager_setup(False)
    print("Disabled RPC manager via gr.rpcmanager_setup(False)")
except Exception as e:
    print(f"Could not disable RPC via gr.rpcmanager_setup: {e}")

print("Creating top block...")
tb = gr.top_block()
print("Success!")

# Try to create a decode_mac block
try:
    import ieee802_11
    print("Importing ieee802_11...")
    dec = ieee802_11.decode_mac(False, False)
    print("Created decode_mac block")
except Exception as e:
    print(f"Failed to create decode_mac: {e}")

sys.exit(0)