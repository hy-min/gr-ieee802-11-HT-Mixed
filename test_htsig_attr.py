#!/usr/bin/env python3
"""
Test htsig module attributes
"""
import os

# Disable RPC
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

print("Testing htsig module...")
try:
    from gnuradio import htsig
    print("✓ Imported htsig")
    print(f"htsig module: {htsig}")
    print(f"htsig file: {htsig.__file__}")

    # List all attributes
    attrs = [x for x in dir(htsig) if not x.startswith('_')]
    print(f"Attributes: {attrs}")

    # Check if ht_header_tagged exists
    if hasattr(htsig, 'ht_header_tagged'):
        print("✓ htsig.ht_header_tagged exists")
        print(f"  Type: {type(htsig.ht_header_tagged)}")
    else:
        print("✗ htsig.ht_header_tagged NOT found")
        # Try to see if it's in a submodule
        print("Checking submodules...")
        for attr in attrs:
            obj = getattr(htsig, attr)
            if not callable(obj) and hasattr(obj, '__file__'):
                print(f"  Submodule: {attr} -> {obj}")

except ImportError as e:
    print(f"✗ ImportError: {e}")
except Exception as e:
    print(f"✗ Other error: {e}")
    import traceback
    traceback.print_exc()

print("\nTesting direct import...")
try:
    # Try alternative import
    import gnuradio.htsig as htsig2
    print("✓ Direct import works")
    # Try to find the class via htsig2.htsig_python maybe?
except Exception as e:
    print(f"✗ Error: {e}")

print("\nTesting if block exists via gr...")
try:
    from gnuradio import gr
    # Try to get block via gr.basic_block?
    print("gr module imported")
except Exception as e:
    print(f"Error: {e}")