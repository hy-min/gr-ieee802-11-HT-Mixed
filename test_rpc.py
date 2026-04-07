#!/usr/bin/env python3
import os
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

import sys
print("Testing GNU Radio import...")
from gnuradio import gr
print("Creating top block...")
tb = gr.top_block()
print("Success!")
sys.exit(0)