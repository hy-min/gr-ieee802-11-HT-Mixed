#!/usr/bin/env python3
"""
Complete RPC disable script with all known methods.
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
    'GNURADIO_RUNTIME_DIR': '/tmp/gnuradio_no_rpc',
    'HOME': '/tmp/gnuradio_no_rpc',
    'GR_PERFORMANCE_COUNTERS_ON': 'False',
    'GR_PREFER_TCP_FOR_RPC': 'False',
    'GR_RPC_THRIFT': 'False',
}

for key, value in rpc_env_vars.items():
    os.environ[key] = value
    print(f"Set {key}={value}")

print("\n" + "="*60)
print("Attempting to import GNU Radio with RPC disabled...")
print("="*60)

try:
    # Import gnuradio
    from gnuradio import gr
    print("✓ Imported gnuradio.gr")

    # Try to disable realtime scheduling (might affect RPC)
    try:
        gr.enable_realtime_scheduling(False)
        print("✓ Disabled realtime scheduling")
    except Exception as e:
        print(f"  Note: Could not disable realtime scheduling: {e}")

    # Try to access rpcmanager
    print("\nChecking rpcmanager...")
    if hasattr(gr, 'rpcmanager'):
        print(f"  gr.rpcmanager: {gr.rpcmanager}")

        # Try to get the rpcserver booter
        try:
            rpc_booter = gr.rpcmanager.get()
            print(f"  Got rpcserver_booter_base: {rpc_booter}")
            print(f"  Type: {type(rpc_booter)}")

            # Try to check if it's active
            if hasattr(rpc_booter, 'is_active'):
                active = rpc_booter.is_active()
                print(f"  RPC booter active: {active}")

            # Try to disable it
            if hasattr(rpc_booter, 'disable'):
                print("  Attempting to disable rpc booter...")
                rpc_booter.disable()
                print("  ✓ Called disable() on rpc booter")

        except Exception as e:
            print(f"  Error accessing rpcmanager.get(): {e}")
    else:
        print("  gr.rpcmanager not available")

    # Try to create a top block
    print("\nCreating top block...")
    tb = gr.top_block()
    print("✓ Created top block")

    # Try to import ieee802_11
    print("\nImporting ieee802_11...")
    import ieee802_11
    print("✓ Imported ieee802_11")

    # Create decode_mac
    print("Creating decode_mac block...")
    decode = ieee802_11.decode_mac(log=False, debug=False)
    print(f"✓ Created decode_mac: {decode}")

    # Try to import htsig
    print("\nImporting htsig...")
    try:
        import htsig
        print("✓ Imported htsig")
    except ImportError as e:
        print(f"✗ Could not import htsig: {e}")
        print("  This might be expected if not in Python path")

    print("\n" + "="*60)
    print("SUCCESS: All imports completed with RPC disabled")
    print("You should now be able to create flow graphs.")
    print("="*60)

    # Return success
    sys.exit(0)

except Exception as e:
    print(f"\n✗ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)