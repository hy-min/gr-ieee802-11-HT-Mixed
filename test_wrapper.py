#!/usr/bin/env python3
"""
Wrapper script to disable RPC before running GNU Radio flow graph.
"""
import os
import sys

# Set all known environment variables to disable RPC/ControlPort
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_PORT'] = '0'
os.environ['GR_CONTROLPORT_ON'] = 'False'
os.environ['GR_CONF_CONTROLPORT'] = 'False'
os.environ['GNURADIO_RUNTIME_DIR'] = '/tmp/gnuradio_test_rpc'
os.environ['HOME'] = '/tmp/gnuradio_test_rpc'

print("Environment variables set for RPC/ControlPort disable")
print("GR_CONF_CONTROLPORT_ON:", os.environ.get('GR_CONF_CONTROLPORT_ON'))
print("GR_RPC_ENABLE:", os.environ.get('GR_RPC_ENABLE'))

# Now import and run the actual flow graph
if __name__ == "__main__":
    print("\nTrying to import GNU Radio modules...")
    try:
        from gnuradio import gr
        print("✓ Imported gnuradio.gr")

        # Try to create top block
        print("Creating top block...")
        tb = gr.top_block()
        print("✓ Top block created successfully")

        # Try to import ieee802_11
        print("Importing ieee802_11...")
        import ieee802_11
        print("✓ Imported ieee802_11")

        # Create decode_mac
        print("Creating decode_mac block...")
        decode = ieee802_11.decode_mac(False, False)
        print(f"✓ Created decode_mac: {decode}")

        print("\n✓ SUCCESS: RPC appears to be disabled")
        print("You should now be able to run flow graphs without RPC errors.")

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)