# Comelit ICONA Bridge — protocol reference

Reverse-engineered notes for the ICONA Bridge protocol as spoken by Comelit ViP
intercoms over **TCP/UDP port 64100**, plus the Home Assistant media plumbing
that carries the resulting streams to a browser.

The reference device is a **6701W** (firmware 2.x). Claims here carry different
evidence weights — see [Provenance](#0-provenance-and-verification-status)
before relying on any of them. Where community notes for other models disagree,
both observations are recorded — see
[Firmware and model differences](#12-firmware-and-model-differences).

Contents:

0. [Provenance and verification status](#0-provenance-and-verification-status)
1. [Framing](#1-framing)
2. [Channels](#2-channels)
3. [Authentication and configuration](#3-authentication-and-configuration)
4. [CTPP: the VIP event channel](#4-ctpp-the-vip-event-channel)
5. [Timestamps, counters, and ACKs](#5-timestamps-counters-and-acks)
6. [Door opening](#6-door-opening)
7. [Outbound video calls](#7-outbound-video-calls-ha-initiated)
8. [Inbound calls: passive video and answering](#8-inbound-calls-passive-video-and-answering)
9. [Media transport](#9-media-transport)
10. [Audio](#10-audio)
11. [Home Assistant end-to-end: RTSP, go2rtc, WebRTC](#11-home-assistant-end-to-end-rtsp-go2rtc-webrtc)
12. [Firmware and model differences](#12-firmware-and-model-differences)
13. [Other channels and unexplored surface](#13-other-channels-and-unexplored-surface)

---

## 0. Provenance and verification status

Not every statement here rests on the same evidence. Four tiers:

| Tier | Meaning |
|---|---|
| **PCAP-confirmed** | Re-verified 2026-08-28 by parsing this repo's captures (`inbound_call.pcap`, `test_inbound_run9.pcap` — gitignored, kept locally) |
| **Live-validated** | Observed working (or failing) against the device through the integration's own debug logs during development |
| **Code-derived** | Transcribed from `custom_components/comelit_man/` implementation and its docstrings. The implementation demonstrably works, but the specific byte-level claim was not independently re-checked against a capture |
| **Secondhand** | Reported by other projects for hardware not available here. Treat as a lead, not fact |

Checks re-run against both captures on 2026-08-28 — all **confirmed**, none refuted:

- §1 framing layout (4377 + 1783 messages parsed cleanly under it)
- §2 channel-open sequence is 1 (`{1: 12, 2: 1}` and `{1: 11, 2: 1}` — the lone
  seq-2 is the client's answer to a device-initiated open, as documented)
- §5 event ACK timestamp = `transform(device_ts)`; the ACKs that *don't* match
  are exactly the renewal pair, which uses the separate init-derived regime
- §4.3 renewal answered with a `0x1800` + `0x1820` pair
- §4.4 ring is `0x18C0` action `0x0028`
- §8 inbound `video_config` carries **320×240**
- §9 H.264 is payload type 99 (1253 + 567 packets)
- §10 PCMA is payload type 8 in 160-byte frames
- §12 this device uses `SB`-prefixed addressing (`SB000003`, `SB0000031`,
  `SB100001`)
- §9 media transport asymmetry between call types (measured separately: 889 UDP
  mic frames in the app's UDP-media call; TCP-only media in the inbound capture)

Not covered by the captures on hand:

- **Ring retransmission sharing a timestamp** (§4.4) — each capture contains a
  single `0x18C0` frame. This claim is instead **live-validated**: a mid-call
  ring on 2026-08-27 produced five forwarded frames all carrying
  `ring_ts=0x7621014E`, which is what the deduplication key relies on.
- **Door-open-during-video byte layout** (§6) — no door open occurs in either
  capture; the layout is **code-derived** from a docstring citing a capture not
  retained here.
- §3 JSON message shapes, §6 door sequences, §7 the full outbound ordering,
  §13 FRCG / discovery / provisioning — **code-derived** or **secondhand**.
- §12's whole table beyond the `SB` row — **secondhand**.

§11 (Home Assistant end-to-end) is **live-validated**: every claim in it was
established by making the corresponding failure happen and then fixing it,
confirmed by device-side logs and an independent `aiortc` client.

---

## 1. Framing

Every ICONA message — TCP or UDP — is an 8-byte header followed by a body:

```
00 06        magic (constant)
LL LL        body length   (uint16, little-endian)
RR RR        request id    (uint16, little-endian) — the channel's server id
00 00        padding
```

- **JSON body**: starts with `{`. UTF-8, compact separators (`,` and `:` with no
  spaces). Used by UAUT, UCFG, PUSH.
- **Binary body**: channel management (`request id == 0`) or VIP signalling on
  CTPP, or RTP media on RTPC/UDPM.

The request id in the header is how the device routes a message to a channel.
Media packets reuse it as a stream identifier — the same value the device
assigned when it opened its own RTPC channel.

Implementation: `protocol.py` (`encode_header`, `decode_header`, `HEADER_SIZE`).

---

## 2. Channels

A channel is opened with a COMMAND packet, used, then closed with END.

| Name   | Wire type id | Purpose |
|--------|--------------|---------|
| `UAUT` | 7            | Authentication (token) |
| `UCFG` | 2            | Configuration read (`get-configuration`) |
| `PUSH` | 2            | FCM token registration; shares the type id with UCFG — the device distinguishes them by the channel *name* string |
| `CTPP` | 16           | VIP events: ring, door opened, registration renewal, door control, call signalling |
| `CSPB` | 17           | Opened alongside CTPP; no traffic observed, but the device expects it |
| `INFO` | 20           | Server info (model, firmware, capabilities) |
| `UDPM` | —            | Media control; device-assigned, opened with `trailing_byte=1` |
| `RTPC` | —            | Media streams; two are opened (RTPC1 audio, RTPC2 video). The device also opens its *own* RTPC back toward the client |

**Channel open** (`protocol.py:encode_channel_open`):

```
[0xABCD LE16] [sequence LE16] [type id LE32] [ascii name] [request id LE16]
[trailing byte] [00] [len LE32] [extra ascii] [00]
```

Two load-bearing quirks:

- **The channel-open sequence must be 1.** The device silently ignores packets
  with any other sequence on an open.
- **The `0x00` pad byte before `extra_data` is required.** Without it the extra
  data lands one byte early and the device ignores every subsequent CTPP
  message — the channel appears open but is inert.

`Channel.next_sequence()` advances by 2 thereafter: the client uses even
sequence numbers, the device odd.

**Channel close** is `[0x01EF LE16] [sequence LE16]`, with the channel's server
id in the *header* request-id field so the device releases its session state.

**Device-initiated opens.** The device opens channels toward the client
(notably its own RTPC during a call). The client must answer with
`[0xABCD LE16] [seq=2] [0x04000000] [request id LE16] [0x0000]`. A placeholder
channel is registered ahead of time so the assigned id can be captured when it
arrives (`client.register_placeholder_channel`).

Timeouts must be **≥ 30 s**: the device is routinely slow to answer, especially
during call setup.

---

## 3. Authentication and configuration

**UAUT** — open channel type 7, then send:

```json
{"message":"access","user-token":"<32 hex>","message-type":"request","message-id":2}
```

Expect `response-code: 200`. The token is a 32-character hex string that can be
extracted from the device's own web UI configuration backup (see
`token.py`), or minted by redeeming an activation code.

**UCFG** — `get-configuration` with `addressbooks: "all"` returns:

- `vip.apt-address` — the apartment's base address (e.g. `SB000006`)
- `vip.apt-subaddress` — this identity's index within the apartment (e.g. `1`)
- `entrance-address-book` — entrance panels (call origins)
- `opendoor-address-book` / `actuator-address-book` — door and relay targets
- `rtsp-camera-address-book` — additional IP cameras, if any

The pairing of **base address** and **base+subaddress** matters everywhere: VIP
messages use `apt_address + subaddress` (e.g. `SB0000061`) as the caller and,
on inbound calls, the bare `apt_address` (`SB000006`) as the callee.

> **Identity caveat.** The wall monitor holds its own identity. Registering a
> second listener under the same identity gets one of them kicked — use a
> dedicated app-class user.

---

## 4. CTPP: the VIP event channel

CTPP is the heart of the integration. It is a *held* registration: the official
app opens it, registers, closes, and relies on cloud push; this integration
keeps it open so events arrive locally with no cloud involvement.

### 4.1 Registration

Open `CTPP` with `extra_data = apt_address + subaddress`, then send the init
message (`protocol.py:encode_ctpp_init`):

```
[0x18C0 LE16] [timestamp LE32] [00 11] [00 40] [18 C2]
[addr+sub \0] [10 0E] [00 00 00 00] [FF FF FF FF]
[addr+sub \0] [apt_addr \0] [00]
```

`0x18C2` is a protocol constant echoed back by the device in renewals. Bytes
2–3 of the timestamp act as a **session id** and must stay consistent across
every ACK derived from this init.

The device replies with a `0x1800` init ACK, then begins a renewal cycle.

### 4.2 Message layout

All CTPP binary messages share a prefix/action shape:

```
[prefix LE16] [timestamp LE32] [action BE16] [flags BE16]
[extra …] [FF FF FF FF] [caller \0] [callee \0 \0]
```

| Prefix   | Meaning |
|----------|---------|
| `0x18C0` | Call init — **this is the doorbell ring** (device → client) |
| `0x1800` | ACK |
| `0x1820` | Confirm ACK (second half of the renewal pair) |
| `0x1840` | Call-phase event (codec, config, keepalive, call end, door open) |
| `0x1860` | VIP FSM event (state changes, registration renewal) |

VIP FSM actions carried in `0x1860`:

| Action   | Meaning |
|----------|---------|
| `0x0000` | IDLE |
| `0x0001` | IN_ALERTING — a ring |
| `0x0002` | CONNECTED — call answered |
| `0x0003` | DOOR_OPENED |
| `0x0004` | OUT_ALERTING |
| `0x0005` | CLOSED |
| `0x000A` | CALL_TERMINATED |
| `0x0010` | **Registration renewal** — must be answered |

Call-phase actions carried in `0x1840` / used in call setup:

| Action   | Meaning |
|----------|---------|
| `0x0028` | CALL_INIT |
| `0x0008` | CODEC_NEG |
| `0x000A` | RTPC_LINK |
| `0x001A` | VIDEO_CONFIG |
| `0x0070` | PEER / accept |
| `0x000E` | CONFIG_ACK |
| `0x0002` | CALL_ACCEPTED (device→client outbound; client→device on inbound answer) |
| `0x0003` | RTPC2_READY (client→device, inbound) / CALL_END (device→client, mid-call) |
| `0x000D` | DOOR_OPEN on an active video channel |
| `0x002D` | HANGUP |

Addresses are extracted by scanning the body for null-terminated ASCII
addresses. The 6701W in kit mode uses an `S`-prefixed form (`SB100001`); other
models use plain numeric addresses.

### 4.3 Registration renewal — the keepalive that matters

Every ~2 minutes the device sends `0x1860` / action `0x0010`. The client must
answer with a **pair**: `0x1800` then `0x1820`. Miss it and the device stops
delivering VIP events entirely — rings simply stop arriving, with no error.

**The renewal's addresses must be read from the message, not from config.**
The device embeds its own binary VIP address, which can differ from the
`apt-address` returned by the JSON configuration API (observed: binary
`SB000003` vs config `SB000006`). Heuristic used
(`vip_listener.py:_resolve_ack_addresses`): the address appearing **twice** in
the message is the caller (base + subaddress); the one appearing **once** is
the callee (base). Fall back to config values if the shape doesn't match.

### 4.4 Event classification

- `0x18C0` (action `0x0028`) → **ring**. Retransmitted every 1–2 s while the
  entrance panel is alerting; all retransmits carry the *same* timestamp, which
  makes `(entrance_addr, ring_ts)` a reliable deduplication key.
- `0x1860` / `0x0001` → also a ring (IN_ALERTING).
- `0x1860` / `0x0003` → door opened. **Never ACKed** — the device retransmits
  briefly then stops on its own; any ACK is rejected.
- `0x1840` / `0x0000` → idle / teardown tail. Also arrives at CTPP init after a
  device reboot, so it is only meaningful as a missed-call signal when it
  closely follows an unanswered ring.

---

## 5. Timestamps, counters, and ACKs

Three distinct timestamp regimes coexist. Mixing them up is the single most
common way to make the device go silent.

**1. Event ACKs — derive from the device's timestamp.**

```python
rb = bytearray(struct.pack("<I", device_ts))
rb[0] |= 0x80
rb[2], rb[3] = rb[3], (rb[2] + 1) & 0xFF
ack_ts = struct.unpack("<I", bytes(rb))[0]
```

Used for every `0x1800` ACK of a `0x18C0` / `0x1840` / `0x1860` event
(`video_call.py:_transform_device_ts`). Caller is `apt_addr+sub`, callee is the
bare `apt_addr`.

**2. Registration renewal ACKs — derive from our own init.**

```
ack_ts = (ctpp_init_ts + 0x01010000) & 0xFFFFFFFF
```

Constant across the life of the channel. Never derived from the device's
renewal timestamp.

**3. Call counters — increment specific bytes.**

```
_CTR_INCR_BYTE4 = 0x00010000   # byte 4 only
_CTR_INCR_BYTE5 = 0x01000000   # byte 5 only
_CTR_INCR_BOTH  = 0x01010000   # both
```

Each call-setup message advances the session counter by one of these,
determined by the step (see `video_call.py`). The call's initial timestamp must
differ from the CTPP init timestamp in bytes 2–3 — the device treats those as
the session id and rejects a call that reuses the registration's.

**Inbound answers derive from the ring.** `fresh_ts` is the same bit transform
as event ACKs applied to `ring_ts`. This is what makes the device accept the
answer instead of continuing to ring.

---

## 6. Door opening

Three code paths, chosen by what is already open:

**Path 1 — during an active video call.** A single message on the existing
CTPP channel: `0x1840` / action `0x000D`, sub `0x002D`, 48-byte body:

```
[0x1840 LE16] [call_counter LE32] [000D BE16] [002D BE16]
[entrance_addr padded to 10] [relay_index LE32] [FF FF FF FF]
[our_addr 10] [entrance_addr 10]
```

The trailing 10 bytes are the **apartment address**, not the entrance address —
an easy mistake to miss in a capture where the two happen to be equal.

**Path 2 — CTPP open, no video (fast path).** Reuse the listener's channel and
skip the init handshake entirely.

**Path 3 — nothing open.** Open a transient CTPP channel with a full
`ctpp_init_sequence(send_ack=False)`, act, then close. The ACK pair is
deliberately *not* sent here; the original door-open flow never sent it and the
device does not expect it.

Per-door sequence (`door.py`):

- **Regular door**: `OPEN` + `CONFIRM` → `door_init` → drain 2 responses →
  `OPEN` + `CONFIRM` again.
- **Actuator**: `actuator_init` → drain 2 → `actuator_open` + `actuator_confirm`.
  Actuators use a distinct `0x18..45BE` init variant.

Whether an entry is a door or an actuator is determined by which address book
it appeared in, not by any field on the entry itself.

---

## 7. Outbound video calls (HA-initiated)

Sequence (`video_call.py:start`), all on the shared connection:

1. Reuse the coordinator's CTPP channel if the VIP listener has one open;
   otherwise open CTPP + CSPB and run the init sequence.
2. `call_init` (`0x18C0` / `0x0028`) at `call_ts = init_ts + 1`, carrying a
   6-byte session block and an `"II"` codec marker.
3. Open `UDPM` (`trailing_byte=1`); the media token is at
   `open_response_body[16:18]`.
4. Create the RTP receiver, bind the local UDP socket, send two discovery
   packets so the device learns the port, start the keepalive loop (1.5 s).
5. `call_ack` with `codec_param=0x27` (outbound).
6. Codec exchange: up to 10 reads. `0x1840/0x0008` → advance both counter bytes
   and ACK; `0x1840/0x0002` (call accepted) → advance byte 5, ACK, proceed.
7. Open `RTPC` and `RTPC2` (RTPC2 uses the wire name `RTPC`).
8. `rtpc_link` (`0x1840/0x000A`) using RTPC1's server id.
9. `video_config` (`0x1840/0x001A`) at 800×480 @ 16 fps. The secondary
   resolution field is hardcoded 320×240 — *not* half the primary.
10. Wait for the device to open its own RTPC (≤ 5 s), then ACK its link.
11. HANGUP/ZERO (`0x1840/0x0000`), then start media.

### Lease renewal (CALL_END)

Roughly every 30 s the device's call lease expires and it sends
`0x1840` / action `0x0003`. Sub-code `0x0000` is the timer; `0x000E` means the
door-open relay triggered it. Both take the same path.

`_inline_reestablish` renews **on the same TCP connection** — no reconnect, no
new session: ACK the CALL_END with the transform, re-run CTPP init, new
call_init and codec exchange, re-send `rtpc_link` + `video_config` reusing the
existing RTPC channels, wait for the device RTPC, HANGUP/ZERO, then send the
renewal peer/accept (`0x1860` / `0x0070`) which is what re-opens audio.

---

## 8. Inbound calls: passive video and answering

The integration deliberately splits what the official app does in one go:

- **`start_inbound()`** establishes media and shows video **without signalling
  the call as answered**. Other stations keep ringing. This is the "passive"
  state — you can see who is at the door before deciding.
- **`answer_inbound()`** actually answers, which is what opens the device's
  audio path.

### 8.1 Passive setup (steps 1–12)

1. ACK the ring with `fresh_ts` (transform of `ring_ts`).
2–4. Open `RTPC` and `UDPM` as concurrent tasks with `await asyncio.sleep(0)`
   yields between, then send the codec ACK with **`codec_param=0x07`**
   (inbound; outbound uses `0x27`) — all within a few milliseconds, while the
   channel opens are still in flight. The device ignores a codec ACK that
   arrives after it has ACKed the channels.
5. Create the receiver, attach RTSP queues, start control + keepalive.
6. Drain the device's burst for up to 10 s: ACK `0x18C0/0x0029` retransmits
   with `fresh_ts`; answer any `0x1860/0x0010` renewal that lands mid-sequence
   with the coordinator's renewal ACK pair.
7–8. ACK2 at `fresh_ts + BYTE5`, open `RTPC2` concurrently, retransmit the
   codec ACK on the same counter.
9. `rtpc2_ready` (`0x1840/0x0003`, flags `0x000A`) — purpose unknown, but the
   device will not proceed without it.
10. `rtpc_link` using RTPC1's id.
11–12. `video_config` at **320×240** (inbound differs from outbound's 800×480),
   a 3 s pause, a retransmit, then a 0.4 s pause.

At this point video flows and the ring event fires.

### 8.2 Answering (steps 13–17)

1. Install a handoff queue so the CTPP monitor routes `0x1840` traffic to the
   answer sequence instead of ACKing it generically.
2. `answer_peer` with `inbound=True` (48-byte form: prefix `0x1840`, action
   `0x0070`, flag `0x01`, extra `0x0000` padding before the separator).
3. `call_accepted` (`0x1840/0x0002`) — on inbound the roles are reversed and
   the *client* sends this.
4. Drain until both `0x1840/0x000A` (rtpc_link) and `0x1840/0x000E` (peer) have
   been ACKed with `transform(device_ts)`. The device opens its audio RTPC only
   after receiving these.
5. Capture the device RTPC's request id and start the audio sender.

---

## 9. Media transport

The device streams **either UDP or TCP** depending on how the call was set up,
and this determines how the client must send audio back.

| Call type | Device → client media | Client → device audio |
|---|---|---|
| Outbound (HA-initiated), app-style | UDP to the client's media port | UDP from the same socket |
| Inbound (ring answered), this integration | **TCP** on RTPC1 (audio) / RTPC2 (video) | **TCP** on the device's own RTPC channel |

This was established by comparing captures: in an app capture of a UDP-media
call, 889 mic frames went out as UDP from the media port (`req=0xF178`); in an
inbound TCP-media capture the client's UDP mic frames were never acknowledged
or acted on. **Transport symmetry is required** — audio sent on the wrong
transport is silently discarded, with no error anywhere.

Packet layout for media, both transports:

```
[ICONA header 8B, request id = the target channel's id]
[RTP header 12B]
[payload]
```

On TCP the client library already frames the ICONA header, so the body handed
to `send_binary` is just RTP + payload.

RTP payload types: **99** for H.264 video, **8** for PCMA audio, **0** for
PCMU. The receiver routes by payload type, not by channel.

H.264 arrives as FU-A fragments, STAP-A aggregates, or single NALs. Two
consumers run in parallel: raw RTP is passed straight to the RTSP server (the
low-latency live path), while a NAL reassembly → PyAV decode → JPEG path
produces stills for the camera entity and the last-ring snapshot.

---

## 10. Audio

**Codec: G.711 A-law (PCMA), payload type 8, 8 kHz, 20 ms frames of exactly
160 bytes.** The device speaks only A-law.

Audio does **not** start with video. On an inbound call it begins only after
the answer sequence completes and the device opens its own RTPC — roughly
400 ms after the peer/accept. On outbound (HA-initiated) calls the device
sends a single `0x1840/0x0070` peer/accept but does **not** send PCMA in
response; audio only flows on inbound calls and on lease renewals, where the
`0x1860/0x0070` renewal peer re-opens it.

### Sending audio to the device

Send one 160-byte frame every 20 ms, on the transport matching the session
(see §9), with the ICONA request id set to the **device's own RTPC channel
id**. RTP sequence increments by 1, timestamp by 160 per frame, SSRC random.

Three details that produce audible artifacts if ignored:

- **Buffer, don't sample.** Upstream audio (from a browser via go2rtc) arrives
  in bursts of arbitrary size over TCP. Taking one queued packet per 20 ms tick
  interleaves silence frames between real ones — heard as scratchiness. Drain
  the queue into a byte buffer and emit exactly 160 bytes per tick.
- **Re-chunk, don't truncate.** Payloads larger than 160 bytes must be split
  across ticks, not clipped.
- **Convert µ-law.** If the browser negotiated PCMU, translate to A-law before
  queueing. µ-law bytes played as A-law are pure distortion. (Python 3.13
  removed `audioop`; a 256-entry translation table is computed at import.)

Bound the buffer (this implementation trims beyond ~800 ms) so talk-back
latency cannot grow without limit.

---

## 11. Home Assistant end-to-end: RTSP, go2rtc, WebRTC

This section documents the HA-side plumbing, which is as much a source of
subtle failure as the ICONA protocol itself.

```
intercom ──ICONA/RTP──▶ rtp_receiver ──▶ local RTSP server (127.0.0.1:8557)
                                              │
                                              ▼
                                          go2rtc  ──WebRTC──▶ browser / app
                                              ▲
       intercom ◀──ICONA/RTP── rtp_receiver ──┘   (mic, via RTSP backchannel)
```

### 11.1 The local RTSP server

A single persistent server, started at integration setup and never stopped
between calls, so go2rtc can hold a producer open. It serves H.264 (PT 96) and
PCMA (PT 8), TCP-interleaved or UDP.

**Timestamps must never jump backwards.** HA's stream worker and go2rtc both
stay connected across call boundaries; a device that restarts its RTP clock
each call would look like a discontinuity and stall the decoder for tens of
seconds. Output timestamps are therefore rebased: `out = device_ts + offset`,
with the offset recomputed on first frame, on an explicit reset, or on a
detected backwards jump.

**Audio pauses follow RFC 3550/3551.** When transmission pauses (silence
suppression between calls), the RTP timestamp still advances by wall-clock
time, and the first packet after the gap sets the **marker bit**. Emitting
contiguous timestamps across a real pause produces time-compressed audio that
drifts against video. A gap is treated as a pause at ≥ 0.5 s; below that the
smooth +160/frame cadence absorbs normal jitter (the 6701W's TCP bursts show
legitimate 0.11–0.21 s inter-burst gaps mid-call).

Between calls, silent PCMA at the correct cadence keeps a browser's WebRTC
audio track alive. During a live call silence is never injected.

### 11.2 SDP direction attributes — the trap

RTSP/ONVIF direction attributes are written **from the client's perspective**.
A main audio track marked `a=sendonly` therefore reads as *"the client sends
this"* — go2rtc treats it as a microphone backchannel and never subscribes to
it, so the viewer gets video with no audio, and idle connections time out.

Correct shape:

- **Forward audio** (intercom → viewer): a plain `m=audio` line with
  `a=control:audio` and **no direction attribute**.
- **Backchannel** (viewer mic → intercom): a *second* `m=audio` line marked
  `a=sendonly` with `a=control:backchannel`, advertised **only** when the
  client requested it via the `Require: www.onvif.org/ver20/backchannel`
  header on DESCRIBE.

Advertising the backchannel unconditionally breaks go2rtc's WebRTC track
mapping (duplicate payload type on an unrequested media), which manifests as a
completely dead stream — not merely a missing mic.

The client SETUPs the backchannel by URL suffix; after PLAY, mic RTP arrives on
that interleaved channel as `$`-framed binary. Strip the RTP header (skipping
CSRCs and any extension header), validate PT ∈ {0, 8}, and queue the payload
for the sender described in §10.

### 11.3 WebRTC signalling through Home Assistant

HA brokers WebRTC for camera entities over its authenticated WebSocket. The
integration implements a `CameraWebRTCProvider`; the three websocket commands a
client uses are:

| Command | Purpose |
|---|---|
| `camera/webrtc/get_client_config` | Returns `{configuration: {iceServers: [...]}}`, including Nabu Casa TURN when the cloud integration is active |
| `camera/webrtc/offer` | Subscription: emits `session` (with `session_id`), then `answer`, then `candidate` events |
| `camera/webrtc/candidate` | Client → server trickled ICE candidates, keyed by `session_id` |

**Trickle ICE is not optional.** A provider that answers an offer in one shot
and has no candidate path causes HA core to raise *"Cannot handle WebRTC
candidate"* for every candidate the client trickles — which kills the session.
The provider must open a per-session signalling channel and implement
`async_on_webrtc_candidate` and `close_webrtc_session`.

This integration proxies signalling to go2rtc's `/api/ws?src=<stream>`
endpoint, whose message shapes are:

```
client → go2rtc : {"type": "webrtc/offer",     "value": "<sdp>"}
client → go2rtc : {"type": "webrtc/candidate", "value": "<candidate string>"}
go2rtc → client : {"type": "webrtc/answer",    "value": "<sdp>"}
go2rtc → client : {"type": "webrtc/candidate", "value": "<candidate string>"}
```

Two client-side details, both learned the hard way:

- **go2rtc's answer carries no msid.** `ontrack` events arrive with an empty
  `event.streams` array, so a player that assigns `event.streams[0]` to the
  video element gets nothing. Accumulate `event.track` into a locally
  constructed `MediaStream` instead.
- **Remote candidates may lack `sdpMid`.** Supply `sdpMid: "0"` when absent, or
  `addIceCandidate` rejects them.

Because signalling rides HA's own websocket, the same code path works on a
local `http://` URL and on a remote HTTPS URL, and TURN relay for remote
viewing comes from HA's client config automatically.

### 11.4 Microphone availability — a hard browser constraint

`navigator.mediaDevices.getUserMedia` is gated on a **secure context**. On a
plain `http://` origin `navigator.mediaDevices` is `undefined`; the mic cannot
be captured at all. This is a Chromium rule with no application-level
workaround, and it applies **inside the Home Assistant companion app**, whose
WebView is Chromium (the app's permission plumbing is correct — the request
simply never fires). Requests to relax it upstream were closed as not feasible.

Consequences for a doorbell:

| Connection | Video + entrance audio | Talk back |
|---|---|---|
| Local `http://` | ✅ | ❌ — impossible in-browser |
| HTTPS (cloud or local TLS) | ✅ | ✅ |

A card should therefore feature-detect and *say* which mode it is in, rather
than presenting a mic control that cannot work. Serving HA over local HTTPS is
the way to get talk-back at home without routing through the cloud.

As of HA 2026.8 the **built-in** WebRTC player is receive-only — it creates
`recvonly` transceivers and contains no `getUserMedia` call, so two-way audio
requires a custom card regardless of origin.

### 11.5 Session lifecycle

Video sessions are leased, not permanent: the device drops the call about every
30 s (§7) and this integration also applies a 120 s session cap. When a session
ends, restarting it unconditionally leaves the camera streaming forever after a
single ring. Restart only while something is actually watching — the RTSP
server's active client count is the signal, since go2rtc holds a producer
connection only while it has a consumer.

---

## 12. Firmware and model differences

Recorded because they are the likely failure points on hardware other than the
one this was developed against. **Only the 6701W column is verified here** —
everything in the right-hand column is secondhand (see
[Provenance](#0-provenance-and-verification-status)).

| Behaviour | 6701W (this repo, PCAP-verified) | Other models (community reports) |
|---|---|---|
| Renewal ACK increment | `0x01010000` | 6742W reported as `0x01010000`; one source reports `0x01000000` for the 6701W — **not** what this device does |
| Addressing | Kit / single-house mode, `S`-prefixed (`SB100001`) | Apartment-block systems use plain numeric addresses (`00000100`) |
| Address in ring frame | As stored in the address book | Kit-mode devices may report the caller *without* the mode prefix (`B100001` for `SB100001`), so comparisons must tolerate a dropped prefix |
| Floor call ("fuoriporta") | Not observed | 6741W distinguishes it with two ASCII bytes immediately before the `FF FF FF FF` marker: `PP` = entrance panel, `FF` = floor door |
| Event delivery | Local CTPP only | 6742W supports local CTPP *or* cloud FCM |

Address parsing that assumes the `SB` prefix will fail on apartment-block
hardware; a regex of the form `(?:SB)?[0-9A-Fa-f]{6,9}` covers both.

---

## 13. Other channels and unexplored surface

> Everything in this section is **code-derived or secondhand** — none of it has
> been observed on the wire here.

**FRCG — face recognition.** The device runs a face-recognition pipeline
locally. On a ring it captures a face image and emits, on the `FRCG` channel
(same JSON-over-ICONA framing as UAUT/UCFG):

- `rcg-detected-recognition` — similarity score, bounding box, and an HTTPS URL
  pointing at Comelit's cloud
- `rcg-detected-image` — the local filesystem path of the capture
  (`/etc/comelit/recognition/detected/unknown/<timestamp>.jpg`)

Opening this channel alongside CTPP would let the captured image feed HA's
vision tooling entirely locally. Not implemented.

**Hardware discovery.** A UDP `INFO` datagram to port **24199** returns
hardware details including the MAC address.

**Open ports observed:** 53 (DNS), 8080 (HTTP web UI), 8443 (HTTPS),
64100 (ICONA).

**User provisioning.** A dedicated identity can be created entirely locally
through the device's web UI: log in, create an "Apps"-type user in a free slot,
generate an activation code, read it from the slot's `.mug` pairing file, and
redeem it on `UAUT` with a `user-activation` message. Slot 0 is the wall
monitor and must never be touched; free slots should be identified from a
configuration backup rather than by probing `.mug` files, since an already
activated user has none and would be silently overwritten. Not implemented.

---

## Sources and credits

- PCAP captures of this repository's own device (6701W, firmware 2.x) — the
  basis for everything not otherwise marked
- [antoiba86/hass-comelit-intercom-local](https://github.com/antoiba86/hass-comelit-intercom-local) — the local video stack this integration forked from
- [nicolas-fricke/ha-component-comelit-intercom](https://github.com/nicolas-fricke/ha-component-comelit-intercom) — earlier integration in the same lineage
- [simllll/hass-comelit-icona](https://github.com/simllll/hass-comelit-icona) — sibling fork; source of the 6742W/6741W observations, the floor-call tag, and the ONVIF backchannel convention
- [grdw — "My intercom" series](https://grdw.nl/2023/01/28/my-intercom-part-1.html) — early ICONA reverse engineering
- [madchicken/comelit-client](https://github.com/madchicken/comelit-client) — independent client implementation
