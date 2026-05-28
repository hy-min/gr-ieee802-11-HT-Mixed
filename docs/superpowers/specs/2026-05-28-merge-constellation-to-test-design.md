# Design: Merge wifi_loopback_constellation.py into test_mcs_end_to_end.py

## Date: 2026-05-28

## Overview

Merge the constellation display functionality from `examples/wifi_loopback_constellation.py` into `test_mcs_end_to_end.py`, adding a `--gui` command-line flag for interactive GUI mode while preserving the existing automated batch testing as the default behavior.

## Requirements

### Default Mode (no --gui)
- Automatically test MCS 0-7 in sequence (Conv baseline + LDPC)
- Command-line output with test report
- No GUI, no user interaction required
- Suitable for CI/automation

### GUI Mode (--gui)
- Launch Qt GUI with real-time constellation display
- Manual control: user selects MCS from dropdown
- LDPC checkbox to toggle BCC/LDPC coding
- SNR slider for real-time noise level adjustment
- Auto-adapt constellation display range based on detected MCS
- Status bar showing sent/received message counts
- Window stays open until user closes it
- Terminal still outputs statistics on close

## Architecture

```
test_mcs_end_to_end.py
├── Default Mode (batch test)
│   ├── Run MCS 0-7 automatically
│   └── Print report to terminal
└── --gui Mode (interactive)
    ├── Qt GUI Window
    │   ├── Control Bar: MCS selector, LDPC checkbox, SNR slider
    │   ├── Main Area: Real-time constellation plot
    │   └── Status Bar: Message counts
    └── Manual control, no auto-sequence
```

## Components

### encoding_stripper
- Removes `encoding` and `mcs` keys from PDU metadata
- Ensures mapper uses `set_encoding()` value instead of PDU tags

### mcs_detector
- Listens on `constellation` message port
- Extracts MCS from PDU metadata
- Triggers callback to adjust constellation display range

### constellation_sink
- `qtgui.const_sink_c` for real-time constellation visualization
- Range auto-adjusted based on detected MCS:
  - BPSK/QPSK (MCS 0-3): [-1.5, 1.5]
  - 16QAM (MCS 4-5): [-3.0, 3.0]
  - 64QAM (MCS 6-8): [-7.0, 7.0]

### Status Monitor
- QTimer updates every 500ms
- Displays `msg_debug_mac.num_messages()` and `msg_debug_rx.num_messages()`

## Data Flow

```
msg_strobe → mac → encoding_stripper → wifi_phy → packet_pad → multiplier → channel → resampler → wifi_phy
                                              ↓
                                        constellation ──→ pdu_to_stream ──→ constellation_sink
                                        constellation ──→ mcs_detector ──→ range update
```

## Key Behaviors

| Feature | Default Mode | --gui Mode |
|---------|-------------|------------|
| MCS selection | Auto 0-7 | Manual dropdown |
| LDPC toggle | Fixed per test | Checkbox |
| SNR control | Fixed 30dB | Slider 0-40dB |
| Constellation | None | Real-time display |
| Test sequence | Automatic | Manual |
| Window close | N/A | Stops flowgraph, outputs stats |

## Code Structure

```python
class test_mcs_end_to_end(gr.top_block, Qt.QWidget):
    def __init__(self, gui_mode=False, ...):
        # Common blocks initialization
        if gui_mode:
            # Qt GUI setup
            # - MCS dropdown, LDPC checkbox, SNR slider
            # - constellation_sink with sip.wrapinstance
            # - Status bar with QTimer

    def set_mcs(self, index): ...
    def set_use_ldpc(self, state): ...
    def set_snr(self, value): ...
    def update_constellation_range(self, mcs): ...
    def update_status(self): ...
    def closeEvent(self, event): ...

def run_batch_test(mcs, test_params): ...

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()

    if args.gui:
        # GUI mode: manual control
        qapp = Qt.QApplication(sys.argv)
        tb = test_mcs_end_to_end(gui_mode=True)
        tb.start()
        tb.show()
        qapp.exec_()
        # Output stats on close
    else:
        # Default: automated batch test
        sys.exit(run_all_tests())
```

## Testing Plan

1. Verify default mode still passes all MCS 0-7 tests
2. Verify --gui mode launches without errors
3. Verify MCS manual switching updates constellation range
4. Verify LDPC checkbox toggles coding mode
5. Verify SNR slider affects constellation spread
6. Verify status bar updates message counts
7. Verify window close stops flowgraph cleanly
