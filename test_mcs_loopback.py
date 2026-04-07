#!/home/hy/conda/envs/gnuradio/bin/python
"""
基于test_loopback_noqt.py的MCS端到端环回测试
测试所有HT MCS模式（0-6），跳过MCS7（QAM64_5_6不可用）
"""

import os
import sys
import time
import pmt

# 设置RPC禁用环境变量
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'

from gnuradio import gr, blocks, channels, pdu
from gnuradio.filter import pfb
import ieee802_11

# 导入wifi_phy_hier
sys.path.insert(0, 'examples')
from wifi_phy_hier import wifi_phy_hier

# MCS到编码映射
MCS_TO_ENCODING = {
    0: ieee802_11.BPSK_1_2,
    1: ieee802_11.QPSK_1_2,
    2: ieee802_11.QPSK_3_4,
    3: ieee802_11.QAM16_1_2,
    4: ieee802_11.QAM16_3_4,
    5: ieee802_11.QAM64_2_3,
    6: ieee802_11.QAM64_3_4,
    # 7: ieee802_11.QAM64_5_6,  # 在Python绑定中不可用
}

def run_single_mcs_test(mcs, encoding, test_params):
    """为单个MCS运行环回测试"""
    print(f"\n{'='*60}")
    print(f"测试 MCS{mcs}")
    print(f"{'='*60}")

    tb = gr.top_block()

    # 测试参数
    pdu_length = test_params['pdu_length']
    interval = test_params['interval']
    snr_db = test_params['snr_db']
    out_buf_size = test_params['out_buf_size']
    sensitivity = test_params['sensitivity']

    print(f"参数: pdu_length={pdu_length}, interval={interval}, encoding={encoding}")

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

    # 消息源：定期产生PDU
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

    # 重采样（补偿epsilon）
    resampler = pfb.arb_resampler_ccf(
        1.0,  # 无采样率偏移
        taps=None,
        flt_size=32,
        atten=100
    )
    resampler.declare_sample_delay(0)

    # 功率调整（SNR模拟）
    multiplier = blocks.multiply_const_cc(1, 1)

    # 尝试导入foo.packet_pad2，如果不可用则跳过
    try:
        import foo
        packet_pad = foo.packet_pad2(False, False, 0.001, 500, 0)
        packet_pad.set_min_output_buffer((out_buf_size * 10))
        use_packet_pad = True
    except ImportError:
        print("警告: foo模块不可用，跳过packet_pad2")
        use_packet_pad = False

    # MAC解析
    parse_mac = ieee802_11.parse_mac(False, True)

    # CSI提取
    extract_csi = ieee802_11.extract_csi()

    # PDU到tagged stream
    pdu_to_ts = pdu.pdu_to_tagged_stream(gr.types.complex_t, 'packet_len')

    # Null sink用于消费PDU流输出
    null_sink = blocks.null_sink(gr.sizeof_gr_complex)

    # 调试消息输出
    msg_debug_rx = blocks.message_debug(True, gr.log_levels.info)

    # ========== 连接流图 ==========

    # 消息连接
    tb.msg_connect((msg_strobe, 'strobe'), (mac, 'app in'))
    tb.msg_connect((mac, 'phy out'), (msg_debug_mac, 'print_pdu'))
    tb.msg_connect((mac, 'phy out'), (wifi_phy, 'mac_in'))
    tb.msg_connect((parse_mac, 'out'), (extract_csi, 'pdu in'))
    tb.msg_connect((wifi_phy, 'mac_out'), (msg_debug_rx, 'print_pdu'))
    tb.msg_connect((wifi_phy, 'mac_out'), (parse_mac, 'in'))
    tb.msg_connect((wifi_phy, 'eq_symbols'), (pdu_to_ts, 'pdus'))

    # 数据流连接
    if use_packet_pad:
        tb.connect((wifi_phy, 0), (packet_pad, 0))
        tb.connect((packet_pad, 0), (multiplier, 0))
    else:
        tb.connect((wifi_phy, 0), (multiplier, 0))

    tb.connect((multiplier, 0), (channel, 0))
    tb.connect((channel, 0), (resampler, 0))
    tb.connect((resampler, 0), (wifi_phy, 0))

    # CSI路径（简化）- 注释掉以避免itemsize不匹配
    # tb.connect((extract_csi, 0), (null_sink, 0))
    # tb.connect((pdu_to_ts, 0), (null_sink, 0))

    # 运行测试
    print("启动流图...")
    start_time = time.time()

    try:
        tb.start()
        # 运行足够长时间以接收至少一个包
        time.sleep(test_params['test_duration'])
        tb.stop()
        tb.wait()
    except Exception as e:
        print(f"错误: 流图运行失败: {e}")
        return None

    elapsed_time = time.time() - start_time

    # 收集结果
    result = {
        'mcs': mcs,
        'success': True,
        'test_duration': elapsed_time,
        'sent_messages': msg_debug_mac.num_messages(),
        'received_messages': msg_debug_rx.num_messages(),
        'errors': 0,
    }

    print(f"测试完成:")
    print(f"  发送消息数: {result['sent_messages']}")
    print(f"  接收消息数: {result['received_messages']}")
    print(f"  测试时长: {elapsed_time:.2f}秒")

    return result

def main():
    print("="*70)
    print("HT Mixed模式 MCS端到端环回测试")
    print("基于test_loopback_noqt.py")
    print("="*70)

    # 测试参数
    test_params = {
        'pdu_length': 10,       # 较小的包以减少测试时间
        'interval': 1000,       # 消息间隔（毫秒）
        'snr_db': 30,           # 高SNR以确保成功
        'out_buf_size': 96000,
        'sensitivity': 0.01,
        'test_duration': 2,     # 每个MCS测试持续时间（秒）
    }

    print(f"测试参数:")
    for key, value in test_params.items():
        print(f"  {key}: {value}")

    # 测试的MCS列表
    mcs_list = list(MCS_TO_ENCODING.keys())
    print(f"\n测试MCS: {mcs_list}")

    results = []
    success_count = 0

    for mcs in mcs_list:
        encoding = MCS_TO_ENCODING[mcs]
        result = run_single_mcs_test(mcs, encoding, test_params)

        if result:
            results.append(result)
            if result['received_messages'] > 0:
                success_count += 1
                print(f"✓ MCS{mcs} 测试成功")
            else:
                print(f"✗ MCS{mcs} 测试失败: 未接收到消息")
        else:
            print(f"✗ MCS{mcs} 测试失败: 流图错误")

    # 打印总结报告
    print("\n" + "="*70)
    print("测试总结报告")
    print("="*70)

    print(f"\n共测试 {len(results)} 个MCS模式:")
    for result in results:
        mcs = result['mcs']
        sent = result['sent_messages']
        received = result['received_messages']
        status = "✓ 通过" if received > 0 else "✗ 失败"
        print(f"  MCS{mcs:2d}: {status} (发送: {sent}, 接收: {received})")

    print(f"\n成功: {success_count}/{len(results)}")

    # 保存结果
    output_file = "/tmp/mcs_loopback_results.txt"
    with open(output_file, 'w') as f:
        f.write("MCS环回测试结果\n")
        f.write("="*50 + "\n")
        f.write(f"测试时间: {time.ctime()}\n")
        f.write(f"成功: {success_count}/{len(results)}\n\n")
        for result in results:
            f.write(f"MCS{result['mcs']}:\n")
            f.write(f"  发送消息: {result['sent_messages']}\n")
            f.write(f"  接收消息: {result['received_messages']}\n")
            f.write(f"  测试时长: {result['test_duration']:.2f}秒\n")
            f.write(f"  状态: {'通过' if result['received_messages'] > 0 else '失败'}\n\n")

    print(f"\n详细结果已保存到: {output_file}")

    if success_count == len(results):
        print("\n✓ 所有MCS测试通过!")
        return 0
    else:
        print("\n✗ 部分MCS测试失败")
        return 1

if __name__ == "__main__":
    # 注意：需要使用LD_PRELOAD禁用RPC
    # LD_PRELOAD=./wrap_rpc2.so python test_mcs_loopback.py
    sys.exit(main())