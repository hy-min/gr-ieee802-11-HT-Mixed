# 构建与测试细节（gr-ieee802-11）

## 构建

```bash
cd build && make -j$(nproc) && make install
```
- **`make install` 不可省**：否则 Python 加载旧 .so（最高频事故源）。
- CMake 需显式 conda 路径（详见 git 历史 / `docs/` 早期 build notes）。
- ASan 构建（查竞态/越界）：
  `cmake -DCMAKE_CXX_FLAGS="-fsanitize=address -g"` → `make && make install` →
  运行时 `LD_PRELOAD=<conda>/lib/libasan.so`（P147 流程）。

## 运行测试的标准前缀

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python <test.py>
```
（LD_PRELOAD 需要绝对路径或先 cd 到仓库根；相对路径在别的 cwd 下会静默失效。）

## 测试清单

| 测试 | 用途 | 通过标准 |
|------|------|----------|
| `examples/test_direct_loopback.py` | 软件回环回归门（每次改动必跑）| `Final: OK=1 FAIL=0` |
| `test_h_estimation_synthetic.py` | 合成 H 估计 | 5/5 |
| `test_lsig_viterbi_synthetic.py` | 合成 L-SIG viterbi | 3/3 |
| `examples/test_htsig_viterbi_synthetic.py` | 合成 HT-SIG viterbi | 3/3 per layer |
| `test_file_replay_e2e.py --iq-file <cap>` | 文件回放（USRP IQ 离线验证）| FCS_OK ≥ 1 |
| `./usrp_realtime_validate.sh --tx-scale 0.1` | USRP 实时基线 | `PASS: DECODE_SUCCESS >= 15` |
| `p158_abab_batch.py --pairs 4 --exp-env X=Y` | 单变量 A/B 金标准 | 输出 `VERDICT:` 行 |
| `p147_race_repro.py` | 双实例竞态复现 | 无 SIGSEGV |
| `p146_rx_throughput_probe.py` / `p146_bisect.py` | RX 链吞吐量/二分定位 | 207-263 MHz |
| `p148_parse.py` / `p148_stats.py` / `p148_funnel.py` | 离线漏斗 + N 次统计尺 | — |
| `p150_count_frames.py` | 已知帧数真值计数 | — |
| `p163b_fate_analysis.py` | 逐帧命运图（需 `IEEE80211_DECODE_SEQ=1`）| — |

## 已知测试陷阱

- **10 MHz loopback 伪影**：`test_direct_loopback.py` 用 bandwidth=10e6
  （8 样本 L-STF 周期与 16-lag boxcar 不匹配）——MIN_PLATEAU=24 在此会漏检；
  回归门用默认 M=2 不受影响（P154 记录）。
- **loopback 无流压缩**：任何依赖流压缩/实时调度的 bug（P148 chunk 依赖、
  P160 前视自中毒、P163 chunk 边界泄漏）loopback 测不出——
  离线回放正收益 ≠ 实时正收益，最终以配对 ABAB 为准。
- **harness stderr 落点**：`/tmp/rt_validate.err` 每轮覆写；批次脚本的
  `run_XX.err` 只有脚本自身 stderr（在那里数触发次数会得到假零）。
- **setsid 陷阱**：`setsid cmd &` 后 `wait $!` 拿到的是 setsid 瞬时退出；
  detached 任务要等真实 PID 消失（P162 运维教训）。
- **probe 报 "No devices found" 但 ping 通** = 设备被占用，正常；
  恢复见 usrp-operations.md。

## 禁止

- 禁止从 `wifi_phy_hier.grc` 生成 Python（段错误）；直接编辑
  `wifi_phy_hier.py`。
- 禁止 `--rate 5`（P58：48× 溢出）。
- 禁止函数级 `static` 可变缓冲区（P147 Heisenbug 根因）。
