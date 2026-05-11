# Constellation 单点问题修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans

**Goal:** 修复星座图只显示一个原点的 bug，让 BPSK 双峰清晰可见

**Architecture:** 问题根因是 Tag 触发机制配置错误。constellation sink 使用 `TRIG_MODE_TAG` + `tr_tag_key="eq_syms"`，但 `add_item_tag` 添加的 tag 没有正确触发显示。需要先用 `TRIG_MODE_FREE` 验证数据流，再用正确配置实现 tag 触发。

**Tech Stack:** GNU Radio Python, QTGUI Constellation Sink, PMT message passing

---

## 问题分析

当前 `wifi_constellation_eqsyms.py` 配置：
- `const_sink` 触发模式：`TRIG_MODE_TAG`，tr_tag_key="eq_syms"
- `EqSymsToStream` 在 `handle_msg` 中调用 `add_item_tag` 添加 tag
- 问题：`add_item_tag` 添加的 tag 可能无法正确触发 QTGUI 的 tag 机制

---

## 修改文件

- Modify: `examples/wifi_constellation_eqsyms.py:121` - 改用 TRIG_MODE_FREE 验证数据流
- Modify: `examples/wifi_constellation_eqsyms.py:113-121` - 正确配置 tag 触发

---

### Task 1: 用 TRIG_MODE_FREE 验证数据流

**Files:**
- Modify: `examples/wifi_constellation_eqsyms.py:121`

- [ ] **Step 1: 修改触发模式为 TRIG_MODE_FREE**

找到第 121 行：
```python
constellation_sink.set_trigger_mode(qtgui.TRIG_MODE_TAG, qtgui.TRIG_SLOPE_POS, 0, 0, "eq_syms")
```

替换为：
```python
constellation_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0, 0, "")
```

- [ ] **Step 2: 运行测试验证数据流**

Run:
```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 15 python3 examples/wifi_constellation_eqsyms.py 2>&1 | grep -E "(TX|Error|EqSyms)" | head -20
```

Expected: 看到 TX 发送消息，无 Error

- [ ] **Step 3: 观察星座图**

如果数据流正常，应该看到：
- 有杂散的底噪点（TRIG_MODE_FREE 会显示所有采样）
- 或者多个 BPSK 点簇（如果数据正确流动）

如果还是只有一个点，说明数据流本身有问题。

---

### Task 2: 检查并修复 EqSymsToStream 的 Tag 添加机制

**Files:**
- Modify: `examples/wifi_constellation_eqsyms.py:68` - 移除 add_item_tag 调用

- [ ] **Step 1: 移除 add_item_tag 调用**

找到第 68 行：
```python
self.add_item_tag(0, self.nitems_written(0), pmt.intern("eq_syms"), pmt.from_long(1))
```

替换为：
```python
# Tag added separately in work() after producing output
pass
```

- [ ] **Step 2: 在 work() 中正确添加 tag**

找到 work() 函数（约第 70-81 行），修改为：
```python
def work(self, input_items, output_items):
    out = output_items[0]
    produced = 0
    while self.eq_buffer and produced < len(out):
        val = self.eq_buffer.pop(0)
        out[produced] = val
        self.last_symbol = val
        # Add tag when we output the first sample of a burst
        if produced == 0:
            self.add_item_tag(0, self.nitems_written(0), pmt.intern("packet_len"), pmt.from_long(52))
        produced += 1
    if produced == 0 and len(out) > 0:
        out[0] = self.last_symbol
        produced = 1
    return produced
```

- [ ] **Step 3: 改回 TRIG_MODE_TAG 配置**

第 121 行改为：
```python
constellation_sink.set_trigger_mode(qtgui.TRIG_MODE_TAG, qtgui.TRIG_SLOPE_POS, 0, 0, "packet_len")
```

---

### Task 3: 最终验证

- [ ] **Step 1: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 15 python3 examples/wifi_constellation_eqsyms.py 2>&1 | head -30
```

- [ ] **Step 2: 观察星座图**

预期结果：
- MCS 0 (BPSK)：看到左右两个点簇（实部约 ±1）
- 无原点孤立点（或只有零星底噪）

---

## 备选方案（如果上述方案无效）

如果 Task 1 发现数据流本身有问题，改用更简单的方案：

**使用 blocks_message_debug 辅助诊断：**
1. 在 `msg_connect` 后添加 debug 探针确认消息到达
2. 使用 `blocks.file_sink` 临时保存数据验证

**直接使用 wifi_loopback.py：**
wifi_loopback.py 中的 `EqSymsProbe` 已经验证可用，直接使用它进行测试。

---

## 关键代码位置

| 位置 | 内容 |
|------|------|
| `wifi_constellation_eqsyms.py:68` | `add_item_tag` 调用 |
| `wifi_constellation_eqsyms.py:70-81` | `work()` 函数 |
| `wifi_constellation_eqsyms.py:121` | `set_trigger_mode` 调用 |

