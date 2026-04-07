#!/usr/bin/env bash
set -euo pipefail

cd /home/hy/gr-ieee802-11/examples

# 使用 gnuradio conda 环境
PY_BIN="/home/hy/conda/envs/gnuradio/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
    echo "错误: gnuradio conda 环境不存在"
    exit 1
fi

echo "使用 Python: $PY_BIN"

# 设置 PYTHONPATH
export PYTHONPATH="/home/hy/gr-ieee802-11/examples:${PYTHONPATH:-}"

# 清理旧的 pyc 文件
find . -name "*.pyc" -delete
rm -rf __pycache__

# 检查 wifi_phy_hier.py 是否存在
if [[ ! -f "wifi_phy_hier.py" ]]; then
    echo "错误: wifi_phy_hier.py 不存在"
    exit 1
fi

# 应用端口修复（如果 needed）
python3 - <<'PY'
from pathlib import Path

p = Path("wifi_phy_hier.py")
if not p.exists():
    raise SystemExit("ERROR: wifi_phy_hier.py was not generated")

s = p.read_text()

# 通用修复：把 grcc 给 HT Header block 生成的字符串端口名改成整数端口 0
s = s.replace("(self.mywifi_ht_header_tagged_0, 'in')",
              "(self.mywifi_ht_header_tagged_0, 0)")
s = s.replace("(self.mywifi_ht_header_tagged_0, 'out')",
              "(self.mywifi_ht_header_tagged_0, 0)")

p.write_text(s)

# 硬检查：如果还残留字符串端口，直接报错退出
s2 = p.read_text()
bad = [
    "(self.mywifi_ht_header_tagged_0, 'in')",
    "(self.mywifi_ht_header_tagged_0, 'out')",
]
left = [x for x in bad if x in s2]
if left:
    raise SystemExit(f"ERROR: unpatched header block ports remain: {left}")

print("patched wifi_phy_hier.py")
PY

echo "运行测试..."
echo "=== 期待看到 [SYNC-SHORT], [SYNC-LONG], [EQ] 等调试输出 ==="
echo "=== 如果没有任何输出，说明同步块未被调用 ==="
echo ""

# 运行简化的测试：只创建流图，不启动 GUI
"$PY_BIN" -u -c "
import sys
import time
from gnuradio import gr
from wifi_phy_hier import wifi_phy_hier
from gnuradio import blocks

print('创建流图...')
tb = gr.top_block()

print('创建 wifi_phy_hier 实例 (sensitivity=0.01)...')
phy = wifi_phy_hier(sensitivity=0.01)

print('创建虚拟信号源...')
# 简单的源：零向量（无信号，但用于测试）
src = blocks.vector_source_c([0]*1000, True)

print('创建空接收器...')
sink = blocks.null_sink(gr.sizeof_gr_complex*1)

print('连接流图...')
tb.connect(src, phy)
tb.connect(phy, sink)

print('启动流图（运行3秒）...')
tb.start()
time.sleep(3)
print('停止流图...')
tb.stop()
tb.wait()
print('测试完成')
" 2>&1 | grep -E "(SYNC|general_work|Frame detected|Received wifi_start|\[EQ\])" || true

echo ""
echo "=== 原始输出结束 ==="
echo "如果没有看到调试输出，说明："
echo "1. 同步块未被调用（没有数据流过）"
echo "2. 修改的库未被加载"
echo "3. 阈值设置过高"