#!/home/hy/conda/envs/gnuradio/bin/python
"""
快速测试MCS 0-2
"""
import os
import sys
import time
import pmt
from gnuradio import gr, blocks, channels, pdu
from gnuradio.filter import pfb
import ieee802_11

sys.path.insert(0, 'examples')
from wifi_phy_hier import wifi_phy_hier

# 设置环境变量禁用RPC
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'

# MCS到编码映射
MCS_TO_ENCODING = {
    0: ieee802_11.BPSK_1_2,
    1: ieee802_11.QPSK_1_2,
    2: ieee802_11.QPSK_3_4,
}

def test_single_mcs(mcs):
    print(f"\n=== 测试 MCS{mcs} ===")
    
    tb = gr.top_block()
    
    # 参数
    pdu_length = 10
    interval = 1000
    snr_db = 30
    out_buf_size = 96000
    sensitivity = 0.01
    test_duration = 2
    
    encoding = MCS_TO_ENCODING[mcs]
    print(f"编码: {encoding}")
    
    # 创建wifi_phy_hier
    wifi_phy = wifi_phy_hier(
        bandwidth=10e6,
        chan_est=ieee802_11.LS,
        encoding=encoding,
        frequency=5.89e9,
        sensitivity=sensitivity,
    )
    
    # MAC层
    mac = ieee802_11.mac(
        [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
        [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
        [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
    )
    
    # MAC层消息调试
    msg_debug_mac = blocks.message_debug(True, gr.log_levels.info)
    
    # 消息源
    msg_strobe = blocks.message_strobe(
        pmt.intern("".join("x" for i in range(pdu_length))),
        interval
    )
    
    # 信道模型
    noise_voltage = 10**(-snr_db / 20.0)
    channel = channels.channel_model(
        noise_voltage=noise_voltage,
        frequency_offset=0,
        epsilon=1.0,
        taps=[1.0],
        noise_seed=0,
        block_tags=False
    )
    
    # 重采样
    resampler = pfb.arb_resampler_ccf(1.0, taps=None, flt_size=32, atten=100)
    resampler.declare_sample_delay(0)
    
    # 功率调整
    multiplier = blocks.multiply_const_cc(1, 1)
    
    # 调试消息输出
    msg_debug_rx = blocks.message_debug(True, gr.log_levels.info)
    
    # 消息连接
    tb.msg_connect((msg_strobe, 'strobe'), (mac, 'app in'))
    tb.msg_connect((mac, 'phy out'), (msg_debug_mac, 'print_pdu'))
    tb.msg_connect((mac, 'phy out'), (wifi_phy, 'mac_in'))
    tb.msg_connect((wifi_phy, 'mac_out'), (msg_debug_rx, 'print_pdu'))
    
    # 数据流连接
    tb.connect((wifi_phy, 0), (multiplier, 0))
    tb.connect((multiplier, 0), (channel, 0))
    tb.connect((channel, 0), (resampler, 0))
    tb.connect((resampler, 0), (wifi_phy, 0))
    
    print("启动流图...")
    tb.start()
    time.sleep(test_duration)
    tb.stop()
    tb.wait()
    
    sent = msg_debug_mac.num_messages()
    received = msg_debug_rx.num_messages()
    print(f"发送: {sent}, 接收: {received}")
    
    return sent, received

if __name__ == "__main__":
    print("快速测试 MCS 0-2")
    results = []
    for mcs in [0, 1, 2]:
        try:
            sent, received = test_single_mcs(mcs)
            results.append((mcs, sent, received, received > 0))
        except Exception as e:
            print(f"MCS{mcs} 失败: {e}")
            import traceback
            traceback.print_exc()
            results.append((mcs, 0, 0, False))
    
    print("\n=== 测试结果 ===")
    for mcs, sent, received, success in results:
        status = "通过" if success else "失败"
        print(f"MCS{mcs}: {status} (发送: {sent}, 接收: {received})")
    
    success_count = sum(1 for _,_,_,s in results if s)
    print(f"\n总计: {success_count}/{len(results)} 通过")
