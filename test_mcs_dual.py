#!/home/hy/conda/envs/gnuradio/bin/python
"""
MCS测试使用独立的发射和接收实例
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
    3: ieee802_11.QAM16_1_2,
    4: ieee802_11.QAM16_3_4,
    5: ieee802_11.QAM64_2_3,
    6: ieee802_11.QAM64_3_4,
}

MCS_DESCRIPTIONS = {
    0: "BPSK 1/2",
    1: "QPSK 1/2",
    2: "QPSK 3/4",
    3: "16-QAM 1/2",
    4: "16-QAM 3/4",
    5: "64-QAM 2/3",
    6: "64-QAM 3/4",
    7: "64-QAM 5/6",
}

def run_mcs_test(mcs, test_params):
    print(f"\n{'='*60}")
    print(f"测试 MCS{mcs}: {MCS_DESCRIPTIONS.get(mcs, 'Unknown')}")
    print(f"{'='*60}")

    if mcs in MCS_TO_ENCODING:
        encoding = MCS_TO_ENCODING[mcs]
    elif mcs == 7:
        print("警告: MCS7使用整数值8（QAM64_5_6）")
        encoding = 8
    else:
        print(f"错误: 不支持的MCS值 {mcs}")
        return None

    tb = gr.top_block()

    # 测试参数
    pdu_length = test_params.get('pdu_length', 10)
    interval = test_params.get('interval', 1000)
    snr_db = test_params.get('snr_db', 30)
    sensitivity = test_params.get('sensitivity', 0.01)
    test_duration = test_params.get('test_duration', 2)

    print(f"参数: pdu_length={pdu_length}, interval={interval}, encoding={encoding}")

    # 创建独立的发射和接收实例
    print("创建发射器实例...")
    wifi_phy_tx = wifi_phy_hier(
        bandwidth=10e6,
        chan_est=ieee802_11.LS,
        encoding=encoding,
        frequency=5.89e9,
        sensitivity=sensitivity,
    )
    
    print("创建接收器实例...")
    wifi_phy_rx = wifi_phy_hier(
        bandwidth=10e6,
        chan_est=ieee802_11.LS,
        encoding=encoding,  # 接收器需要知道编码？实际上会从HT-SIG解析
        frequency=5.89e9,
        sensitivity=sensitivity,
    )

    # MAC层
    mac = ieee802_11.mac(
        [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
        [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
        [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
    )

    # 消息调试
    msg_debug_tx = blocks.message_debug(True, gr.log_levels.info)
    msg_debug_rx = blocks.message_debug(True, gr.log_levels.info)

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

    # 重采样（简化）
    resampler = pfb.arb_resampler_ccf(1.0, taps=None, flt_size=32, atten=100)
    resampler.declare_sample_delay(0)

    # 消息连接
    tb.msg_connect((msg_strobe, 'strobe'), (mac, 'app in'))
    tb.msg_connect((mac, 'phy out'), (msg_debug_tx, 'store'))
    tb.msg_connect((mac, 'phy out'), (wifi_phy_tx, 'mac_in'))
    tb.msg_connect((wifi_phy_rx, 'mac_out'), (msg_debug_rx, 'store'))

    # 数据流连接：发射器 -> 信道 -> 接收器
    tb.connect((wifi_phy_tx, 0), (channel, 0))
    tb.connect((channel, 0), (resampler, 0))
    tb.connect((resampler, 0), (wifi_phy_rx, 0))

    print("启动流图...")
    start_time = time.time()

    try:
        tb.start()
        time.sleep(test_duration)
        tb.stop()
        tb.wait()
    except Exception as e:
        print(f"错误: 流图运行失败: {e}")
        import traceback
        traceback.print_exc()
        return None

    elapsed_time = time.time() - start_time

    result = {
        'mcs': mcs,
        'encoding': str(encoding),
        'description': MCS_DESCRIPTIONS.get(mcs, 'Unknown'),
        'test_duration': elapsed_time,
        'pdu_length': pdu_length,
        'snr_db': snr_db,
        'success': True,
        'sent_messages': msg_debug_tx.num_messages(),
        'received_messages': msg_debug_rx.num_messages(),
    }

    print(f"测试完成:")
    print(f"  发送消息数: {result['sent_messages']}")
    print(f"  接收消息数: {result['received_messages']}")
    print(f"  测试时长: {elapsed_time:.2f}秒")

    return result

def main():
    print("="*70)
    print("HT Mixed模式 MCS测试（独立发射接收实例）")
    print("="*70)

    test_params = {
        'pdu_length': 10,
        'interval': 1000,
        'snr_db': 30,
        'test_duration': 2,
        'sensitivity': 0.01,
    }

    print(f"测试参数:")
    for key, value in test_params.items():
        print(f"  {key}: {value}")

    # 只测试MCS0
    mcs_list = [0]

    results = []
    for mcs in mcs_list:
        result = run_mcs_test(mcs, test_params)
        if result:
            results.append(result)

    print("\n" + "="*70)
    print("测试总结报告")
    print("="*70)

    if not results:
        print("没有成功的测试结果")
        return 1

    print(f"\n共测试 {len(results)} 个MCS模式:")
    success_count = 0
    for result in results:
        mcs = result['mcs']
        desc = result['description']
        rx_msgs = result['received_messages']
        status = "✓ 通过" if rx_msgs > 0 else "✗ 失败"
        print(f"  MCS{mcs:2d} ({desc:15s}): {status} (接收消息: {rx_msgs})")
        if rx_msgs > 0:
            success_count += 1

    print(f"\n成功: {success_count}/{len(results)}")

    output_file = "/tmp/mcs_dual_results.txt"
    with open(output_file, 'w') as f:
        f.write("MCS双实例测试结果\n")
        f.write("="*50 + "\n")
        for result in results:
            f.write(f"\nMCS{result['mcs']}: {result['description']}\n")
            for key, value in result.items():
                if key not in ['mcs', 'description']:
                    f.write(f"  {key}: {value}\n")

    print(f"\n详细结果已保存到: {output_file}")

    if success_count == len(results):
        print("\n✓ 所有测试通过!")
        return 0
    else:
        print("\n✗ 部分测试失败")
        return 1

if __name__ == "__main__":
    sys.exit(main())
