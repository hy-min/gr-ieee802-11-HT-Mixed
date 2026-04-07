#!/usr/bin/env python3
"""
Test imports in conda environment
"""
import os
import sys

print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")
print(f"Python path: {sys.path}")

# Set RPC environment variables
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

print("\nTesting imports...")

# Test gnuradio
try:
    from gnuradio import gr
    print("✓ gnuradio.gr imported")
except Exception as e:
    print(f"✗ gnuradio.gr import failed: {e}")

# Test htsig
try:
    from gnuradio import htsig
    print("✓ gnuradio.htsig imported")
    # Check attributes
    attrs = [x for x in dir(htsig) if not x.startswith('_')]
    print(f"  Attributes: {attrs}")
    # Check for ht_header_tagged
    if hasattr(htsig, 'ht_header_tagged'):
        print("✓ htsig.ht_header_tagged found!")
        print(f"  Type: {type(htsig.ht_header_tagged)}")
    else:
        print("✗ htsig.ht_header_tagged not found")
        # Try htsig.htsig_python
        if hasattr(htsig, 'htsig_python'):
            hp = htsig.htsig_python
            hp_attrs = [x for x in dir(hp) if not x.startswith('_')]
            print(f"  htsig_python attributes: {hp_attrs}")
except Exception as e:
    print(f"✗ gnuradio.htsig import failed: {e}")

# Test ieee802_11
try:
    import ieee802_11
    print("✓ ieee802_11 imported")
except Exception as e:
    print(f"✗ ieee802_11 import failed: {e}")

# Test mywifi
try:
    from gnuradio import mywifi
    print("✓ gnuradio.mywifi imported")
except Exception as e:
    print(f"✗ gnuradio.mywifi import failed: {e}")

print("\nTesting module locations...")
for mod in [gr, htsig, 'ieee802_11']:
    try:
        if isinstance(mod, str):
            import importlib
            mod_obj = importlib.import_module(mod)
        else:
            mod_obj = mod
        print(f"{mod_obj.__name__}: {getattr(mod_obj, '__file__', 'No file')}")
    except Exception as e:
        print(f"Error getting location for {mod}: {e}")