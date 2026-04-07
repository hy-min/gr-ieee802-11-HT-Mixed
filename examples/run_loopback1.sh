#!/usr/bin/env bash
set -euo pipefail

cd /home/hy/gr-ieee802-11/examples

find . -name "*.pyc" -delete
rm -rf __pycache__

# grcc -o "$PWD" wifi_phy_hier.grc

python - <<'PY'
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

# grcc -o "$PWD" wifi_loopback1.grc

PY_BIN="${CONDA_PREFIX:-}/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python"
fi

PYTHONPATH="$PWD" "$PY_BIN" -u "$PWD/wifi_loopback1.py"
