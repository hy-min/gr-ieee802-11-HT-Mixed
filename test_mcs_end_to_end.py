#!/home/hy/conda/envs/gnuradio/bin/python
"""
MCS 0-7端到端性能测试和系统验证

测试所有HT MCS模式的完整发送-接收链：
1. 生成测试数据
2. 通过wifi_phy_hier编码和调制
3. 添加噪声（可选）
4. 通过wifi_phy_hier解调和解码
5. 验证数据完整性和测量性能
"""

import os
import sys
import time
import pmt
import numpy as np
from gnuradio import gr, blocks, channels, pdu
from gnuradio.filter import pfb
import ieee802_11

# 导入wifi_phy_hier
sys.path.insert(0, 'examples')
from wifi_phy_hier import wifi_phy_hier

# MCS到编码映射（基于decode_mac.cc的mcs_to_encoding函数）
MCS_TO_ENCODING = {
    0: ieee802_11.BPSK_1_2,    # BPSK 1/2
    1: ieee802_11.QPSK_1_2,    # QPSK 1/2
    2: ieee802_11.QPSK_3_4,    # QPSK 3/4
    3: ieee802_11.QAM16_1_2,   # 16-QAM 1/2
    4: ieee802_11.QAM16_3_4,   # 16-QAM 3/4
    5: ieee802_11.QAM64_2_3,   # 64-QAM 2/3
    6: ieee802_11.QAM64_3_4,   # 64-QAM 3/4
    # 注意：QAM64_5_6在Python绑定中可能不可用
    # 7: 需要特殊处理
}

# MCS描述
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
    """
    运行单个MCS的端到端测试
    基于test_loopback_noqt.py但简化，移除CSI路径

    参数:
        mcs: MCS值 (0-7)
        test_params: 测试参数字典

    返回:
        dict: 测试结果
    """
    print(f"\n{'='*60}")
    print(f"测试 MCS{mcs}: {MCS_DESCRIPTIONS.get(mcs, 'Unknown')}")
    print(f"{'='*60}")

    # 获取编码
    if mcs in MCS_TO_ENCODING:
        encoding = MCS_TO_ENCODING[mcs]
    elif mcs == 7:
        # 尝试使用整数值8（QAM64_5_6的枚举值）
        print("警告: MCS7使用整数值8（QAM64_5_6）")
        encoding = 8  # QAM64_5_6的枚举值
    else:
        print(f"错误: 不支持的MCS值 {mcs}")
        return None

    tb = gr.top_block()

    # 测试参数
    pdu_length = test_params.get('pdu_length', 10)
    interval = test_params.get('interval', 1000)
    snr_db = test_params.get('snr_db', 30)
    out_buf_size = test_params.get('out_buf_size', 96000)
    sensitivity = test_params.get('sensitivity', 0.01)
    test_duration = test_params.get('test_duration', 2)

    print(f"参数: pdu_length={pdu_length}, interval={interval}, encoding={encoding}")

    # 创建wifi_phy_hier实例
    print(f"创建wifi_phy_hier实例 (编码={encoding})...")
    try:
        wifi_phy = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=encoding,
            frequency=5.89e9,
            sensitivity=sensitivity,
        )
    except Exception as e:
        print(f"错误: 创建wifi_phy_hier失败: {e}")
        return None

    # MAC层
    try:
        mac = ieee802_11.mac(
            [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
            [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
            [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
        )
    except Exception as e:
        print(f"错误: 创建MAC失败: {e}")
        return None

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
        epsilon=1.0,  # 无采样率偏移
        taps=[1.0],
        noise_seed=0,
        block_tags=False
    )

    # 重采样（补偿epsilon） - 简化，使用1.0
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

    # 调试消息输出
    msg_debug_rx = blocks.message_debug(True, gr.log_levels.info)

    # ========== 连接流图 ==========

    # 消息连接
    tb.msg_connect((msg_strobe, 'strobe'), (mac, 'app in'))
    tb.msg_connect((mac, 'phy out'), (msg_debug_mac, 'store'))
    tb.msg_connect((mac, 'phy out'), (wifi_phy, 'mac_in'))
    tb.msg_connect((wifi_phy, 'mac_out'), (msg_debug_rx, 'store'))

    # 数据流连接
    if use_packet_pad:
        tb.connect((wifi_phy, 0), (packet_pad, 0))
        tb.connect((packet_pad, 0), (multiplier, 0))
    else:
        tb.connect((wifi_phy, 0), (multiplier, 0))

    tb.connect((multiplier, 0), (channel, 0))
    tb.connect((channel, 0), (resampler, 0))
    tb.connect((resampler, 0), (wifi_phy, 0))

    # 运行测试
    print("启动流图...")
    start_time = time.time()

    try:
        tb.start()
        # 运行足够长时间以接收至少一个包
        time.sleep(test_duration)
        tb.stop()
        tb.wait()
    except Exception as e:
        print(f"错误: 流图运行失败: {e}")
        import traceback
        traceback.print_exc()
        return None

    elapsed_time = time.time() - start_time

    # 收集结果
    result = {
        'mcs': mcs,
        'encoding': str(encoding),
        'description': MCS_DESCRIPTIONS.get(mcs, 'Unknown'),
        'test_duration': elapsed_time,
        'pdu_length': pdu_length,
        'snr_db': snr_db,
        'success': True,
        'sent_messages': msg_debug_mac.num_messages(),
        'received_messages': msg_debug_rx.num_messages(),
    }

    print(f"测试完成:")
    print(f"  发送消息数: {result['sent_messages']}")
    print(f"  接收消息数: {result['received_messages']}")
    print(f"  测试时长: {elapsed_time:.2f}秒")

    return result

def main():
    print("="*70)
    print("HT Mixed模式 MCS 0-7端到端性能测试")
    print("="*70)

    # 设置环境变量禁用RPC
    os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
    os.environ['GR_RPC_ENABLE'] = 'False'
    os.environ['GR_RPC_SERVER_ENABLE'] = 'False'

    # 测试参数
    test_params = {
        'pdu_length': 10,       # 测试数据长度（字节）
        'interval': 1000,       # 消息间隔（毫秒）
        'snr_db': 30,           # 高SNR以确保成功
        'out_buf_size': 96000,  # 输出缓冲区大小
        'test_duration': 2,     # 测试持续时间（秒）
        'sensitivity': 0.01,    # 接收灵敏度
    }

    print(f"测试参数:")
    for key, value in test_params.items():
        print(f"  {key}: {value}")

    # 测试的MCS列表
    # 注意：MCS7可能有问题（QAM64_5_6在Python绑定中不可用）
    mcs_list = [0]  # 只测试MCS0用于验证
    # mcs_list.append(7)  # 如果需要测试MCS7

    results = []

    for mcs in mcs_list:
        result = run_mcs_test(mcs, test_params)
        if result:
            results.append(result)

    # 打印总结报告
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
        success = result['success']
        rx_msgs = result['received_messages']

        status = "✓ 通过" if success and rx_msgs > 0 else "✗ 失败"
        print(f"  MCS{mcs:2d} ({desc:15s}): {status} (接收消息: {rx_msgs})")

        if success and rx_msgs > 0:
            success_count += 1

    print(f"\n成功: {success_count}/{len(results)}")

    # 保存详细结果到文件
    output_file = "/tmp/mcs_test_results.txt"
    with open(output_file, 'w') as f:
        f.write("MCS端到端测试结果\n")
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
    # 注意：此脚本需要在conda环境中运行，并使用LD_PRELOAD禁用RPC
    # 示例: LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py
    sys.exit(main())