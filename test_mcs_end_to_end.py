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

import argparse
import os
import sys
import time
import pmt
import numpy as np
from gnuradio import gr, blocks, channels, pdu
from gnuradio.filter import pfb

try:
    from PyQt5 import Qt, sip, QtWidgets
    from gnuradio import qtgui
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

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

# GUI mode: MCS names and encoding values for dropdown
GUI_MCS_NAMES = [
    'BPSK 1/2 (MCS0)', 'BPSK 3/4',
    'QPSK 1/2 (MCS1)', 'QPSK 3/4 (MCS2)',
    '16QAM 1/2 (MCS3)', '16QAM 3/4 (MCS4)',
    '64QAM 2/3 (MCS5)', '64QAM 3/4 (MCS6)',
    '64QAM 5/6 (MCS7)',
]

GUI_MCS_VALUES = [
    ieee802_11.BPSK_1_2, ieee802_11.BPSK_3_4,
    ieee802_11.QPSK_1_2, ieee802_11.QPSK_3_4,
    ieee802_11.QAM16_1_2, ieee802_11.QAM16_3_4,
    ieee802_11.QAM64_2_3, ieee802_11.QAM64_3_4,
    ieee802_11.QAM64_5_6,
]

# Constellation display ranges per MCS
CONSTELLATION_RANGES = {
    0: (-1.5, 1.5),   # BPSK 1/2
    1: (-1.5, 1.5),   # BPSK 3/4
    2: (-1.5, 1.5),   # QPSK 1/2
    3: (-1.5, 1.5),   # QPSK 3/4
    4: (-3.0, 3.0),   # 16QAM 1/2
    5: (-3.0, 3.0),   # 16QAM 3/4
    6: (-7.0, 7.0),   # 64QAM 2/3
    7: (-7.0, 7.0),   # 64QAM 3/4
    8: (-7.0, 7.0),   # 64QAM 5/6
}


class encoding_stripper(gr.basic_block):
    """Remove encoding/mcs tags from PDU meta so mapper uses set_encoding()."""

    def __init__(self):
        gr.basic_block.__init__(
            self,
            name="encoding_stripper",
            in_sig=None,
            out_sig=None
        )
        self.message_port_register_in(pmt.intern("pdu"))
        self.message_port_register_out(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        data = pmt.cdr(msg)
        meta = pmt.dict_delete(meta, pmt.mp("encoding"))
        meta = pmt.dict_delete(meta, pmt.mp("mcs"))
        self.message_port_pub(pmt.intern("pdu"), pmt.cons(meta, data))


class mcs_detector(gr.basic_block):
    """Detect MCS from constellation PDU meta and trigger callback."""

    def __init__(self, callback):
        gr.basic_block.__init__(
            self,
            name="mcs_detector",
            in_sig=None,
            out_sig=None
        )
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)
        self.callback = callback
        self.last_mcs = -1

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        mcs = pmt.to_long(pmt.dict_ref(meta, pmt.mp('mcs'), pmt.from_long(0)))
        if mcs != self.last_mcs:
            self.last_mcs = mcs
            self.callback(mcs)


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
    cooldown = test_params.get('cooldown', 2)

    print(f"参数: pdu_length={pdu_length}, interval={interval}, encoding={encoding}, cooldown={cooldown}")

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

    # 设置LDPC模式
    use_ldpc = test_params.get('use_ldpc', False)
    wifi_phy.ieee802_11_mapper_0.set_use_ldpc(use_ldpc)
    print(f"  set_use_ldpc({use_ldpc})")

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
        # 活跃发送阶段
        time.sleep(test_duration)
        # 冷却阶段：让在途帧完成处理
        if cooldown > 0:
            time.sleep(cooldown)
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

def run_batch_mode():
    """Run automated MCS 0-7 batch tests (default mode)."""
    print("="*70)
    print("HT Mixed模式 MCS 0-7端到端性能测试")
    print("="*70)

    # 测试参数
    test_params = {
        'pdu_length': 10,       # 测试数据长度（字节）
        'interval': 1000,       # 消息间隔（毫秒）
        'snr_db': 30,           # 高SNR以确保成功
        'out_buf_size': 96000,  # 输出缓冲区大小
        'test_duration': 5,     # 活跃发送时间（秒）
        'cooldown': 3,          # 冷却时间（秒），停止发送后等待在途帧完成
        'sensitivity': 0.01,    # 接收灵敏度
    }

    print(f"测试参数:")
    for key, value in test_params.items():
        print(f"  {key}: {value}")

    # 测试MCS0 Conv作为baseline，然后测试MCS 0-7的LDPC
    results = []

    # Conv baseline
    print("\n" + "="*70)
    print("Baseline: MCS0 Conv")
    print("="*70)
    result = run_mcs_test(0, test_params)
    if result:
        results.append(result)

    # LDPC tests for MCS 0-7
    print("\n" + "="*70)
    print("LDPC模式测试: MCS 0-7")
    print("="*70)
    test_params['use_ldpc'] = True
    for mcs in range(8):
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

class MCSEndToEndGUI(gr.top_block, Qt.QWidget):
    """GUI mode for test_mcs_end_to_end with constellation display."""

    def __init__(self):
        gr.top_block.__init__(self, "MCS End-to-End Test + Constellation")
        Qt.QWidget.__init__(self)
        self.setWindowTitle("MCS End-to-End Test + Constellation Display")
        self.resize(800, 600)

        # ===== GUI Layout =====
        self.top_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_layout)

        # Control panel
        self.control_layout = Qt.QHBoxLayout()
        self.top_layout.addLayout(self.control_layout)

        # MCS chooser
        self.mcs_label = Qt.QLabel("TX MCS:")
        self.control_layout.addWidget(self.mcs_label)

        self.mcs_combo = Qt.QComboBox()
        self.mcs_combo.addItems(GUI_MCS_NAMES)
        self.mcs_combo.currentIndexChanged.connect(self.set_mcs)
        self.control_layout.addWidget(self.mcs_combo)

        # LDPC toggle
        self.ldpc_check = Qt.QCheckBox("LDPC")
        self.ldpc_check.setToolTip("Enable LDPC coding (unchecked = BCC)")
        self.ldpc_check.stateChanged.connect(self.set_use_ldpc)
        self.control_layout.addWidget(self.ldpc_check)

        # SNR slider
        self.snr_label = Qt.QLabel("SNR (dB):")
        self.control_layout.addWidget(self.snr_label)

        self.snr_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.snr_slider.setRange(0, 40)
        self.snr_slider.setValue(30)
        self.snr_slider.valueChanged.connect(self.set_snr)
        self.control_layout.addWidget(self.snr_slider)

        self.snr_value_label = Qt.QLabel("30 dB")
        self.control_layout.addWidget(self.snr_value_label)

        # Status labels
        self.rx_mcs_label = Qt.QLabel("RX MCS: --")
        self.control_layout.addWidget(self.rx_mcs_label)

        self.sent_label = Qt.QLabel("Sent: 0")
        self.control_layout.addWidget(self.sent_label)

        self.recv_label = Qt.QLabel("Recv: 0")
        self.control_layout.addWidget(self.recv_label)

        self.control_layout.addStretch(1)

        # ===== GNU Radio Blocks =====

        # WiFi PHY
        self.wifi_phy = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01
        )

        # Message strobe
        self.msg_strobe = blocks.message_strobe(pmt.intern("x" * 10), 1000)

        # MAC layer
        self.mac = ieee802_11.mac(
            [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
            [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
            [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
        )

        # Message debug (for counting)
        self.msg_debug_mac = blocks.message_debug(True, gr.log_levels.info)
        self.msg_debug_rx = blocks.message_debug(True, gr.log_levels.info)

        # Packet pad
        try:
            import foo
            self.packet_pad = foo.packet_pad2(False, False, 0.001, 500, 0)
            self.packet_pad.set_min_output_buffer(960000)
            use_packet_pad = True
        except ImportError:
            use_packet_pad = False

        # SNR / channel
        self.snr = 30.0
        self.multiply_const = blocks.multiply_const_cc(1.0)

        noise_voltage = 10**(-self.snr / 20.0)
        self.channel = channels.channel_model(
            noise_voltage=noise_voltage,
            frequency_offset=0.0,
            epsilon=1.0,
            taps=[1.0],
            noise_seed=0,
            block_tags=False
        )

        # Resampler
        self.resampler = pfb.arb_resampler_ccf(
            1.0, taps=None, flt_size=32, atten=100
        )
        self.resampler.declare_sample_delay(0)

        # Constellation display
        self.pdu_to_stream = blocks.pdu_to_tagged_stream(
            gr.types.complex_t, 'packet_len'
        )

        self.constellation_sink = qtgui.const_sink_c(480, "", 1, None)
        self.constellation_sink.set_update_time(0.10)
        self.constellation_sink.set_x_axis(-2, 2)
        self.constellation_sink.set_y_axis(-2, 2)

        constellation_widget = sip.wrapinstance(
            self.constellation_sink.qwidget(), QtWidgets.QWidget
        )
        self.top_layout.addWidget(constellation_widget)

        # Encoding stripper and MCS detector
        self.encoding_stripper = encoding_stripper()
        self.mcs_detect = mcs_detector(self.update_constellation_range)

        # ===== Connections =====

        # Message connections
        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
        self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy, 'mac_in'))
        self.msg_connect((self.wifi_phy, 'mac_out'), (self.msg_debug_rx, 'store'))
        self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))
        self.msg_connect((self.wifi_phy, 'constellation'), (self.pdu_to_stream, 'pdus'))
        self.msg_connect((self.wifi_phy, 'constellation'), (self.mcs_detect, 'pdu'))

        # Stream connections (loopback)
        if use_packet_pad:
            self.connect((self.wifi_phy, 0), (self.packet_pad, 0))
            self.connect((self.packet_pad, 0), (self.multiply_const, 0))
        else:
            self.connect((self.wifi_phy, 0), (self.multiply_const, 0))

        self.connect((self.multiply_const, 0), (self.channel, 0))
        self.connect((self.channel, 0), (self.resampler, 0))
        self.connect((self.resampler, 0), (self.wifi_phy, 0))

        # Constellation stream
        self.connect((self.pdu_to_stream, 0), (self.constellation_sink, 0))

        # Status update timer
        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(500)

    def set_mcs(self, index):
        encoding = GUI_MCS_VALUES[index]
        self.wifi_phy.set_encoding(encoding)
        print(f"[MCS] TX set to {GUI_MCS_NAMES[index]} (encoding={encoding})")

    def set_use_ldpc(self, state):
        enabled = (state == Qt.Qt.Checked)
        self.wifi_phy.set_use_ldpc(enabled)
        print(f"[LDPC] {'Enabled' if enabled else 'Disabled'} (BCC)")

    def set_snr(self, value):
        self.snr = float(value)
        self.snr_value_label.setText(f"{value} dB")
        noise_voltage = 10**(-self.snr / 20.0)
        self.channel.set_noise_voltage(noise_voltage)

    def update_constellation_range(self, mcs):
        xmin, xmax = CONSTELLATION_RANGES.get(mcs, (-2, 2))
        self.constellation_sink.set_x_axis(xmin, xmax)
        self.constellation_sink.set_y_axis(xmin, xmax)
        self.rx_mcs_label.setText(f"RX MCS: {GUI_MCS_NAMES[mcs]}")
        print(f"[CONSTELLATION] Auto-adapted to MCS {mcs}: range [{xmin}, {xmax}]")

    def update_status(self):
        sent = self.msg_debug_mac.num_messages()
        recv = self.msg_debug_rx.num_messages()
        self.sent_label.setText(f"Sent: {sent}")
        self.recv_label.setText(f"Recv: {recv}")

    def closeEvent(self, event):
        sent = self.msg_debug_mac.num_messages()
        recv = self.msg_debug_rx.num_messages()
        print(f"\n[GUI] Test session ended")
        print(f"  Sent messages: {sent}")
        print(f"  Received messages: {recv}")
        self.stop()
        self.wait()
        event.accept()


def run_gui_mode():
    """Launch the interactive GUI mode."""
    if not GUI_AVAILABLE:
        print("ERROR: GUI mode requires PyQt5 and gnuradio.qtgui.")
        print("Install with: pip install PyQt5")
        return 1

    from PyQt5 import Qt
    qapp = Qt.QApplication(sys.argv)
    gui = MCSEndToEndGUI()
    gui.show()
    gui.start()
    qapp.exec_()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='HT Mixed mode MCS 0-7 end-to-end test'
    )
    parser.add_argument(
        '--gui', action='store_true',
        help='Launch interactive GUI mode with constellation display'
    )
    args = parser.parse_args()

    os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
    os.environ['GR_RPC_ENABLE'] = 'False'
    os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
    os.environ['GR_RPC_PORT'] = '0'
    os.environ['GR_CONTROLPORT_ON'] = 'False'

    if args.gui:
        return run_gui_mode()
    else:
        return run_batch_mode()

if __name__ == "__main__":
    # 注意：此脚本需要在conda环境中运行，并使用LD_PRELOAD禁用RPC
    # 示例: LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py
    sys.exit(main())