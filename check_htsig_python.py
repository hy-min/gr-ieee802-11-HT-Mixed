#!/usr/bin/env python3
import os
os.environ['GR_CONF_CONTROLPORT_ON']='False'
os.environ['GR_RPC_ENABLE']='False'

try:
    from gnuradio import htsig
    print("htsig imported")
    import htsig.htsig_python as hp
    print("htsig_python module:", hp)
    attrs = [x for x in dir(hp) if not x.startswith('_')]
    print("Attributes:", attrs)
    # Check for ht_header_tagged
    for attr in attrs:
        print(f"  {attr}: {type(getattr(hp, attr))}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()