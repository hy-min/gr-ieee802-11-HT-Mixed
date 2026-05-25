# 实时星座图显示 + MCS 切换 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `frame_equalizer` 后实时显示星座图，支持 TX 端 MCS 切换和 RX 端自动适配。

**Architecture:** 修改 `frame_equalizer` 在 symbols 消息中携带 MCS 元信息，修改 `wifi_phy_hier` 新增 `constellation` 消息端口，创建纯 Python 顶层示例整合 loopback、星座图显示和 MCS 切换 GUI。

**Tech Stack:** GNU Radio 3.10, PyQt5, C++ (frame_equalizer), Python (顶层流图)

---

## 文件结构

| 文件 | 操作 | 说明 |
|------|------|------|
| `lib/frame_equalizer_impl.cc` | 修改 | symbols 消息 meta 添加 `mcs` 字段 |
| `wifi_phy_hier.py` | 修改 | 添加 `constellation` 消息端口 |
| `examples/wifi_loopback_constellation.py` | 创建 | 带星座图和 MCS 切换的 loopback 示例 |

---

### Task 1: 修改 frame_equalizer_impl.cc — 添加 mcs 到 symbols meta

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:2333-2336`

- [ ] **Step 1: 添加 mcs 字段到 symbols 消息 meta dict**

  找到 `lib/frame_equalizer_impl.cc` 中发送 symbols 消息的代码段（约 2333 行）：

  ```cpp
  pmt::pmt_t meta = pmt::make_dict();
  meta = pmt::dict_add(meta, pmt::mp("packet_len"), pmt::from_long(52));
  pmt::pmt_t vec = pmt::init_c32vector(52, out52);
  message_port_pub(pmt::mp("symbols"), pmt::cons(meta, vec));
  ```

  修改为：

  ```cpp
  pmt::pmt_t meta = pmt::make_dict();
  meta = pmt::dict_add(meta, pmt::mp("packet_len"), pmt::from_long(52));
  meta = pmt::dict_add(meta, pmt::mp("mcs"), pmt::from_long(d_frame_encoding));
  pmt::pmt_t vec = pmt::init_c32vector(52, out52);
  message_port_pub(pmt::mp("symbols"), pmt::cons(meta, vec));
  ```

- [ ] **Step 2: 编译 C++ 代码**

  Run:
  ```bash
  cd /home/hy/gr-ieee802-11/build
  make -j$(nproc)
  ```

  Expected: 编译成功，无错误。frame_equalizer_impl.cc 重新编译。

- [ ] **Step 3: 复制编译产物到 conda 环境**

  Run:
  ```bash
  cp /home/hy/gr-ieee802-11/build/lib/libgnuradio-ieee802_11.so* /home/hy/conda/envs/gnuradio/lib/
  cp /home/hy/gr-ieee802-11/build/python/bindings/ieee802_11_python.cpython-38-x86_64-linux-gnu.so /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/
  ```

  Expected: so 文件复制成功，无报错。

- [ ] **Step 4: 运行现有测试确认无回归**

  Run:
  ```bash
  cd /home/hy/gr-ieee802-11
  conda run -n gnuradio python test_mcs_end_to_end.py
  ```

  Expected: 测试通过，FCS OK 数量与之前一致。

- [ ] **Step 5: Commit**

  ```bash
  git add lib/frame_equalizer_impl.cc
  git commit -m "feat(constellation): add mcs field to symbols message meta"
  ```

---

### Task 2: 修改 wifi_phy_hier.py — 添加 constellation 消息端口

**Files:**
- Modify: `wifi_phy_hier.py`

- [ ] **Step 1: 注册 constellation 消息端口**

  在 `wifi_phy_hier.py` 的 `__init__` 方法中，找到现有消息端口注册代码：

  ```python
  self.message_port_register_hier_out("carrier")
  self.message_port_register_hier_out("mac_out")
  ```

  在其后添加：

  ```python
  self.message_port_register_hier_out("constellation")
  ```

- [ ] **Step 2: 连接 frame_equalizer symbols 到 constellation**

  找到现有消息连接：

  ```python
  self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'carrier'))
  ```

  在其后添加：

  ```python
  self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'constellation'))
  ```

  完整的消息连接部分应为：

  ```python
  self.msg_connect((self.ieee802_11_decode_mac_0, 'out'), (self, 'mac_out'))
  self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'carrier'))
  self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'constellation'))
  self.msg_connect((self, 'mac_in'), (self.ieee802_11_mapper_0, 'in'))
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add wifi_phy_hier.py
  git commit -m "feat(constellation): add constellation message port to wifi_phy_hier"
  ```

---

### Task 3: 创建顶层示例 — wifi_loopback_constellation.py

**Files:**
- Create: `examples/wifi_loopback_constellation.py`

- [ ] **Step 1: 创建文件骨架和导入**

  创建 `examples/wifi_loopback_constellation.py`：

  ```python
  #!/usr/bin/env python3
  """
  WiFi Loopback with Real-time Constellation Display and MCS Switch

  Loopback test with:
  - Real-time constellation display after frame_equalizer
  - TX MCS switching via GUI chooser
  - RX auto-adaptation of constellation display range based on detected MCS
  """
  import sys
  import os

  sys.path.insert(0, '/home/hy/gr-ieee802-11')
  sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')

  os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
  os.environ['GR_RPC_ENABLE'] = 'False'
  os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
  os.environ['GR_RPC_PORT'] = '0'
  os.environ['GR_CONTROLPORT_ON'] = 'False'

  from PyQt5 import Qt
  from gnuradio import blocks
  from gnuradio import gr
  from gnuradio import analog
  from gnuradio import qtgui
  from gnuradio import channels
  import ieee802_11
  import wifi_phy_hier
  import pmt
  import numpy as np
  ```

- [ ] **Step 2: 添加 MCS 检测 block**

  在导入部分之后添加：

  ```python
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
  ```

- [ ] **Step 3: 创建主 TopBlock 类**

  ```python
  class wifi_loopback_constellation(gr.top_block, Qt.QWidget):
      def __init__(self):
          gr.top_block.__init__(self, "WiFi Loopback + Constellation")
          Qt.QWidget.__init__(self)
          self.setWindowTitle("WiFi Loopback + Constellation Display")
          self.resize(800, 600)

          # ===== GUI Layout =====
          self.top_layout = Qt.QVBoxLayout()
          self.setLayout(self.top_layout)

          # Control panel
          self.control_layout = Qt.QHBoxLayout()
          self.top_layout.addLayout(self.control_layout)

          # MCS mapping
          self.mcs_names = [
              'BPSK 1/2 (MCS0)', 'BPSK 3/4',
              'QPSK 1/2 (MCS1)', 'QPSK 3/4 (MCS2)',
              '16QAM 1/2 (MCS3)', '16QAM 3/4 (MCS4)',
              '64QAM 2/3 (MCS5)', '64QAM 3/4 (MCS6)',
              '64QAM 5/6 (MCS7)',
          ]
          self.mcs_values = [
              ieee802_11.BPSK_1_2, ieee802_11.BPSK_3_4,
              ieee802_11.QPSK_1_2, ieee802_11.QPSK_3_4,
              ieee802_11.QAM16_1_2, ieee802_11.QAM16_3_4,
              ieee802_11.QAM64_2_3, ieee802_11.QAM64_3_4,
              ieee802_11.QAM64_5_6,
          ]

          # MCS chooser
          self.mcs_label = Qt.QLabel("TX MCS:")
          self.control_layout.addWidget(self.mcs_label)

          self.mcs_combo = Qt.QComboBox()
          self.mcs_combo.addItems(self.mcs_names)
          self.mcs_combo.currentIndexChanged.connect(self.set_mcs)
          self.control_layout.addWidget(self.mcs_combo)

          # SNR slider
          self.snr_label = Qt.QLabel("SNR (dB):")
          self.control_layout.addWidget(self.snr_label)

          self.snr_slider = Qt.QSlider(Qt.Qt.Horizontal)
          self.snr_slider.setRange(0, 30)
          self.snr_slider.setValue(20)
          self.snr_slider.valueChanged.connect(self.set_snr)
          self.control_layout.addWidget(self.snr_slider)

          self.snr_value_label = Qt.QLabel("20 dB")
          self.control_layout.addWidget(self.snr_value_label)

          # Detected MCS label
          self.rx_mcs_label = Qt.QLabel("RX MCS: --")
          self.control_layout.addWidget(self.rx_mcs_label)

          self.control_layout.addStretch(1)

          # ===== GNU Radio Blocks =====

          # WiFi PHY
          self.wifi_phy = wifi_phy_hier.wifi_phy_hier(
              bandwidth=10e6,
              chan_est=ieee802_11.LS,
              encoding=ieee802_11.BPSK_1_2,
              frequency=5.89e9,
              sensitivity=0.56
          )

          # Message strobe: send dummy packets periodically
          dummy_data = bytes([0xAA] * 100)
          dummy_pdu = pmt.cons(
              pmt.PMT_NIL,
              pmt.init_u8vector(len(dummy_data), list(dummy_data))
          )
          self.msg_strobe = blocks.message_strobe(dummy_pdu, 500)

          # MAC layer
          self.mac = ieee802_11.mac(
              [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
              [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
              [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
          )

          # Packet pad
          self.packet_pad = foo.packet_pad2(
              debug=False, delay=0, delay_sec=0.001,
              pad_front=0, pad_tail=0
          )

          # Multiply const (SNR scaling)
          self.snr = 20.0
          self.multiply_const = blocks.multiply_const_cc((10**(self.snr/10.0))**0.5)

          # Channel model
          self.channel = channels.channel_model(
              noise_voltage=1.0,
              frequency_offset=0.0,
              epsilon=1.0,
              taps=[1.0],
              noise_seed=0,
              block_tags=False
          )

          # PDU to tagged stream for constellation
          self.pdu_to_stream = blocks.pdu_to_tagged_stream(
              gr.types.complex_t, 'packet_len'
          )

          # Constellation sink
          self.constellation_sink = qtgui.const_sink_c(480, "", 1, None)
          self.constellation_sink.set_update_time(0.10)
          self.constellation_sink.set_x_axis(-2, 2)
          self.constellation_sink.set_y_axis(-2, 2)

          # Add constellation widget to layout
          constellation_widget = self.constellation_sink.qwidget()
          self.top_layout.addWidget(constellation_widget)

          # MCS detector
          self.mcs_detect = mcs_detector(self.update_constellation_range)

          # ===== Connections =====

          # Message connections
          self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
          self.msg_connect((self.mac, 'phy out'), (self.wifi_phy, 'mac_in'))
          self.msg_connect((self.wifi_phy, 'constellation'), (self.pdu_to_stream, 'pdus'))
          self.msg_connect((self.wifi_phy, 'constellation'), (self.mcs_detect, 'pdu'))

          # Stream connections (loopback)
          self.connect((self.wifi_phy, 0), (self.packet_pad, 0))
          self.connect((self.packet_pad, 0), (self.multiply_const, 0))
          self.connect((self.multiply_const, 0), (self.channel, 0))
          self.connect((self.channel, 0), (self.wifi_phy, 0))

          # Constellation stream
          self.connect((self.pdu_to_stream, 0), (self.constellation_sink, 0))

      def set_mcs(self, index):
          encoding = self.mcs_values[index]
          self.wifi_phy.set_encoding(encoding)
          print(f"[MCS] TX set to {self.mcs_names[index]} (encoding={encoding})")

      def set_snr(self, value):
          self.snr = float(value)
          self.snr_value_label.setText(f"{value} dB")
          scale = (10**(self.snr/10.0))**0.5
          self.multiply_const.set_k(scale)

      def update_constellation_range(self, mcs):
          """Adjust constellation display range based on detected MCS."""
          ranges = {
              0: (-1.5, 1.5),   # BPSK
              1: (-1.5, 1.5),   # BPSK 3/4
              2: (-1.5, 1.5),   # QPSK
              3: (-1.5, 1.5),   # QPSK 3/4
              4: (-3.0, 3.0),   # 16QAM
              5: (-3.0, 3.0),   # 16QAM 3/4
              6: (-7.0, 7.0),   # 64QAM
              7: (-7.0, 7.0),   # 64QAM 3/4
              8: (-7.0, 7.0),   # 64QAM 5/6
          }
          xmin, xmax = ranges.get(mcs, (-2, 2))
          self.constellation_sink.set_x_axis(xmin, xmax)
          self.constellation_sink.set_y_axis(xmin, xmax)
          self.rx_mcs_label.setText(f"RX MCS: {self.mcs_names[mcs]}")
          print(f"[CONSTELLATION] Auto-adapted to MCS {mcs}: range [{xmin}, {xmax}]")

      def closeEvent(self, event):
          self.stop()
          self.wait()
          event.accept()
  ```

- [ ] **Step 4: 添加 main 函数**

  在文件末尾添加：

  ```python
  def main():
      qapp = Qt.QApplication(sys.argv)
      tb = wifi_loopback_constellation()
      tb.start()
      tb.show()
      qapp.exec_()


  if __name__ == '__main__':
      main()
  ```

- [ ] **Step 5: 添加执行权限并测试启动**

  Run:
  ```bash
  chmod +x /home/hy/gr-ieee802-11/examples/wifi_loopback_constellation.py
  cd /home/hy/gr-ieee802-11
  conda run -n gnuradio python examples/wifi_loopback_constellation.py
  ```

  Expected: QT GUI 窗口正常显示，包含 MCS 选择器、SNR 滑块和星座图区域。

  Note: 由于需要在图形界面下运行，如果无图形环境可能报错。此时可用 `QT_QPA_PLATFORM=offscreen` 或确认在桌面环境下测试。

- [ ] **Step 6: Commit**

  ```bash
  git add examples/wifi_loopback_constellation.py
  git commit -m "feat(constellation): add loopback example with real-time constellation display and MCS switch"
  ```

---

### Task 4: 端到端验证

**Files:**
- Test: `examples/wifi_loopback_constellation.py`

- [ ] **Step 1: 验证 MCS 切换**

  启动示例后，在 GUI 中切换不同 MCS：
  - BPSK (MCS0): 星座图显示约 2 个点
  - QPSK (MCS1-2): 星座图显示约 4 个点
  - 16QAM (MCS3-4): 星座图显示约 16 个点
  - 64QAM (MCS5-7): 星座图显示约 64 个点

  同时观察 RX MCS 标签是否正确显示检测到的 MCS。

- [ ] **Step 2: 验证 SNR 影响**

  调整 SNR 滑块：
  - 高 SNR (30dB): 星座点集中，清晰可辨
  - 低 SNR (5dB): 星座点发散，噪声明显

- [ ] **Step 3: 确认向后兼容**

  Run:
  ```bash
  cd /home/hy/gr-ieee802-11
  conda run -n gnuradio python test_loopback_noqt.py
  ```

  Expected: 正常运行，无异常。`wifi_phy_hier` 的 `constellation` 消息端口未被连接，不影响现有功能。

- [ ] **Step 4: 最终 Commit（如有调整）**

  ```bash
  git status
  # 确认所有变更已提交
  ```

---

## Self-Review

### Spec Coverage Check

| Spec 需求 | 对应 Task / Step |
|-----------|-----------------|
| frame_equalizer symbols 添加 mcs 元信息 | Task 1, Step 1 |
| wifi_phy_hier 添加 constellation 消息端口 | Task 2, Steps 1-2 |
| 实时星座图显示 | Task 3, Steps 3-4 |
| TX MCS 切换 | Task 3, Step 3 (mcs_combo + set_mcs) |
| RX 自动适配坐标范围 | Task 3, Step 3 (mcs_detector + update_constellation_range) |
| 向后兼容 | Task 4, Step 3 |

### Placeholder Scan

- 无 "TBD" / "TODO" / "implement later"
- 所有代码块包含完整可运行的代码
- 所有文件路径精确
- 所有命令包含预期输出

### Type Consistency

- `d_frame_encoding` (int) → `pmt::from_long()` → `pmt.to_long()` (int) → Python int — 一致
- `gr.types.complex_t` 用于 `blocks.pdu_to_tagged_stream` — 正确
- `qtgui.const_sink_c(size, name, nconnections, parent)` — 与 GNU Radio 3.10 API 一致
- `mcs` 值范围 0-8 与 `Encoding` 枚举一致

### 已知限制

- 示例需要图形环境（X11/Wayland）才能显示 QT GUI。无图形环境时可用 `QT_QPA_PLATFORM=offscreen` 测试启动但无法查看星座图。
- `foo.packet_pad2` 依赖 `foo` 模块。如果环境中不可用，需要安装或替换为等效 block。
- 星座图显示的 52 个点包含 4 个 pilot 子载波（BPSK 固定点），会混在数据子载波中显示。
