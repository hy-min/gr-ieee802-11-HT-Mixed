#!/usr/bin/env python3
"""
p178_cosr_mix.py — Co-SR 阶段 0：离线混合 PHY 可解码性地图。

流程：
  1. 从 --tx-capture 波形提取单帧（自相关定帧周期，帧内同内容）
  2. 数字混合：C = A + 10^(-SIR/20)·B(t-τ)，重复 N_REPS 次加 2ms 零间隔
  3. 逐配置 --rx-file 回放进实时解码链，按 len 归属计数 FCS_OK
     （A: len38→PSDU 66；B: len61→PSDU 89）
  4. 输出 SIR × τ × {A,B} 可解码性表

用法：
  python3 p178_cosr_mix.py build            # 生成混合文件
  python3 p178_cosr_mix.py run              # 回放全部 + 汇总表
"""
import numpy as np
import os
import re
import subprocess
import sys
from pathlib import Path

CAP = Path('/home/hy/captures')
FILE_A = CAP / 'p178_tx_mcs0_len38.fc32'   # victim:  MCS 0, len 38 -> PSDU 66
FILE_B = CAP / 'p178_tx_mcs0_len61.fc32'   # interferer: MCS 0, len 61 -> PSDU 89
PITCH_A, PITCH_B = 2481, 3041              # 帧周期（含 1 分隔样本），自相关实测
FRAME_A, FRAME_B = 2480, 3040              # 实际帧长（样本）
LEN_A_LOG, LEN_B_LOG = 66, 89              # DECODE_SUCCESS 行里的 len 归属标记
RATE = 20e6
GAP = 40000                                # 2ms 零间隔（sync 复位）
N_REPS = 50
WARMUP_NOISE = 2_000_000                   # 100ms 纯噪声前缀（吸收 sync warmup）
TARGET_RMS = 0.315                         # 链输入目标 RMS（校准自 p150 可解码捕获）
NOISE_RMS = 0.0186                         # 噪声地板 RMS（p150 实测——纯零间隙会漂移
                                           # sync 触发定时，必须给真实噪声床）
OUTDIR = CAP / 'p178_cosr_mix'

TAUS = [0, 400, 1200, 2480]                # 样本：0/20/60/124 µs
SIRS = [-20, -10, -6, -3, 0, 3, 6, 10, 20]  # dB, 相对 A/B 功率比（负=B 更强）


def extract_frame(path, pitch, frame_len, k=10):
    """取第 k 帧。帧首 = L-STF 起点，用 lag-16 周期相关定位（L-STF = 16 样本
    周期 ×10 重复）——不能用能量前沿：MCS1 pad 帧的 L-STF 幅度低于数据段峰值，
    能量阈值会切到帧中间（P178b 实测教训）。"""
    x = np.fromfile(path, dtype=np.complex64)
    seg = x[k * pitch:(k + 2) * pitch].copy()   # 取 2 个 pitch 防止截断
    # lag-16 周期相关找 L-STF 起点
    T = 64
    best_n, best_s = 0, -1.0
    for n in range(0, min(pitch, len(seg) - 16 - T)):
        a = seg[n:n + T]
        b = seg[n + 16:n + 16 + T]
        denom = np.sqrt((np.abs(a) ** 2).sum() * (np.abs(b) ** 2).sum())
        s = np.abs((a * np.conj(b)).sum()) / denom if denom > 0 else 0.0
        if s > best_s:
            best_s, best_n = s, n
    frame = np.zeros(frame_len, dtype=np.complex64)
    avail = seg[best_n:best_n + frame_len]
    frame[:len(avail)] = avail
    return frame


def build_pair(tag, file_a, pitch_a, frame_a, file_b, pitch_b, frame_b,
               taus=(0,), sirs=SIRS, randomize_phase=True):
    """MCS 参数化构建（阶段 0.5+）：默认相位随机化（P178 教训：固定位相产生假凹陷）。
    输出到 OUTDIR/<tag>/。返回配置数。"""
    outdir = OUTDIR / tag
    outdir.mkdir(parents=True, exist_ok=True)
    A = extract_frame(file_a, pitch_a, frame_a)
    B = extract_frame(file_b, pitch_b, frame_b)
    A /= A.std()
    B /= B.std()
    np.save(outdir / 'frame_A.npy', A)
    np.save(outdir / 'frame_B.npy', B)

    configs = [('baselineA', None, None), ('baselineB', None, None)]
    for tau in taus:
        for sir in sirs:
            configs.append((f'tau{tau}_sir{sir}', tau, sir))

    for name, tau, sir in configs:
        rng = np.random.default_rng(abs(hash(f'{tag}/{name}')) & 0xffffffff)
        if tau is None:
            only_b = (name == 'baselineB')
            span = frame_b if only_b else frame_a
            chunks = [np.zeros(WARMUP_NOISE, np.complex64)]
            for _ in range(N_REPS):
                pair = np.zeros(span, dtype=np.complex64)
                pair[:span] = (B if only_b else A) * TARGET_RMS
                chunks.append(np.zeros(GAP, np.complex64))
                chunks.append(pair)
        else:
            span = max(frame_a, tau + frame_b)
            chunks = [np.zeros(WARMUP_NOISE, np.complex64)]
            for _ in range(N_REPS):
                pair = np.zeros(span, dtype=np.complex64)
                pair[:frame_a] = A * TARGET_RMS
                g = TARGET_RMS * 10 ** (-sir / 20.0)
                if randomize_phase:
                    phi = rng.uniform(0, 2 * np.pi)
                    pair[tau:tau + frame_b] += B * g * np.exp(1j * phi)
                else:
                    pair[tau:tau + frame_b] += B * g
                chunks.append(np.zeros(GAP, np.complex64))
                chunks.append(pair)
        stream = np.concatenate(chunks)
        stream = stream + (rng.standard_normal(len(stream)) +
                           1j * rng.standard_normal(len(stream))) * (NOISE_RMS / np.sqrt(2))
        stream.astype(np.complex64).tofile(outdir / f'{name}.fc32')
    print(f'[BUILD2] {tag}: {len(configs)} 个配置 -> {outdir}')


def run_tag(tag, len_a_log, len_b_log):
    """回放 OUTDIR/<tag>/ 下全部配置并按 len 归属计数。"""
    outdir = OUTDIR / tag
    logdir = outdir / 'logs'
    logdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(outdir.glob('*.fc32')):
        ok_a, ok_b = replay(f, logdir, len_a_log, len_b_log)
        rows.append((f.stem, ok_a, ok_b))
        print(f'[REPLAY] {tag}/{f.stem}: A={ok_a}/{N_REPS} B={ok_b}/{N_REPS}',
              flush=True)
    print(f'\n===== {tag} 可解码性表 =====')
    print(f'{"config":24s}  A_ok/{N_REPS}  B_ok/{N_REPS}')
    for name, a, b in rows:
        print(f'{name:24s}  {a:6d}  {b:6d}')


def build():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    A = extract_frame(FILE_A, PITCH_A, FRAME_A)
    B = extract_frame(FILE_B, PITCH_B, FRAME_B)
    A /= A.std()   # RMS=1
    B /= B.std()
    np.save(OUTDIR / 'frame_A.npy', A)
    np.save(OUTDIR / 'frame_B.npy', B)

    configs = [('baselineA', None, None), ('baselineB', None, None)]
    for tau in TAUS:
        for sir in SIRS:
            configs.append((f'tau{tau}_sir{sir}', tau, sir))

    for name, tau, sir in configs:
        if tau is None:
            # baselineA = 纯 A；baselineB = 纯 B
            only_b = (name == 'baselineB')
            span = FRAME_B if only_b else FRAME_A
            pair = np.zeros(span, dtype=np.complex64)
            pair[:span] = (B if only_b else A) * TARGET_RMS
        else:
            span = FRAME_A + abs(tau) + FRAME_B
            pair = np.zeros(span, dtype=np.complex64)
            pair[:FRAME_A] = A * TARGET_RMS          # A 在原点
            g_b = 10 ** (-sir / 20.0)
            pair[tau:tau + FRAME_B] += B * TARGET_RMS * g_b
        stream = np.tile(np.concatenate([np.zeros(GAP, np.complex64), pair]),
                         N_REPS)
        # 前补 100ms 零段（与噪声床一起构成 sync warmup 区，帧全部落在稳态段）
        stream = np.concatenate([np.zeros(WARMUP_NOISE, np.complex64), stream])
        # 全流加复高斯噪声床（I/Q 各 NOISE_RMS/√2）——sync 的自适应阈值在
        # 零噪声床上触发定时会漂移（P89/P159 的阈值逻辑按真实噪声整定）。
        rng = np.random.default_rng(abs(hash(name)) & 0xffffffff)
        stream = stream + (rng.standard_normal(len(stream)) +
                           1j * rng.standard_normal(len(stream))) * (NOISE_RMS / np.sqrt(2))
        out = OUTDIR / f'{name}.fc32'
        stream.astype(np.complex64).tofile(out)
    print(f'[BUILD] {len(configs)} 个配置 -> {OUTDIR}')


def replay(path, logdir, len_a_log=None, len_b_log=None):
    """经 P145c 验证的 e2e hier RX 回放（P171 已证 harness --rx-file 在此用途
    被证伪——splitter 处理噪声段）。stderr 里按 len 归属统计 DECODE_SUCCESS。"""
    len_a_log = LEN_A_LOG if len_a_log is None else len_a_log
    len_b_log = LEN_B_LOG if len_b_log is None else len_b_log
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    env['LD_PRELOAD'] = '/home/hy/gr-ieee802-11/wrap_rpc2.so'
    env['PYTHONPATH'] = ('/home/hy/gr-ieee802-11/build/python/bindings:'
                         '/home/hy/gr-ieee802-11/python:/home/hy/gr-ieee802-11/examples')
    cmd = ['/home/hy/conda/envs/gnuradio/bin/python',
           '/home/hy/gr-ieee802-11/examples/test_file_replay_e2e.py',
           '--phase', 'rx', '--iq-file', str(path), '--rx-duration', '8']
    proc = subprocess.run(cmd, cwd='/home/hy/gr-ieee802-11',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=300, env=env)
    (logdir / (path.stem + '.log')).write_text((proc.stdout or '') + '\n=== STDERR ===\n'
                                               + (proc.stderr or ''))
    combined = (proc.stdout or '') + '\n' + (proc.stderr or '')
    ok_a = len(re.findall(rf'DECODE_SUCCESS.*len={len_a_log}\b', combined))
    ok_b = len(re.findall(rf'DECODE_SUCCESS.*len={len_b_log}\b', combined))
    return ok_a, ok_b


def run():
    logdir = OUTDIR / 'logs'
    logdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(OUTDIR.glob('*.fc32')):
        ok_a, ok_b = replay(f, logdir)
        rows.append((f.stem, ok_a, ok_b))
        print(f'[REPLAY] {f.stem}: A={ok_a}/{N_REPS} B={ok_b}/{N_REPS}',
              flush=True)
    print('\n===== Co-SR 阶段0 可解码性表 =====')
    print(f'{"config":24s}  A_ok/{N_REPS}  B_ok/{N_REPS}')
    for name, a, b in rows:
        print(f'{name:24s}  {a:6d}  {b:6d}')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'build':
        build()
    elif len(sys.argv) > 1 and sys.argv[1] == 'run':
        run()
    elif len(sys.argv) > 1 and sys.argv[1] == 'build2':
        # build2 <tag> <fileA> <pitchA> <frameA> <fileB> <pitchB> <frameB>
        _t, fa, pa, ffa, fb, pb, ffb = sys.argv[2:9]
        build_pair(_t, fa, int(pa), int(ffa), fb, int(pb), int(ffb))
    elif len(sys.argv) > 1 and sys.argv[1] == 'run2':
        # run2 <tag> <lenA_log> <lenB_log>
        run_tag(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    else:
        print(__doc__)
