---
name: check-governor-before-batch
enabled: true
event: bash
pattern: p158_abab_batch\.py|usrp_realtime_validate\.sh
---

⚠️ **USRP 实时批次前检查（P158 教训：powersave 会伪装成设备漂移，基线 DS 降 ~30）**

运行批次前确认：

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor   # 须为 performance
sysctl -n net.core.wmem_max net.core.rmem_max               # 须为 2453333
ping -c1 192.168.10.2                                       # 须通
```

- governor 非 performance → `sudo systemctl start gr-cpu-performance.service`
- 若上一轮批次被 kill：先 `pkill -9 -f 'python.*usrp'` + `uhd_usrp_probe --args addr=192.168.10.2` nudge
- ping 通但 probe 报 "No devices found" = 设备被占用，不是离线

如已在本会话近期确认过，可忽略本警告。
