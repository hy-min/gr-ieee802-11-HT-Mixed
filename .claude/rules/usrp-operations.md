# USRP 硬件操作手册（X310 + UBX-160 @ 192.168.10.2）

## 每轮批次前检查

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor   # 须为 performance
sysctl -n net.core.wmem_max net.core.rmem_max               # 须为 2453333
ping -c1 192.168.10.2                                       # 须通
```
- governor 须 `performance`：powersave 下基线 DS 会降 ~30（P158 教训：
  162-170 vs 历史 200 是 governor，不是设备漂移）。
  修复：`sudo systemctl start gr-cpu-performance.service`（重启持久）。
- wmem/rmem 由 `/etc/sysctl.d/99-gr-ieee80211-uhd.conf` 持久化（P150）。

## 挂死恢复

症状：`uhd_usrp_probe` 报 "No devices found" 但 ping 通 = 设备被占用
或坏状态，**不是离线**。恢复顺序：

```bash
pkill -9 -f 'python.*usrp|python.*gnuradio|python.*ieee802'
sleep 3
uhd_usrp_probe --args addr=192.168.10.2    # nudge，看到 X300 即恢复
```
批次中途被 kill 后必做（P158 教训 #3）。

## RF 配置

| 场景 | 配置 |
|------|------|
| 电缆（当前最优）| `--freq 5250 --tx-gain 0 --rx-gain 31.5 --tx-scale 0.1` |
| 空口 | `--freq 5250 --tx-gain 0 --rx-gain 31.5 --rx-scale 40 --interval 100` |
| 历史空口 | `--freq 5890 --tx-gain 20`（P31b 起）|

- **同板默认**：A:0 TX → A:0 RX2。跨板弱 2.4×（P53），且跨板 LO 独立漂移
  0.5-1 rad（P122：3-way H52 平均在跨板破 L-SIG）。
- **电缆过驱动**：裸缆 tx-gain 0 = +5 dBm 进 RX2，超 UBX-160 线性区 20 dB。
  修复 = `--tx-scale 0.1`（TX 软件衰减 -20 dB），终败 20→5（P165）。
  **勿降 rx-gain < 20**（boxcar 贴地板伪影，终败暴增 45-49）。
- **禁止 `--rate 5`**（P58：溢出 48×）。固定 `--rate 20`。

## 捕获与回放

- 捕获文件放 `/home/hy/captures/`——**/tmp 重启会被清**（P150 教训）。
- 实时捕获必须用 `capture_usrp_txrx.py`（TX 开但捕获流图无 wifi_phy_rx，
  避免 RX 链反压 USRP source，P145c/P146）。
- 文件回放验证：`./usrp_validate_replay.sh 10 5250 0` 或
  `test_file_replay_e2e.py --iq-file <path>`。
- sync_long 的 `nread` 是慢消费计数器，**不是捕获位置**（勿误读为楔点）。

## 已知硬件特性

- UBX-160 内部 LO 相位噪声 ~1.77 rad/SC（P112 R1 实测）——当前 99.55%
  天花板的物理根源。外部 10 MHz 参考/GPSDO 是唯一压制路径。
- L-LTF0/L-LTF1 相位噪声实现独立（P139：2-way 平均收益 √2 的依据）。
- 外部 REF IN 已查过：PLL 无法锁定（P142）。
- X300 B:0 2.4 GHz 子板有 LO 泄漏（P16）；用 A:0 5 GHz（P17）。
