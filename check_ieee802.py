#!/usr/bin/env python3
import os
os.environ['GR_CONF_CONTROLPORT_ON']='False'
os.environ['GR_RPC_ENABLE']='False'

try:
    import ieee802_11
    print("ieee802_11 imported")
    attrs = [x for x in dir(ieee802_11) if 'header' in x.lower() or 'ht' in x.lower()]
    print("Relevant attributes:", attrs)
    # Print all if few
    if len(attrs) < 10:
        all_attrs = [x for x in dir(ieee802_11) if not x.startswith('_')]
        print("All attributes:", all_attrs)
except Exception as e:
    print(f"Error: {e}")