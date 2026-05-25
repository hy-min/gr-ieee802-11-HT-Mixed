# 实时星座图显示 + MCS 切换 设计文档

## 日期
2026-05-26

## 背景

当前 `gr-ieee802-11` 项目已实现完整的 IEEE 802.11n HT-Mixed 收发链路，支持 MCS 0-7（BPSK 到 64QAM）以及 LDPC/卷积码切换。`frame_equalizer` 均衡器已有 `symbols` 消息端口输出均衡后的子载波数据，但尚未提供可视化的星座图显示功能。

本设计在现有链路基础上，新增实时星座图显示能力，并支持在发送端切换不同 MCS 调制方式，接收端星座图根据检测到的 MCS 自动调整显示参数。

## 目标

1. **实时显示均衡器后的星座图**：从 `frame_equalizer` 提取均衡后的数据子载波符号，通过 QT GUI Constellation Sink 实时显示。
2. **发送端 MCS 切换**：通过 GUI 控件在发送端切换不同 MCS（BPSK ~ 64QAM）。
3. **接收端自动适配**：星座图根据 `frame_equalizer` 检测到的 MCS 动态调整坐标范围和参考星座点。

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         顶层示例 (新)                            │
│  ┌──────────────┐                                              │
│  │ qtgui_chooser│──MCS──┐                                      │
│  │ (BPSK..64QAM)│       │                                      │
│  └──────────────┘       ▼                                      │
│              ┌─────────────────────┐                          │
│              │   wifi_phy_hier     │                          │
│              │  (修改: +constellation│                          │
│  ┌───────────│        端口)        │──── carrier msg ───┐     │
│  │           │                     │                    │     │
│  │    ┌──────┴─────────────────────┘                    │     │
│  │    │                                                 │     │
│  │    ▼ constellation msg                               │     │
│  │  ┌─────────────────────────┐                         │     │
│  │  │ pdu_to_tagged_stream    │                         │     │
│  │  │  (msg → tagged stream)  │                         │     │
│  │  └──────────┬──────────────┘                         │     │
│  │             ▼ stream                                  │     │
│  │  ┌─────────────────────────┐                         │     │
│  │  │ qtgui_const_sink_c      │                         │     │
│  │  │  (实时星座图显示)         │                         │     │
│  │  └─────────────────────────┘                         │     │
│  └──────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

## 组件设计

### 1. frame_equalizer — 添加 MCS 元信息

**文件**：`lib/frame_equalizer_impl.cc`

在 `general_work` 中发送 `symbols` 消息时，在 meta dict 中新增 `mcs` 字段：

```cpp
pmt::pmt_t meta = pmt::make_dict();
meta = pmt::dict_add(meta, pmt::mp("packet_len"), pmt::from_long(52));
meta = pmt::dict_add(meta, pmt::mp("mcs"), pmt::from_long(d_frame_encoding));  // 新增
```

这样每个 PDU 消息携带当前帧的 MCS 信息，供顶层动态调整星座图参数。

### 2. wifi_phy_hier — 添加 constellation 消息端口

**文件**：`wifi_phy_hier.py`

注册新的 hier_block 消息输出端口 `constellation`，将 `frame_equalizer` 的 `symbols` 消息同时转发到 `carrier`（现有）和 `constellation`（新增）：

```python
self.message_port_register_hier_out("constellation")
self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'constellation'))
```

`carrier` 保持原样，完全向后兼容。

### 3. 顶层示例 — 星座图显示 + MCS 切换

**新文件**：`examples/wifi_loopback_constellation.py`

核心组件：

| 组件 | 类型 | 作用 |
|------|------|------|
| `qtgui_chooser` | QT GUI | 选择 TX MCS |
| `wifi_phy_hier` | Hier Block | 现有收发链路 |
| `pdu_to_tagged_stream` | PDU → Stream | 将 constellation PDU 消息转为 tagged stream |
| `qtgui_const_sink_c` | QT GUI | 实时显示星座图 |

#### MCS 切换机制

TX 侧通过 `qtgui_chooser` 设置 `wifi_phy_hier.encoding`：

```python
self.qtgui_chooser = Qt.QComboBox()
self.qtgui_chooser.addItems(['BPSK_1/2', 'QPSK_1/2', 'QPSK_3/4', '16QAM_1/2', '16QAM_3/4', '64QAM_2/3', '64QAM_3/4', '64QAM_5/6'])
self.qtgui_chooser.currentIndexChanged.connect(self.set_mcs)

def set_mcs(self, index):
    encodings = [ieee802_11.BPSK_1_2, ieee802_11.QPSK_1_2, ...]
    self.wifi_phy_hier_0.set_encoding(encodings[index])
```

#### 星座图动态适配

RX 侧通过消息回调解析 PDU meta 中的 `mcs`，动态调整 `qtgui_const_sink_c` 的坐标范围和参考星座点：

```python
def handle_constellation_pdu(self, pdu):
    meta = pmt.car(pdu)
    mcs = pmt.to_long(pmt.dict_ref(meta, pmt.mp('mcs'), pmt.from_long(0)))
    # 根据 mcs 调整星座图参数
    self.update_constellation_display(mcs)
```

星座图参数对照表：

| MCS | 调制方式 | 坐标范围 | 参考星座点数 |
|-----|---------|---------|-------------|
| 0   | BPSK    | ±1.5    | 2           |
| 1-2 | QPSK    | ±1.5    | 4           |
| 3-4 | 16-QAM  | ±3.0    | 16          |
| 5-7 | 64-QAM  | ±7.0    | 64          |

### 4. 数据流格式

`frame_equalizer` 的 `symbols` 消息格式（每 OFDM 数据符号一次）：
- **meta**: `{"packet_len": 52, "mcs": <encoding>}`
- **data**: `c32vector(52)` — 52 个均衡后的子载波复数值（48 数据 + 4 pilot）

经过 `pdu_to_tagged_stream` 后变为 tagged stream（52 个 `gr_complex` 采样），每个采样在星座图上显示为一个点。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `lib/frame_equalizer_impl.cc` | 修改 | symbols meta 添加 `mcs` 字段（约 2333 行） |
| `wifi_phy_hier.py` | 修改 | 添加 `constellation` 消息端口 |
| `examples/wifi_loopback_constellation.py` | 新增 | 带星座图显示和 MCS 切换的顶层示例 |

## 测试计划

1. 运行 `wifi_loopback_constellation.py`，确认 GUI 正常启动
2. 切换不同 MCS，观察星座图点数变化（BPSK→2点，QPSK→4点，16QAM→16点，64QAM→64点）
3. 验证 TX 和 RX 调制方式一致（通过星座图分布判断）
4. 确认现有示例（不连接 constellation 端口）不受影响

## 风险与回退

- **向后兼容**：`constellation` 是新增的消息端口，现有代码不连接它，无影响
- **C++ 修改范围**：仅在 meta dict 中添加一个字段，风险极低
- **回退**：删除 `examples/wifi_loopback_constellation.py` 即可完全回退，不影响核心链路
