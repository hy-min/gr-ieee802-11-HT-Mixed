#!/home/hy/conda/envs/gnuradio/bin/python
"""
MCS0 Conv vs LDPC 端到端测试
基于test_mcs_end_to_end.py框架（该框架conv可达10/10）
"""

import os
import sys
import time
import pmt
import numpy as np
from gnuradio import gr, blocks, channels, pdu
from gnuradio.filter import pfb
import ieee802_11

sys.path.insert(0, 'examples')
from wifi_phy_hier import wifi_phy_hier

def run_mcs_test(mcs, use_ldpc):
    coding = "LDPC" if use_ldpc else "Conv"
    print(f"\n{'='*60}")
    print(f"MCS{mcs} ({coding})")
    print(f"{'='*60}")

    tb = gr.top_block()
    wifi_phy = wifi_phy_hier(
        bandwidth=10e6,
        chan_est=ieee802_11.LS,
        encoding=ieee802_11.BPSK_1_2,
        frequency=5.89e9,
        sensitivity=0.01,
    )

    # 设置LDPC模式
    wifi_phy.ieee802_11_mapper_0.set_use_ldpc(use_ldpc)

    mac = ieee802_11.mac(
        [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
        [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
        [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
    )

    msg_debug_mac = blocks.message_debug(True, gr.log_levels.info)
    msg_strobe = blocks.message_strobe(pmt.intern("x"*10), 1000)
    noise_voltage = 0.0
    channel = channels.channel_model(
        noise_voltage=noise_voltage,
        frequency_offset=0,
        epsilon=1.0,
        taps=[1.0],
        noise_seed=0,
        block_tags=False
    )
    resampler = pfb.arb_resampler_ccf(1.0, taps=None, flt_size=32, atten=100)
    resampler.declare_sample_delay(0)
    multiplier = blocks.multiply_const_cc(1, 1)
    msg_debug_rx = blocks.message_debug(True, gr.log_levels.info)

    tb.msg_connect((msg_strobe, 'strobe'), (mac, 'app in'))
    tb.msg_connect((mac, 'phy out'), (msg_debug_mac, 'store'))
    tb.msg_connect((mac, 'phy out'), (wifi_phy, 'mac_in'))
    tb.msg_connect((wifi_phy, 'mac_out'), (msg_debug_rx, 'store'))
    tb.connect((wifi_phy, 0), (multiplier, 0))
    tb.connect((multiplier, 0), (channel, 0))
    # DEBUG: bypass arb_resampler to eliminate group delay/filtering issues
    tb.connect((channel, 0), (wifi_phy, 0))

    tb.start()
    time.sleep(12)
    tb.stop()
    tb.wait()

    tx = msg_debug_mac.num_messages()
    rx = msg_debug_rx.num_messages()
    print(f"  TX={tx}, RX={rx}")
    return rx

if __name__ == "__main__":
    os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
    os.environ['GR_RPC_ENABLE'] = 'False'
    os.environ['GR_RPC_SERVER_ENABLE'] = 'False'

    print("="*60)
    print("MCS0 Conv vs LDPC")
    print("="*60)

    conv_rx = run_mcs_test(0, False)
    ldpc_rx = run_mcs_test(0, True)

    print(f"\n{'='*60}")
    print("结果")
    print(f"{'='*60}")
    print(f"  Conv RX: {conv_rx}")
    print(f"  LDPC RX: {ldpc_rx}")

    sys.exit(0 if (conv_rx >= 5 and ldpc_rx >= 5) else 1)
