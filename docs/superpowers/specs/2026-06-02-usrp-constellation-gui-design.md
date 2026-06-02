# USRP X310 硬件测试 + 星座图 GUI 设计文档

**Date:** 2026-06-02  
**Branch:** TEST1 (based on maint-3.10)  
**Scope:** 将纯仿真 loopback 测试扩展到 USRP X310 硬件，并集成实时星座图 GUI  

---

## 1. Context & Goal

### Current State
- 纯仿真测试 `test_mcs_end_to_end.py` 9/9 全部通过（MCS0 Conv + MCS0-7 LDPC）
- 星座图 GUI `wifi_loopback_constellation.py` 支持 loopback 模式下的实时星座显示 + MCS 切换
- `wifi_phy_hier` 是硬件无关的分层块，包含完整的 TX/RX PHY 链
- 现有 GRC 文件（`wifi_tx.grc`, `wifi_rx.grc`, `wifi_transceiver.grc`）包含 UHD 块，但不可用于代码生成（会导致段错误）

### Target State
- 新建 `test_mcs_usrp.py`：使用 USRP X310 的 Radio 0 (TX) 和 Radio 1 (RX) 进行自发自收
- 集成 PyQt5 GUI：实时星座图显示、MCS 切换、增益调节、频谱监控
- 复用已有的 `wifi_phy_hier`、`mcs_detector`、`encoding_stripper` 等组件
- 支持命令行参数化配置（频率、增益、MCS、LDPC 开关等）

### Hardware Configuration
| Parameter | Value |
|-----------|-------|
| Device | USRP X310 (FW 6.1, FPGA 39.2, RFNoC) |
| Daughterboards | Dual UBX-160 v2 (Radio 0 + Radio 1) |
| Connection | 1 GigE (IP: 192.168.10.2) |
| Frequency Range | 10 MHz - 6 GHz |
| Gain Range | 0-31.5 dB (0.5 dB step) |
| Clock Sources | internal / external / gpsdo |
| TX Subdev | A:0 (Radio 0) |
| RX Subdev | B:0 (Radio 1) |
| TX Antenna | TX/RX |
| RX Antenna | RX2 |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PyQt5 GUI Window                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Control Panel: MCS chooser | LDPC toggle | TX Gain | RX Gain | Freq │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────┐  ┌──────────────────────────┐                │
│  │    Constellation Plot    │  │      Spectrum Plot       │                │
│  │  (RX after equalizer)    │  │   (RX baseband)          │                │
│  └──────────────────────────┘  └──────────────────────────┘                │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Status Bar: FCS pass/fail | Frame count | RSSI | Detected MCS       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GNU Radio Flowgraph                                │
│                                                                              │
│  TX Path:                                                                    │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
│  │msg_strobe│──▶│   mac    │──▶│stripper  │──▶│wifi_phy  │──▶│mult_const│  │
│  └──────────┘   └──────────┘   └──────────┘   │ (TX)     │   └────┬─────┘  │
│                                               └──────────┘        │        │
│                                                                    ▼        │
│                                                               ┌──────────┐  │
│                                                               │uhd_usrp  │  │
│                                                               │_sink    │  │
│                                                               │Radio 0  │  │
│                                                               └────┬─────┘  │
│                                                                    │        │
│                                                           空中/线缆/衰减器   │
│                                                                    │        │
│                                                               ┌────┴─────┐  │
│                                                               │uhd_usrp  │  │
│                                                               │_source  │  │
│                                                               │Radio 1  │  │
│                                                               └────┬─────┘  │
│                                                                    │        │
│                                                                    ▼        │
│                                                               ┌──────────┐  │
│                                                               │wifi_phy  │  │
│                                                               │ (RX)     │  │
│                                                               └────┬─────┘  │
│                                                                    │        │
│                   ┌────────────────────────────────────────────────┤        │
│                   │                                                │        │
│                   ▼                                                ▼        │
│            ┌──────────┐                                    ┌──────────┐     │
│            │msg_debug │                                    │constell. │     │
│            │  (FCS)   │                                    │  sink    │     │
│            └──────────┘                                    └──────────┘     │
│                                                                              │
│  Spectrum Path (branch from RX):                                             │
│  uhd_usrp_source ──▶ freq_xlating_fir_filter ──▶ qtgui_freq_sink            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Design

### 3.1 UHD Sink (TX)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `device_addr` | `"addr=192.168.10.2"` | USRP IP address |
| `stream_args` | `""` | Default stream args |
| `otw_format` | `"sc16"` | 16-bit complex samples |
| `channels` | `[0]` | Single TX channel |
| `sample_rate` | `20e6` | 20 MHz (802.11n HT20) |
| `center_freq` | `2.437e9` | Channel 6, 2.4 GHz ISM (CLI configurable) |
| `gain` | `10.0` | TX gain in dB (CLI configurable) |
| `antenna` | `"TX/RX"` | UBX TX/RX port |
| `subdev_spec` | `"A:0"` | Radio 0, frontend 0 |

**Notes:**
- Same X310's two radios share the same internal reference clock → no external ref needed for basic testing
- TX gain starts low (10 dB) for close-range testing to avoid saturating RX

### 3.2 UHD Source (RX)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `device_addr` | `"addr=192.168.10.2"` | Same USRP |
| `stream_args` | `""` | Default |
| `otw_format` | `"sc16"` | 16-bit complex samples |
| `channels` | `[0]` | Single RX channel |
| `sample_rate` | `20e6` | Must match TX |
| `center_freq` | `2.437e9` | Must match TX |
| `gain` | `20.0` | RX gain in dB (CLI configurable) |
| `antenna` | `"RX2"` | UBX RX2 port (isolated from TX) |
| `subdev_spec` | `"B:0"` | Radio 1, frontend 0 |
| `bw` | `20e6` | RX baseband filter bandwidth |

**Notes:**
- Using RX2 antenna port instead of TX/RX avoids TX leakage when doing full-duplex on same radio
- But since we're using Radio 1 for RX, TX/RX port could also work; RX2 is cleaner

### 3.3 GUI Components (Reused from `wifi_loopback_constellation.py`)

| Component | Source | Adaptation |
|-----------|--------|------------|
| `mcs_detector` | `wifi_loopback_constellation.py` | No change |
| `encoding_stripper` | `wifi_loopback_constellation.py` | No change |
| `mcs_combo` (QComboBox) | `wifi_loopback_constellation.py` | No change |
| `ldpc_check` (QCheckBox) | `wifi_loopback_constellation.py` | No change |
| `constellation_sink` (qtgui.const_sink_c) | `wifi_loopback_constellation.py` | No change |
| `update_constellation_range` | `wifi_loopback_constellation.py` | No change |

**New GUI Components:**

| Component | Type | Purpose |
|-----------|------|---------|
| `freq_input` | QLineEdit + QPushButton | Center frequency input (MHz) |
| `tx_gain_slider` | QSlider (0-31) | TX gain control |
| `rx_gain_slider` | QSlider (0-31) | RX gain control |
| `freq_sink` | qtgui.freq_sink_c | **Real-time spectrum display** |
| `status_bar` | QLabel | FCS count, frame rate, RSSI |
| `start_stop_btn` | QPushButton | Start/stop USRP streaming |

### 3.4 Spectrum Display Design

The spectrum display uses `qtgui.freq_sink_c` connected directly to the `uhd_usrp_source` output to show the raw RX baseband spectrum in real-time.

**Spectrum Sink Parameters:**

| Parameter | Value | Description |
|-----------|-------|-------------|
| `fft_size` | 1024 | Frequency resolution |
| `sample_rate` | 20e6 | Match USRP sample rate |
| `center_freq` | 2.437e9 | Match USRP center frequency |
| `bandwidth` | 20e6 | Display bandwidth |
| `name` | `"RX Spectrum"` | Plot title |
| `nconnections` | 1 | Single input |

**Layout:**
- The spectrum plot occupies the **right half** of the bottom panel
- The constellation plot occupies the **left half**
- Both plots update at 10 Hz (`set_update_time(0.10)`)

**Visual Features:**
- X-axis: Frequency offset from center (±10 MHz)
- Y-axis: Power spectral density (dB)
- Expected visual: 20 MHz wide OFDM spectrum with flat-top shape and ~6 dB roll-off at edges
- Averaging: Enable `set_average(True)` with `set_avg_alpha(0.5)` for smoother display

**Connection:**
```
uhd_usrp_source ──▶ blocks.stream_to_vector (optional) ──▶ qtgui.freq_sink_c
```

Since `freq_sink_c` accepts a stream input directly, no additional conversion blocks are needed.

### 3.4 Removed from Loopback Version

| Block | Reason |
|-------|--------|
| `channels.channel_model` | Real air channel replaces simulated AWGN |
| `pfb.arb_resampler_ccf` | No timing offset in hardware loopback |
| `multiply_const_cc` (SNR scaling) | Hardware gain control replaces SNR simulation |
| `snr_slider` | Replaced by TX/RX gain sliders |

---

## 4. Data Flow

### 4.1 TX Frame Flow

```
msg_strobe (1 Hz) → mac (PDU framing) → encoding_stripper
                                               │
                                               ▼
                                    wifi_phy_hier (TX path)
                                               │
                                               ▼
                                    uhd_usrp_sink (Radio 0 TX)
                                               │
                                               ▼
                                           Antenna
```

### 4.2 RX Frame Flow

```
Antenna → uhd_usrp_source (Radio 1 RX) → wifi_phy_hier (RX path)
                                                │
                        ┌───────────────────────┼───────────────────────┐
                        │                       │                       │
                        ▼                       ▼                       ▼
                  constellation           msg_debug_rx            mcs_detector
                  (qtgui plot)            (FCS counter)           (MCS detection)
```

### 4.3 Spectrum Monitor Flow

```
uhd_usrp_source → blocks.throttle (optional) → qtgui.freq_sink_c
```

---

## 5. Key Parameters

### 5.1 802.11 PHY Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Sample Rate | 20 Msps | 802.11n HT20 standard |
| FFT Size | 64 | Standard OFDM |
| CP Length | 16 | Standard 802.11 |
| Bandwidth | 20 MHz | HT20 mode |
| Subcarriers | 52 data + 4 pilot = 56 | HT data (vs 48 legacy) |
| `wifi_phy_hier.bandwidth` | `10e6` | Must keep this value (project convention, not actual BW) |
| `wifi_phy_hier.frequency` | `5.89e9` | Project convention (not actual center freq) |

### 5.2 USRP Parameters

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| Center Freq | 2437 MHz | 10-6000 MHz | Wi-Fi Ch 6 (2.4 GHz) |
| TX Gain | 10 dB | 0-31.5 dB | Start low for close range |
| RX Gain | 20 dB | 0-31.5 dB | Adjust based on signal strength |
| TX Subdev | A:0 | - | Radio 0 |
| RX Subdev | B:0 | - | Radio 1 |

### 5.3 Frame Timing

| Parameter | Value |
|-----------|-------|
| Frame interval | 1000 ms (1 Hz) |
| Packet size | 10 bytes payload |
| Preamble duration | ~20 μs (L-STF + L-LTF + HT-SIG + HT-STF + HT-LTF) |
| Data duration | Variable (depends on MCS and packet size) |

---

## 6. Implementation Steps

### Phase 1: Environment Setup
1. Increase UDP socket buffer: `sudo sysctl -w net.core.wmem_max=33554432`
2. Verify UHD Python API: `python -c "import uhd; print(uhd.get_version_string())"`
3. Test basic TX: `uhd_fft --args addr=192.168.10.2`
4. Test basic RX: `uhd_fft` on RX channel

### Phase 2: Create Base USRP Script
1. Create `examples/test_mcs_usrp.py`
2. Add UHD sink (Radio 0) and UHD source (Radio 1)
3. Connect TX path: msg_strobe → mac → stripper → wifi_phy → uhd_sink
4. Connect RX path: uhd_source → wifi_phy → msg_debug
5. Verify frames flow through without GUI

### Phase 3: Add GUI Framework
1. Port GUI components from `wifi_loopback_constellation.py`
2. Add PyQt5 window layout
3. Add constellation sink
4. Add MCS chooser + LDPC toggle

### Phase 4: Add Hardware Controls
1. Add TX gain slider (0-31 dB)
2. Add RX gain slider (0-31 dB)
3. Add center frequency input (MHz)
4. Add start/stop streaming button

### Phase 5: Add Spectrum Monitor
1. Add `qtgui.freq_sink_c` for real-time spectrum
2. Branch from uhd_source output
3. Configure frequency range (±10 MHz around center)

### Phase 6: Add Status Display
1. Add FCS pass/fail counter
2. Add frame rate indicator
3. Add detected MCS label
4. Add RSSI/signal strength indicator

### Phase 7: Validation
1. **MCS0 (BPSK 1/2)**: Verify basic TX/RX链路
2. **MCS1-4 (QPSK/16QAM)**: Test higher-order modulation
3. **MCS5-7 (64QAM)**: Test highest rates
4. **LDPC vs BCC**: Toggle and compare
5. **Distance test**: Vary antenna distance, adjust gains
6. **Frequency sweep**: Test different 2.4 GHz channels

---

## 7. Error Handling

| Scenario | Handling |
|----------|----------|
| USRP not connected | Graceful exit with error message |
| UHD buffer overflow | Print warning, continue with dropped samples |
| UHD buffer underflow | Print warning, reduce frame rate |
| LO not locked | Wait for lock timeout, abort if failed |
| No frames received | Display "No signal" in status bar |
| FCS failures | Display failure rate, suggest gain adjustment |

---

## 8. Verification Criteria

| Test | Pass Criteria |
|------|---------------|
| MCS0 BPSK Conv | ≥ 80% FCS OK |
| MCS0 BPSK LDPC | ≥ 80% FCS OK |
| MCS7 64QAM Conv | ≥ 50% FCS OK (expected lower due to SNR) |
| MCS7 64QAM LDPC | ≥ 50% FCS OK |
| GUI Constellation | Clear constellation points visible |
| GUI Spectrum | OFDM spectrum visible with 20 MHz BW |
| Gain adjustment | RX gain slider affects signal strength |
| Frequency change | Center freq change updates both TX and RX |

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| 1 GigE bandwidth insufficient for 20 Msps | Medium | High | Use smaller frame rate, or upgrade to 10 GigE |
| TX leakage saturates RX | Medium | High | Use RX2 port, keep TX gain low, use attenuator |
| CFO not compensated | Low | Medium | sync_long has CFO estimation; verify it works on real signals |
| Timing offset | Low | Medium | sync_long handles timing; pfb resampler not needed |
| UHD API version mismatch | Low | High | Test UHD import before running |
| GUI crashes with USRP streaming | Low | High | Use separate thread for USRP, Qt for GUI |

---

## 10. Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `examples/test_mcs_usrp.py` | **Create** | Main USRP + GUI test script |
| `examples/wifi_loopback_constellation.py` | Reference | Copy GUI components from here |
| `wifi_phy_hier.py` | No change | Reuse as-is |
| `test_mcs_end_to_end.py` | No change | Keep for regression testing |

---

## 11. Command Line Interface

```bash
# Basic usage
python test_mcs_usrp.py

# With parameters
python test_mcs_usrp.py --freq 2437 --tx-gain 15 --rx-gain 25 --mcs 0 --ldpc

# 5 GHz test
python test_mcs_usrp.py --freq 5180 --tx-gain 20 --rx-gain 30 --mcs 7
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--freq` | 2437 | Center frequency in MHz |
| `--tx-gain` | 10 | TX gain in dB |
| `--rx-gain` | 20 | RX gain in dB |
| `--mcs` | 0 | Initial MCS (0-7) |
| `--ldpc` | False | Enable LDPC coding |
| `--rate` | 20 | Sample rate in MHz |
| `--interval` | 1000 | Frame interval in ms |
