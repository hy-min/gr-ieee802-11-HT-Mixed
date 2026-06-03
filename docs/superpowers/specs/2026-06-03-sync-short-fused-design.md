# sync_short_fused Design Document

**Date**: 2026-06-03
**Author**: Claude Code
**Status**: Approved

---

## 1. Problem Statement

USRP real-time air transmission fails with RX overflow. Software loopback passes 9/9.

**Root cause**: The 8-block correlation chain upstream of `sync_short` consumes ~140% CPU due to GNU Radio scheduler overhead, causing the PHY processing chain to fall behind USRP's 20Msps data rate by ~1%.

**Existing 8-block chain**:
```
USRP Source → delay(16) → conjugate → multiply_vcc → MA_cc(48) → complex_to_mag
  ↓                                                              ↓
complex_to_mag_squared → MA_ff(64) ─────────────────────→ divide_ff → sync_short
```

Problems with the 8-block chain:
- **Scheduler overhead**: GR scheduler calls `general_work` 8 times per batch
- **Memory round-trips**: Each block reads input buffer → computes → writes output buffer = 16 memory copies per sample
- **Cannot skip**: Even when no frame is present, all 8 blocks process every sample

---

## 2. Goals

1. Replace the 8-block correlation chain with a single C++ block
2. Add energy gating to skip correlation computation during noise-only periods
3. Maintain bit-exact output compatibility with the original chain (for verification)
4. Reduce correlation CPU from ~140% to ~20%
5. Eliminate USRP RX overflow

---

## 3. Non-Goals

1. Do NOT modify `sync_short` itself — it has uncommitted modifications (gap detector, power-aware detection) that are still being iterated
2. Do NOT implement SIMD/VOLK acceleration in the first version — manual C++ is sufficient
3. Do NOT change the frame detection algorithm — only the infrastructure that feeds it

---

## 4. Architecture

### 4.1 Block Interface

**Block name**: `ieee802_11.sync_short_fused`

```cpp
// Input: raw RX samples
io_signature::make(1, 1, sizeof(gr_complex))

// Output: 3 streams, matching existing sync_short inputs exactly
io_signature::make3(3, 3,
    sizeof(gr_complex),   // out[0]: delayed raw samples (sync_short in[0])
    sizeof(gr_complex),   // out[1]: MA correlation complex (sync_short in[1])
    sizeof(float)         // out[2]: normalized correlation (sync_short in[2])
)
```

**Constructor parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `threshold` | float | 0.5 | Frame detection threshold (same value passed to sync_short) |
| `energy_gate_factor` | float | 3.0 | Energy gate threshold = noise_floor × factor. Linear scale: 3.0 ≈ 4.8 dB above noise floor. Set to 0.0 to disable gating. |
| `noise_est_window` | int | 1024 | Effective window size for adaptive noise floor estimation (in samples) |

### 4.2 Internal Algorithm

**Per-sample correlation computation (single pass, zero intermediate buffers)**:

```cpp
// Internal state (persistent across general_work calls)
gr_complex delay_ring[16];     // 16-sample delay circular buffer
gr_complex mult_ring[48];      // multiply results for MA_cc(48)
float      mag_sq_ring[64];    // |in|^2 for MA_ff(64)
gr_complex sum_cc = 0;         // running sum for MA_cc
float      sum_ff = 0;         // running sum for MA_ff
float      noise_floor = 1e-6; // adaptive noise floor estimate
```

**Per-sample processing**:
```
for each sample in[n]:
    // 1. 16-sample delay
    out0 = delay_ring[idx16]
    delay_ring[idx16] = in[n]

    // 2. Conjugate multiply (autocorrelation with lag=16)
    mult = in[n] * conj(out0)

    // 3. MA_cc(48) — complex moving average
    sum_cc += mult
    sum_cc -= mult_ring[idx48]
    mult_ring[idx48] = mult
    out1 = sum_cc / 48.0

    // 4. MA_ff(64) — power moving average
    mag_sq = norm(in[n])
    sum_ff += mag_sq
    sum_ff -= mag_sq_ring[idx64]
    mag_sq_ring[idx64] = mag_sq
    denom = sum_ff / 64.0

    // 5. Normalize
    out2 = (denom > 0) ? (abs(out1) / denom) : 0
```

**Energy gating (per batch)**:
```
batch_power = mean(norm(in[0:ninput-1]))

// Exponential moving average for noise floor
alpha = exp(-1.0 / noise_est_window)
noise_floor = alpha * noise_floor + (1 - alpha) * batch_power

gate_threshold = noise_floor * energy_gate_factor

if (batch_power < gate_threshold):
    // Noise-only batch: skip correlation, output zeros
    for i in 0..ninput-1:
        out0[i] = delay_ring output (still needed for sync_short COPY)
        out1[i] = 0
        out2[i] = 0
else:
    // Signal present: compute full correlation
    // (run per-sample loop above)
```

### 4.3 Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Sliding average implementation | Running sum + circular buffer | O(1) per sample, no dynamic allocation |
| Energy gating granularity | Per batch (entire batch skipped) | Aligns with GR scheduler batch processing; reduces branch misprediction |
| Noise floor update | Always updated (regardless of gating result) | Simple; tracks slowly changing environment |
| Warm-up handling | No special handling for first 64 samples | Original 8-block chain's moving_average blocks also start from zero; behavior naturally matches |
| Group delay compensation | None (preserves natural delay of original chain) | sync_short was designed with the full chain's delay in mind |

---

## 5. Integration

### 5.1 `wifi_phy_hier.py` Changes

**Remove** the following 8 blocks and their connections:
- `blocks_delay_0_0`
- `blocks_conjugate_cc_0`
- `blocks_multiply_xx_0`
- `blocks_moving_average_xx_0`
- `blocks_complex_to_mag_0`
- `blocks_complex_to_mag_squared_0`
- `blocks_moving_average_xx_1`
- `blocks_divide_xx_0`

**Add**:
```python
self.sync_short_fused_0 = ieee802_11.sync_short_fused(
    sensitivity,    # threshold
    3.0,            # energy_gate_factor
    1024            # noise_est_window
)
self.sync_short_fused_0.set_min_output_buffer(500000)
```

**Connections**:
```python
self.connect((self, 0), (self.sync_short_fused_0, 0))
self.connect((self.sync_short_fused_0, 0), (self.sync_short, 0))   # raw samples
self.connect((self.sync_short_fused_0, 1), (self.sync_short, 1))   # MA correlation
self.connect((self.sync_short_fused_0, 2), (self.sync_short, 2))   # normalized correlation
```

All `sync_short` downstream connections remain unchanged.

### 5.2 File Additions

| File | Purpose |
|------|---------|
| `include/ieee802_11/sync_short_fused.h` | Public header (following existing project convention) |
| `lib/sync_short_fused.cc` | Implementation |
| `python/bindings/sync_short_fused_python.cc` | Python binding for GNU Radio |
| `lib/CMakeLists.txt` | Add new source file |

### 5.3 Rollback Strategy

If the fused block has issues, `wifi_phy_hier.py` can be reverted by:
1. Commenting out the 4 `sync_short_fused` connections
2. Restoring the 8-block chain (kept as commented code or in git history)

---

## 6. Testing

### 6.1 Unit Test: Sample-by-Sample Consistency

Build a test flowgraph:
```
noise_source → head(10000) → [8-block chain] → file_sink (reference)
noise_source → head(10000) → [sync_short_fused] → file_sink (test)
```

Compare reference and test 3-output streams. Require per-sample difference < 1e-6.

**Test scenarios**:
1. Pure noise — verify correlation outputs are ~0 (accounting for floating point differences)
2. 802.11 frame signal — verify correlation peak timing and amplitude match exactly
3. Noise + frame mix — verify energy gating does not suppress real frames
4. Stream start/end boundary — verify warm-up behavior matches

### 6.2 Regression Test: Disable Gating

Set `energy_gate_factor = 0.0` (or a very large value) to effectively disable gating. Verify output is bit-exact with original 8-block chain.

### 6.3 USRP Real-Time Test

| Test | Expected Result |
|------|-----------------|
| Run `test_mcs_usrp.py` for 30 seconds | No RX overflow messages; Sent > 0, Recv > 0 |
| Run `test_usrp_air_loopback.py` | FCS OK count > 0 |
| CPU monitoring (`top`) | Correlation-related CPU drops from ~140% to ~20% |

---

## 7. Success Criteria

1. `sync_short_fused` output matches original 8-block chain within 1e-6 per sample (with gating disabled)
2. USRP RX overflow eliminated in `test_mcs_usrp.py`
3. FCS OK frames received in air loopback test
4. Correlation CPU < 30% (down from ~140%)

---

## 8. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Output does not match original chain exactly | Medium | High | Keep 8-block chain as commented fallback; unit test before USRP test |
| Energy gating suppresses weak frames | Low | High | Configurable `energy_gate_factor`; default 3.0 is conservative; can tune down to 1.5-2.0 if needed |
| sync_short state machine incompatible with batched zero-output | Low | Medium | sync_short's gap detector already handles correlation=0 during noise; no change needed |
| Compilation/binding issues | Low | Medium | Follow existing block patterns (e.g., `sync_short.cc`); build incrementally |

---

## 9. Future Work (Out of Scope)

1. **VOLK SIMD optimization**: Replace manual loops with `volk_32fc_x2_multiply_conjugate_32fc`, `volk_32fc_magnitude_squared_32f`, etc.
2. **Full fusion**: Merge correlation + sync_short state machine into a single block for maximum efficiency
3. **Multi-threading**: If still CPU-bound, explore GR's thread-per-block scheduling
