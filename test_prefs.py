#!/usr/bin/env python3
"""
Test disabling RPC via gr.prefs
"""
import os

# Set environment variables first
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
os.environ['GR_RPC_PORT'] = '0'
os.environ['GR_CONTROLPORT_ON'] = 'False'

from gnuradio import gr

print("gr.prefs available:", hasattr(gr, 'prefs'))
if hasattr(gr, 'prefs'):
    prefs = gr.prefs()
    print("Prefs object:", prefs)

    # Try to get current controlport settings
    try:
        cp_on = prefs.get_bool('controlport', 'on', False)
        print(f"controlport.on = {cp_on}")
    except Exception as e:
        print(f"Error getting controlport.on: {e}")

    # Try to set controlport off
    try:
        prefs.set_bool('controlport', 'on', False)
        print("Set controlport.on = False")
    except Exception as e:
        print(f"Error setting controlport.on: {e}")

    # Try to get rpc settings
    try:
        rpc_enable = prefs.get_bool('rpc', 'enable', False)
        print(f"rpc.enable = {rpc_enable}")
    except Exception as e:
        print(f"Error getting rpc.enable: {e}")

    # Try to set rpc off
    try:
        prefs.set_bool('rpc', 'enable', False)
        print("Set rpc.enable = False")
    except Exception as e:
        print(f"Error setting rpc.enable: {e}")

    # List all sections
    try:
        sections = prefs.sections()
        print(f"Sections: {sections}")
    except Exception as e:
        print(f"Error getting sections: {e}")

# Try to import htsig
print("\nTrying to import htsig...")
try:
    from gnuradio import htsig
    print("Successfully imported htsig")
except ImportError as e:
    print(f"ImportError: {e}")
except Exception as e:
    print(f"Other error importing htsig: {e}")

# Try to create a simple top block
print("\nTrying to create top block...")
try:
    tb = gr.top_block()
    print("Successfully created top block")
except Exception as e:
    print(f"Error creating top block: {e}")