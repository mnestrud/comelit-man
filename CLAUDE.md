# Comelit Man — Claude Code Context

## MANDATORY SESSION STARTUP

Run ALL of these before responding to any user message.

1. `git -C /home/ataraxia/code/comelit-man status`
2. `git -C /home/ataraxia/code/comelit-man log --oneline -5`
3. Read `custom_components/comelit_man/manifest.json` → note version
4. Read memory file `memory/comelit_man_audit.md` → note (a) quality tier, (b) `Last full sweep` date, (c) `**Stale rows:**` count from the summary block at the top.

**Output before anything else:**
```
STARTUP OK | branch: <name> | version: <x.y.z> | quality: <tier> | audit: <YYYY-MM-DD> | STALE: <count>
```
- `audit:` is the `Last full sweep` date.
- `STALE:` is the `**Stale rows:**` count.
- If `STALE` ≥ 1 OR the audit date is more than 90 days ago, flag it on the next line and ask the user whether to refresh the affected rows before continuing. A row is stale when its `Verified` date is >90 days old OR older than the current `manifest.json` minor version.

This checklist is not optional. "Resume directly" does not skip it.

---

## Overview

Home Assistant custom component for the **Comelit 6701W** WiFi video intercom. Communicates entirely locally via the **ICONA Bridge TCP protocol** on port 64100 — no cloud dependency. Fork of [antoiba86/hass-comelit-intercom-local](https://github.com/antoiba86/hass-comelit-intercom-local).

---

## Repo Structure

| Path | Role |
|------|------|
| `custom_components/comelit_man/__init__.py` | HA integration setup; registers card JS static paths + Lovelace resources |
| `custom_components/comelit_man/config_flow.py` | UI config flow with auto token extraction + options flow (enable_notifications) |
| `custom_components/comelit_man/coordinator.py` | DataUpdateCoordinator; owns shared TCP client, RTSP server, video session, VIP listener, keepalive loop |
| `custom_components/comelit_man/const.py` | All constants and defaults |
| `custom_components/comelit_man/strings.json` | UI strings |
| `custom_components/comelit_man/translations/en.json` | Mirrors strings.json (required by HA) |
| `custom_components/comelit_man/manifest.json` | Integration metadata |
| `custom_components/comelit_man/button.py` | Door open + Start/Stop video button entities |
| `custom_components/comelit_man/camera.py` | Camera entity; is_streaming property |
| `custom_components/comelit_man/event.py` | Doorbell ring / missed call event entities |
| `custom_components/comelit_man/protocol.py` | Wire protocol: 8-byte header, message types, binary payloads |
| `custom_components/comelit_man/client.py` | AsyncIO TCP client for ICONA Bridge; TCP keepalives; 120s read timeout |
| `custom_components/comelit_man/door.py` | Door open paths (stable — edit carefully, test on real device) |
| `custom_components/comelit_man/video_call.py` | Video call signaling (stable — outbound + inbound flows; edit carefully) |
| `custom_components/comelit_man/vip_listener.py` | Persistent VIP event listener on CTPP channel |
| `custom_components/comelit_man/rtsp_server.py` | Local RTSP server: H.264; RTCP Sender Reports; PLAY gating |
| `custom_components/comelit_man/rtp_receiver.py` | UDP/TCP RTP receiver: H.264 FU-A→PyAV→JPEG + PCMA audio routing |
| `tests/conftest.py` | Shared fixtures |
| `tests/test_*.py` | One file per source module |
| `.github/workflows/validate.yml` | CI: HACS, hassfest, ruff check+format, mypy, pytest |

Platforms: `BUTTON, CAMERA, EVENT` | Min HA: `2026.1.0` | Repo: `https://github.com/mnestrud/comelit-man`

---

## Running Tests Locally

**Working directory: `/home/ataraxia/code/comelit-man`**

```bash
# First time — create venv
uv venv .venv
uv pip install -p .venv/bin/python pytest pytest-asyncio aiohttp av pytest-cov pyturbojpeg homeassistant freezegun syrupy pytest-aiohttp
```

⚠️ Do NOT install `pytest-homeassistant-custom-component` — its bundled pytest-socket plugin
blocks socket creation and fails the two `test_video_flow_integration.py` tests (verified
2026-08-11). Install `homeassistant` directly instead, as above.

```bash
# All tests
.venv/bin/pytest tests/ -v

# Unit tests only (no device needed)
.venv/bin/pytest tests/test_protocol.py tests/test_client.py tests/test_rtp_receiver.py tests/test_rtsp_server.py tests/test_token.py tests/test_video_call.py tests/test_video_signaling.py -v

# Stop on first failure
.venv/bin/pytest tests/ -x --tb=short
```

Integration tests (real device required):
```bash
COMELIT_HOST=192.168.113.12 COMELIT_TOKEN=<token> .venv/bin/pytest tests/test_integration.py -v -s
```

---

## Stable Files — Edit with Care

`door.py` and `video_call.py` are stable and fully tested. They can be edited, but changes to either should be tested against the real device before committing — protocol bugs here are hard to catch in unit tests.

**Flow Protection Rule:** For any shared file (`client.py`, `coordinator.py`, `protocol.py`, `ctpp.py`, `channels.py`, `rtp_receiver.py`, `rtsp_server.py`): if a proposed change touches code paths used by the video feed flow or door opening flow, **stop and ask the user before making the change**.

---

## Branch and PR Workflow

- **`dev`** — all development. Never commit directly to main.
- **`main`** — merged from dev via PR only; always tagged with a release.
- Feature branches: from dev, PR back to dev.

**PR checklist before merging dev → main:**
- [ ] All CI checks pass (validate workflow)
- [ ] `manifest.json` version bumped (semver)
- [ ] Docs updated if behavior or config changed
- [ ] `memory/comelit_man_audit.md` updated if any rule status changed

---

## CI/CD Workflows

| Workflow | Trigger | What it checks |
|----------|---------|----------------|
| `validate.yml` | Every push + PR | HACS → hassfest → ruff check (E,F,I,UP,B,SIM,ASYNC,LOG,RUF,TC,PERF,C90,S) → ruff format --check → mypy → pytest |

---

## Development and Deploy Workflow

**Source of truth: git repo. Test target: live HA via the /mnt/ha-config mount. Two separate steps.**

### Step 1 — Edit and test locally
1. Edit files in `/home/ataraxia/code/comelit-man/custom_components/comelit_man/`
2. Run `pytest tests/ --tb=short` to catch regressions

### Step 2 — Deploy to live HA for integration testing
```bash
rsync -a --delete /home/ataraxia/code/comelit-man/custom_components/comelit_man/ /mnt/ha-config/custom_components/comelit_man/
```
- **Python changes** (any `.py` file): full HA restart required — use `ha_restart` MCP call; do NOT poll after, tell user to confirm when ready
- **Non-Python changes** (strings.json, translations): reload only — `ha_reload_config component=core`

### Step 2b — Validate on live HA (after user confirms restart complete)
Invoke `ha-integration-validator` agent: "Validate comelit_man on live HA"

### Step 3 — Commit and push
```bash
git add <changed files>
git commit -m "..."
git push origin dev
```

---

## Agent Usage

| When | Use |
|------|-----|
| HA entity API signatures, coordinator/flow patterns, HA breaking changes | `ha-dev` agent |
| After rsync deploy + restart confirmed | `ha-integration-validator` agent |
| General Python/testing questions | Answer directly |

---

## Quality Scale

**Tier status (v1.0.2):**
- Bronze — EFFECTIVE PASS (`brands` accepted-FAIL, won't fix; all other rules PASS)
- Silver — **MET** (BL-023 DONE: 98% total coverage, rtsp_server.py 100%, 759 tests)
- Gold — **MET** (19/19 applicable rules PASS)
- Platinum — **MET** (all 3 rules PASS)

Full audit checklist: `memory/comelit_man_audit.md`
Quality scale rules: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules

No remaining quality-scale work. Coverage is 100% across all files.

---

## ICONA Bridge Protocol

All communication is raw TCP on port **64100**. Every message has an 8-byte header:

```
[0x00 0x06] [body_length LE16] [request_id LE16] [0x00 0x00]
```

### Channels and Flow

1. **UAUT** — Authentication: open channel → send JSON access request with token → expect code 200
2. **UCFG** — Configuration: request config → parse doors, cameras, apt_address
3. **PUSH** — Notifications: registers FCM token; keepalive probe (re-send push-info every 90s)
4. **CTPP** — Persistent channel for VIP events (doorbell ring, door opened) and door control
5. **UDPM/RTPC** — Video call signaling (uses `trailing_byte=1`)

### Critical Protocol Rules

- **Channel open sequence must always be 1** — device ignores packets with seq != 1
- **Timeout must be >= 30s** — device can be very slow to respond
- **Request ID** starts semi-random (8000+) and increments per message
- JSON messages use compact format: `separators=(",", ":")`

---

## Key Entities

All entities use `_attr_has_entity_name = True`. Entity IDs reflect the user-configured name set during setup.

| Entity | Description |
|--------|-------------|
| `button.<name>_<door_name>` | Press to open door/gate; stops video 10s after if active |
| `event.<name>_doorbell` | Fires `doorbell_ring` and `missed_call` events |
| `camera.<name>_live_feed` | Live video stream from intercom |
| `button.<name>_start_video_feed` | Manually trigger video call |
| `button.<name>_stop_video_feed` | Stop active video call |

---

## Door Control

Three code paths selected automatically by `coordinator.async_open_door`:

**Path 1 — video active** (`video_call.py`): single `0x1840/0x000D` message on existing CTPP channel.

**Path 2 — VIP listener active, no video** (`door.py` → `open_door`, fast path): reuse open CTPP channel; skips init handshake.

**Path 3 — no CTPP channel open** (`door.py` → `open_door`, standalone path): opens transient `CTPP_DOOR` channel with full `ctpp_init_sequence`.

---

## Video Streaming

- `video_call.py` handles TCP signaling; reuses open CTPP when VIP listener is active
- `rtp_receiver.py`: ICONA header → RTP → H.264 FU-A → PyAV → JPEG; PCMA audio routing
- `rtsp_server.py`: H.264 over local RTSP (TCP interleaved); monotonic timestamps rebased across calls
- **Persistent RTSP server** owned by coordinator — started at HA setup, never stopped between calls
- **`_video_ready_event`** gates `stream_source()` and RTSP `PLAY` handler during CTPP handshake
- **`_video_start_lock`** prevents concurrent `async_start_video` calls
- RTCP Sender Reports every 5s for NTP/RTP sync
- Inline re-establishment on CALL_END (~30s): ACK → refresh → no TCP reconnect

---

## Audio Streaming

- Audio does NOT auto-start — requires explicit "answer" sequence after video starts
- **Codec: PCMA G.711 A-law, PT=8, 20ms frames (160 bytes/frame)**
- Answer sequence: `encode_answer_video_reconfig` → `encode_answer_peer` → `encode_answer_config_ack`
- Audio arrives on same UDP port as video, distinguished by RTP payload type (PT=8)

---

## Testing Device

- Host: `192.168.113.12`, HTTP port: `8080`, ICONA port: `64100`
- Credentials: `admin` / `comelit`, token in `.env` (COMELIT_TOKEN)
- Config: apt_address=SB000006, apt_subaddress=1, 2 doors (Actuator, Entrance Lock), 0 cameras

---

## Lovelace Cards

Both cards registered on HA startup via `StaticPathConfig` (HA 2024.7+).

**Intercom camera card** (`www/comelit-intercom-card.js`): snapshot with play button; click to start video.

**Doorbell notification card** (`www/comelit-doorbell-card.js`): Idle → Ringing → Answered states; auto-dismisses after `dismiss_after` seconds.

---

## HA Debug Logging

```yaml
logger:
  default: info
  logs:
    custom_components.comelit_man: debug
```

---

## Coding Conventions

- AsyncIO throughout — all network I/O is async
- Protocol encoding/decoding lives in `protocol.py`; business logic in channel-specific modules
- Compact JSON serialization (`separators=(",",":")`) for all messages to device
- Exceptions defined in `exceptions.py`
- pytest with `asyncio_mode = "auto"` — async test functions work without decorator
- Ruff enforces: E, F, I, UP, B, SIM, ASYNC, LOG, RUF, TC, PERF, C90 (max 15), S — see `pyproject.toml` for per-file ignores
- `ruff format` is the formatter (HA standard); run locally before committing
- Logging uses `%`-style lazy args (not f-strings) per HA guidelines

---

## Device Behavior & Quirks

- The intercom has a DIP switch for always-on WiFi — this device is configured with it enabled; the device does NOT sleep and does not need to be woken before network tests
- Open ports: **53** (DNS), **8080** (HTTP), **8443** (HTTPS), **64100** (ICONA)
- Send UDP `INFO` to port **24199** for hardware discovery (MAC address, etc.)
- VIP listener receives actual call events (doorbell ring, door opened) on CTPP — NOT on PUSH channel
- Door IDs from device can be non-unique; `index` field on Door model provides unique entity IDs
- Events deduplicated within 10s window to suppress device retransmissions

---

## Future Opportunities

### Inbound call answer (complete)
When a visitor presses the doorbell, the integration auto-answers via `_on_inbound_ring` → `async_start_inbound_video` (coordinator) → `VideoCallSession.start_inbound()` (video_call.py). An "Answer Doorbell" button entity triggers `answer_inbound()` for two-way audio. Live-tested on device 2026-05-30: 547 video frames + 676 audio frames received, 551 audio frames sent.

### Face recognition via FRCG channel
The device has a built-in face recognition pipeline. When a ring occurs, the device:
1. Captures a face image from the video stream
2. Sends `rcg-detected-recognition` JSON on the **FRCG** channel with similarity score, bounding box, and an HTTPS image URL pointing to Comelit's cloud (`eps.cloud.comelitgroup.com`)
3. Sends `rcg-detected-image` JSON with the local filesystem path of the captured image (`/etc/comelit/recognition/detected/unknown/<timestamp>.jpg`)

**Opportunity**: open the FRCG channel alongside CTPP during coordinator setup, receive these events, and pipe the captured image into HA's vision/AI capabilities (e.g. `image_processing`, a local Frigate/Doods integration, or an LLM vision call) to identify visitors and trigger automations — all locally, without Comelit's cloud.

The FRCG channel uses the same JSON-over-ICONA framing as UAUT/UCFG. The `rcg-detected-recognition` payload includes enough metadata (bounding box, existing similarity score) to know whether the device already matched the face against its own database.

### Full protocol reference
See `memory/protocol_reference.md` for complete PCAP-derived wire format documentation (ICONA header, CTPP binary format, channel types, inbound/outbound call sequences, audio).

---

## Reference

- [Original fork source](https://github.com/antoiba86/hass-comelit-intercom-local) — antoiba86
- [Protocol analysis Part 1](https://grdw.nl/2023/01/28/my-intercom-part-1.html) — grdw (reverse engineering ICONA)
- [comelit-client](https://github.com/madchicken/comelit-client) — Pierpaolo Follia
- `memory/protocol_reference.md` — full PCAP-derived protocol reference (this repo)
