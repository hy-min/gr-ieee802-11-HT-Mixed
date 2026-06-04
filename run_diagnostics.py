#!/home/hy/conda/envs/gnuradio/bin/python
"""
USRP 综合诊断脚本 — 自动执行多步骤诊断并收集结果
Usage: python run_diagnostics.py
"""
import subprocess
import sys
import os
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

def run_cmd(cmd, timeout=60, desc=""):
    """Run a command and return (returncode, stdout, stderr)."""
    print(f"\n{'='*60}")
    print(f"[STEP] {desc}")
    print(f"[CMD]  {cmd}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, 'LD_LIBRARY_PATH': '/home/hy/conda/envs/gnuradio/lib'}
        )
        print(result.stdout)
        if result.stderr:
            print("[STDERR]", result.stderr[:2000])
        print(f"[RESULT] returncode={result.returncode}")
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] after {timeout}s")
        return -1, "", "timeout"
    except Exception as e:
        print(f"[ERROR] {e}")
        return -1, "", str(e)

print("="*60)
print("USRP 综合诊断开始")
print("="*60)
print("前提确认：")
print("  - 两根 TX/RX 天线分别接在 Slot A 和 Slot B 的 TX/RX 端口")
print("  - 两根 RX2 天线分别接在 Slot A 和 Slot B 的 RX2 端口")
print("  - 天线之间距离 5-30cm，面向彼此")
print("="*60)

results = {}

# Step 1: 基础连通性测试（tone 信号）
results['step1_tone'] = run_cmd(
    f"{sys.executable} examples/test_usrp_diagnose3.py",
    timeout=30,
    desc="Step 1: 四方向交叉测试（tone 信号验证 FDD 连通性）"
)

# Step 2: 最小化 loopback，默认 gain
print(f"\n{'='*60}")
print("[STEP] Step 2: 最小化 loopback 测试（默认 gain: TX=10, RX=20）")
print(f"{'='*60}")
results['step2_default'] = run_cmd(
    f"{sys.executable} test_usrp_minimal_loopback.py --duration 15 --tx-gain 10 --rx-gain 20",
    timeout=30,
    desc="Step 2a: 默认 gain 测试"
)

# Step 3: 扫描 RX gain
for rx_g in [5, 15, 25, 31]:
    results[f'step3_rx{rx_g}'] = run_cmd(
        f"{sys.executable} test_usrp_minimal_loopback.py --duration 10 --tx-gain 10 --rx-gain {rx_g}",
        timeout=25,
        desc=f"Step 3: TX=10, RX={rx_g}"
    )

# Step 4: 扫描 TX gain
for tx_g in [5, 15, 25, 31]:
    results[f'step4_tx{tx_g}'] = run_cmd(
        f"{sys.executable} test_usrp_minimal_loopback.py --duration 10 --tx-gain {tx_g} --rx-gain 20",
        timeout=25,
        desc=f"Step 4: TX={tx_g}, RX=20"
    )

# Step 5: 捕获 IQ 样本（需要 TX 同时发送）
print(f"\n{'='*60}")
print("[STEP] Step 5: 同时捕获 IQ 样本")
print("  先启动 TX，再启动 RX 捕获")
print(f"{'='*60}")

# Start TX in background
print("[INFO] 启动 TX 发送...")
tx_proc = subprocess.Popen(
    [sys.executable, "test_usrp_minimal_loopback.py",
     "--duration", "20", "--tx-gain", "15", "--rx-gain", "20"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env={**os.environ, 'LD_LIBRARY_PATH': '/home/hy/conda/envs/gnuradio/lib'}
)
time.sleep(2)

# Capture RX
results['step5_capture'] = run_cmd(
    f"{sys.executable} test_usrp_rx_capture.py --duration 3 --rx-gain 20 --output /tmp/diag_capture.fc32",
    timeout=15,
    desc="Step 5: RX IQ 捕获（3秒）"
)

# Analyze
if os.path.exists('/tmp/diag_capture.fc32'):
    results['step5_analyze'] = run_cmd(
        f"{sys.executable} analyze_raw_iq.py /tmp/diag_capture.fc32",
        timeout=30,
        desc="Step 5b: IQ 离线分析"
    )

# Wait for TX to finish
try:
    tx_out, tx_err = tx_proc.communicate(timeout=25)
    print(f"\n[TX OUTPUT]\n{tx_out[-2000:] if len(tx_out) > 2000 else tx_out}")
except subprocess.TimeoutExpired:
    tx_proc.kill()
    print("[TX TIMEOUT]")

# Summary
print("\n" + "="*60)
print("诊断结果汇总")
print("="*60)
for name, (rc, out, err) in results.items():
    status = "✅" if rc == 0 else "❌"
    print(f"  {status} {name}: returncode={rc}")

print("\n" + "="*60)
print("请将上述完整输出复制给我分析")
print("="*60)
