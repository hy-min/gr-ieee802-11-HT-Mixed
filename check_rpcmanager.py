#!/usr/bin/env python3
"""
Check what methods are available on gr.rpcmanager
"""
import os
# Set environment variables before importing
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

from gnuradio import gr

print("gr.rpcmanager:", gr.rpcmanager)
print("Type:", type(gr.rpcmanager))

# Try to see what methods are available
try:
    print("\nAttempting to call dir() on gr.rpcmanager...")
    methods = [m for m in dir(gr.rpcmanager) if not m.startswith('_')]
    print("Available methods/properties:", methods)
except Exception as e:
    print(f"Error: {e}")

# Try to call setup if available
try:
    if hasattr(gr.rpcmanager, 'setup'):
        print("\nCalling gr.rpcmanager.setup(False)...")
        result = gr.rpcmanager.setup(False)
        print(f"Result: {result}")
    else:
        print("\ngr.rpcmanager.setup not available")
except Exception as e:
    print(f"Error calling setup: {e}")

# Try alternative
try:
    print("\nTrying gr.rpcmanager.disable()...")
    if hasattr(gr.rpcmanager, 'disable'):
        result = gr.rpcmanager.disable()
        print(f"Result: {result}")
    else:
        print("gr.rpcmanager.disable not available")
except Exception as e:
    print(f"Error: {e}")

# Check if there's a get() method
try:
    print("\nChecking gr.rpcmanager.get()...")
    if hasattr(gr.rpcmanager, 'get'):
        rpc_obj = gr.rpcmanager.get()
        print(f"Got rpc object: {rpc_obj}")
        print(f"Type: {type(rpc_obj)}")
    else:
        print("gr.rpcmanager.get not available")
except Exception as e:
    print(f"Error: {e}")