# Phase 76 Task 2 — TX Encoder Tag Flow Investigation

**Date**: 2026-07-02
**Branch**: TEST1
**Author**: investigating agent

## Context

Phase 76 Task 1 captured USRP 5890 with explicit tight_v2 env vars
(commit `6dc4693`, log `/tmp/p76_tight_v2_freq_5890.log`) and found:

- L-SIG decoded 10 frames successfully: **5 enc=5 + 5 enc=7** (NO enc=0)
- HT_SIG chain did NOT fire (HT_CAND=0)
- avg_snr_lsig = 2.74 dB (below 6 dB viterbi threshold)
- MAPPER log shows mapper constructor: encoding=0, but emitted enc=5/7

This means: TX mapper starts at `encoding=0` (HT-mode) but emits legacy frames
(enc=5/7 = QAM64_2_3 / QAM64_3_4) on USRP. Task 2 traces why.

## TX-side encoding tag flow

### Step 1 — Init (test script → wifi_phy_hier → mapper)

- `test_usrp_minimal_loopback.py:56-62` constructs two `wifi_phy_hier` instances
  with `encoding=ieee802_11.BPSK_1_2` (TX + RX share same init).
  `BPSK_1_2 == 0`, which is the HT-mode MCS0 encoding (per mapper_impl.cc line 164).
- `wifi_phy_hier.py:95`:
  `self.ieee802_11_mapper_0 = ieee802_11.mapper(encoding, False)`
  i.e. mapper constructor gets `encoding=0`.
- `wifi_phy_hier.py:222-227` exposes `set_encoding(encoding)` which re-broadcasts
  to the mapper via `self.ieee802_11_mapper_0.set_encoding(self.encoding)`.
  This is called from `wifi_phy_hier.py:227`. No other call site in
  `test_usrp_minimal_loopback.py`.
- `mapper_impl.cc:38-46` ctor sets `d_ofdm = ofdm_param(e)` (init encoding
  0 → BPSK_1_2) and prints `[MAPPER] constructor: encoding=0`.
- `mapper_impl.cc:57-62` `set_encoding(e)` only re-assigns `d_ofdm`; never
  called except at ctor + (hypothetical) `set_encoding` overrides.

### Step 2 — PDU emission path (mac → encoding_stripper → mapper → ht_header → signal_field)

`wifi_phy_hier.py:177`:
```
self.msg_connect((self, 'mac_in'), (self.ieee802_11_mapper_0, 'in'))
```

`test_usrp_minimal_loopback.py:118-130` defines an `encoding_stripper` block
that **de-letes** the `"encoding"` and `"mcs"` keys from the upstream PMT dict
**before** it reaches `wifi_phy_hier.mac_in`.

- `mac.cc:124-138` `app_in` writes the encoding tag from `d_encoding` (the MAC
  block's internal state) into the outgoing PDU.
- The encoding_stripper then **removes** that tag.
- The mapper receives `mac_in` with `meta` containing `len`/`psdu_len`
  etc. but NO `encoding` tag.

### Step 3 — Mapper message handler behaviour with no encoding tag

`mapper_impl.cc:436-619` `handle_msg` reads:
- `mcs = meta_get_mcs(meta)` (line 456): looks up first `"mcs"`, then
  `"encoding"`. Both are -1 because the stripper removed them. Result: **mcs = -1**.
- `psdu_len_meta` from `"psdu_len"` (line 458).
- Line 468: `if (mcs >= 0) { ... d_ofdm = ofdm_param((Encoding)mcs); }` —
  this branch is **SKIPPED** because mcs = -1.
- Line 487: `frame_param frame(d_ofdm, psdu_length);` — uses the
  ctor-set `d_ofdm = ofdm_param(BPSK_1_2)` = encoding=0.
- Line 489-497: `effective_mcs = encoding_to_ht_mcs(d_ofdm.encoding)` →
  since `d_ofdm.encoding == BPSK_1_2 == 0`, `effective_mcs = 0`.
- Line 499: `ht_mode = is_ht_mcs(0) = true`.
- Line 506: `setup_ht_params(0, psdu_length, frame, d_use_ldpc)`.
- Line 597-609: emits **both** `encoding` and `mcs` tags onto the output:
  - `tag_enc = (mcs >= 0) ? mcs_to_encoding(mcs) : (int)d_ofdm.encoding`
  - With mcs=-1: `tag_enc = d_ofdm.encoding = 0` (BPSK_1_2).
  - `tag_mcs = encoding_to_ht_mcs(0) = 0` (HT-MCS0).

So the mapper **does** emit `encoding=0` and `mcs=0` onto the stream.

### Step 4 — Mapper output fork

`wifi_phy_hier.py:185-186` (header path) and `:202` (data path) both connect
the same mapper output port 0:
```
self.connect((self.ieee802_11_mapper_0, 0), (self.ht_header_tagged_0, 0))     # line 185 → header
self.connect((self.ieee802_11_mapper_0, 0), (self.ieee802_11_chunks_to_symbols_xx_0, 0))  # line 202 → data
```

`chunks_to_symbols_impl.cc:50-90` reads the `"encoding"` tag from the input
and uses it for data mapping: switching on the value:
- enc 0,1 → BPSK
- enc 1,2 → QPSK (NB: QPSK_1_2 == 1, QPSK_3_4 == 2 in the enum)
- 16-QAM
- 64-QAM

Output bytes are tagged-stream with the same tags carried.

### Step 5 — ht_header_tagged reads and re-tags

`ht_header_tagged_impl.cc:36` holds a `d_formatter(signal_field::make())`.
`make_one_header_from_tags` (line 60-128):
- Reads tags from upstream mapper stream (lines 67-90).
- Looks for key `d_encoding_tag_key` (= `pmt::intern("encoding")`) and
  stores `d_pending_encoding`.
- Line 112-120: pushes a synthesized `encoding` tag into a copy of the tag
  list passed to `d_formatter->header_formatter(...)`.
- Line 183-189: also emits `encoding` + `mcs` onto the output stream at the
  first output byte (header index 0).

`signal_field_impl.cc:326-368` `header_formatter`:
- Iterates tags (line 338), looks for key `pmt::mp("encoding")` (line 341).
- If found, `encoding = pmt::to_long(tags[i].value)`.
- Line 363: `ofdm_param ofdm((Encoding)encoding)` and
  `generate_signal_field((char*)out, frame, ofdm, use_ldpc)` builds the
  L-SIG/HT-SIG header with that encoding's rate field.

So the chain **from mapper output** all the way through `ht_header_tagged` →
`signal_field` reads `encoding=0` (BPSK_1_2) — UNLESS something between the
MAC and the mapper or between the mapper and ht_header re-tags.

## Where could encoding be overridden on TX?

### Candidate 1 — Upstream MAC tag bypass

`mac.cc:124-138` `app_in` writes `pmt::dict_add(dict, pmt::mp("encoding"),
pmt::from_long((int)d_encoding))`.  `d_encoding` starts at **whatever the ctor
default for the `mac()` arg list provides** and is updated only by
`mac_in(pmt::pmt_t msg)` at line 60-70, which expects an integer pmt message
on port `mcs_in`.

Critically, in `test_usrp_minimal_loopback.py`, the `ieee802_11.mac` block is
instantiated at line 78-83 **without** any explicit initial encoding, and there
**is no `mcs_in` connection**. Therefore the MAC's `d_encoding` retains its
ctor default.

`mac.h` / `mac_impl` default value for `d_encoding`: unknown here without
reading `mac_impl.cc` ctor — search needed.

But: the `encoding_stripper` deletes the `encoding` tag from the PDU before
it reaches mapper, so the MAC's `d_encoding` does not matter for mapper init
on this path.  However it DOES matter if the encoding_stripper is bypassed or
not present (per `test_mcs_usrp.py` and `wifi_loopback_constellation.py`,
which strip explicitly with code comments stating "so mapper uses
set_encoding()").

### Candidate 2 — External tag on mapper's `in` port

Other than the test script's encoding_stripper, there is no path that
re-injects an `encoding` tag into the mapper's input. Both `mac_in` ports feed
`mapper_0.in`, but the stripper is the only encoder-aware block in
`test_usrp_minimal_loopback.py`.

### Candidate 3 — Mapper output forked to chunks_to_symbols bypasses header

`chunks_to_symbols_impl.cc:50-90` reads encoding from the mapper's output
stream (after the stripper no longer matters). The encoding value seen there
is whatever the mapper emits (`tag_enc = 0`), so the data constellation will
be BPSK (1 bit per symbol). However `chunks_to_symbols_xx_0` is the **data
path** (line 102); its mapping is independent of the L-SIG rate-field the
RX decodes.

### Candidate 4 — Background wifi frames at 5890 MHz

5890 MHz is part of UNII-3 (5.725-5.850 GHz). 5890 MHz actually crosses the
boundary slightly; on some regulatory domains the channelization is
5 MHz-centered. The lower 5.8 GHz band is heavily used by 802.11n WiFi APs.
The capture shows LSIG_DECODE lines with `enc=5` (QAM64_2_3) and `enc=7`
(QAM64_3_4) — these are **high-rate OFDM** modulations that 802.11n HT-MCS5/6
use (per `mapper_impl.cc:201-202` QAM64 n_bpsc=6). HT-MCS5 → enc=5,
HT-MCS6 → enc=6, HT-MCS7 → enc=7.

But the same mapper emits encoding=0 from ctor, so if the chain is correct,
TX frames must carry L-SIG rate that maps to BPSK (rate=0xD = 6 Mb/s, which
is what `LSIG_RATE_FORCE=0xD` forces on the RX side per Phase 18 and Phase 65
standard config).

If we see 5/7 in the LSIG_DECODE log, that is **REAL-WORLD WIFI TRAFFIC**
captured at 5890 MHz, not our own TX frames, because:
- Our TX mapper emits enc=0 → L-SIG rate field encodes BPSK_1_2.
- LSIG_DECODE-OK with enc=5/7 → those frames have **non-BPSK constellation**
  in the L-SIG OFDM symbol — incompatible with BPSK_1_2.

This is the most plausible explanation. **The "enc=5 / enc=7" frames captured
by `test_usrp_minimal_loopback.py` are background WiFi transmissions, not our
own TX frames.**

`LSIG_RATE_FORCE=0xD` (which we set) **forces** the RX to interpret every
L-SIG as rate=0xD (BPSK_1_2).  So if enc=5/7 lines still print despite the
env var being set, that suggests the env var is **not gating them** — those
lines may be printed post-viterbi but pre-env-var check, or the env var only
controls a downstream decision.

Letting the LSIG_RATE_FORCE env var to its non-forced value would let real
L-SIG rate fields pass through normally and we would see a mix of enc values.
That mixed capture is what we have: **5590/5890 MHz UNII-band is busy with
other 802.11n APs**.

## Files examined

| File | Lines | Purpose |
|------|-------|---------|
| `lib/mapper_impl.cc` | 29-46, 57-68, 117-130, 436-619 | mapper state + tag emit |
| `lib/mac.cc` | 60-70, 124-138 | MAC mcs_in handler + encoding tag emit |
| `lib/signal_field_impl.cc` | 326-368 | L-SIG/HT-SIG formatter reads encoding tag |
| `lib/ht_header_tagged_impl.cc` | 30-128, 132-220 | propagates encoding tag into formatter |
| `lib/chunks_to_symbols_impl.cc` | 40-90 | data path mapping reads encoding tag |
| `wifi_phy_hier.py` | 95, 177, 185-202, 222-227 | block init + connections |
| `test_usrp_minimal_loopback.py` | 56-62, 78-83, 108-130 | test construction + stripper |
| `/tmp/p76_tight_v2_freq_5890.log` | n/a | captured MAPPER log lines |

## Hypothesis (most likely)

**The 5×enc=5 + 5×enc=7 frames observed in the Phase 76 T1 log are NOT
self-TX frames. They are background WiFi transmissions at 5890 MHz.**

The mapper chain (from `test_usrp_minimal_loopback.py` → encoding_stripper →
mapper → ht_header_tagged → signal_field) is **structurally** emitting
encoding=0 / mcs=0, which would produce L-SIG rate=0xD. The
`LSIG_RATE_FORCE=0xD` env var rejects all non-0xD frames. However, the printed
`LSIG_DECODE OK enc=5/7` suggests those lines are emitted **before or in
parallel with** the rate-force gate — OR the env var is not gating as expected.

To test this hypothesis, Task 3 should:
1. **Disable** `IEEE80211_LSIG_RATE_FORCE` so all real L-SIG rates pass.
2. **Modify** (or add an env-var-bypassable) TX-side tag flow to inject an
   *additional* identifiable tag (e.g. a magic `d_test_seq` counter) on every
   self-TX frame, so RX-side can distinguish self-TX from background.

Specifically the FORCING must happen upstream of `app_in` (e.g. inside MAC)
since the encoding_stripper removes the encoding tag before mapper sees the
PDU. If forcing at MAC → encoding_stripper → mapper chain, the mapper will
re-derive encoding=0 from ctor state via the `mcs_to_encoding(0)/` path that
we already see. So forcing at **mapper input or constructor** is sufficient.

## Next steps (Phase 76 Task 3+)

Task 3 should add `IEEE80211_FORCE_TX_ENCODING=N` env var with default OFF
that, when set:

- Either (a) sets `d_ofdm = ofdm_param((Encoding)N)` in
  `mapper_impl::handle_msg()` BEFORE `frame_param frame(...)` is constructed
  (line 487 of mapper_impl.cc), so the frame geometry uses forcing,
- Or (b) overwrites the `tag_enc = ...` and `tag_mcs = ...` values emitted at
  lines 597-609 with `N`, so the downstream signal_field/chunks_to_symbols
  see encoding=N even if upstream tags differ.

Path (a) is cleaner because it forces both the frame layout AND the emitted
tag. The MAC-side `d_encoding` value would become irrelevant since the
stripper removes it.

To **prove** the background-WiFi hypothesis independently, also worth
verifying: change `IEEE80211_LSIG_RATE_FORCE` to `0` (or other rates) and see
if more frames appear with varied enc values. If yes → hypothesis confirmed.
