#!/usr/bin/env python3
"""
Test wifi_phy_hier with RPC disabled via prefs
"""
import os
import sys

# Set ALL known RPC/ControlPort environment variables
rpc_env_vars = {
    'GR_CONF_CONTROLPORT_ON': 'False',
    'GR_RPC_ENABLE': 'False',
    'GR_RPC_SERVER_ENABLE': 'False',
    'GR_RPC_PORT': '0',
    'GR_CONTROLPORT_ON': 'False',
    'GR_CONF_CONTROLPORT': 'False',
    'GR_PERFORMANCE_COUNTERS_ON': 'False',
    'GR_PREFER_TCP_FOR_RPC': 'False',
    'GR_RPC_THRIFT': 'False',
}

for key, value in rpc_env_vars.items():
    os.environ[key] = value
    print(f"Set {key}={value}")

print("\nImporting gnuradio...")
from gnuradio import gr

print("Setting prefs...")
prefs = gr.prefs()
try:
    prefs.set_bool('controlport', 'on', False)
    print("Set controlport.on = False")
except Exception as e:
    print(f"Error setting controlport.on: {e}")

try:
    prefs.set_bool('rpc', 'enable', False)
    print("Set rpc.enable = False")
except Exception as e:
    print(f"Error setting rpc.enable: {e}")

print("\nImporting wifi_phy_hier...")
# Import the module - note: this will execute its imports
try:
    sys.path.insert(0, os.path.dirname(__file__))
    from examples.wifi_phy_hier import wifi_phy_hier
    print("Successfully imported wifi_phy_hier")
except Exception as e:
    print(f"Error importing wifi_phy_hier: {e}")
    sys.exit(1)

print("\nCreating wifi_phy_hier instance...")
try:
    # Create instance with default parameters
    instance = wifi_phy_hier()
    print(f"Successfully created instance: {instance}")
except Exception as e:
    print(f"Error creating instance: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\nTest completed successfully!")
sys.exit(0)