# Comelit Man

Home Assistant custom component for the **Comelit 6701W** WiFi video intercom. Communicates via the ICONA Bridge TCP protocol — no cloud required.

## Features

- **Remote door opening** — open doors/gates from Home Assistant
- **Live intercom video** — view the door camera stream directly in HA dashboards via local RTSP
- **Doorbell events** — automations trigger on ring or missed call
- **Custom Lovelace card** — play-button UI auto-registered on startup; starts video on click, stops on navigation away
- **100% local** — all communication stays on your LAN, no cloud required

## Requirements

- Comelit 6701W (or compatible ICONA Bridge device)
- Device accessible on your local network
- Home Assistant 2026.1+

## Supported devices

**Tested and confirmed working:**

| Device | Firmware | Notes |
|--------|----------|-------|
| Comelit 6701W | 2.x | All features — door open, video, doorbell events |

**Likely compatible (same ICONA Bridge protocol):**
- Other Comelit WiFi video door panels using the ICONA Bridge protocol on port 64100
- Compatibility is not guaranteed for devices with different firmware lines

The integration communicates via the **ICONA Bridge TCP protocol** on port 64100. If your device accepts TCP connections on that port and responds to UAUT/UCFG/CTPP messages, it is likely compatible.

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Install **Comelit Man**
3. Restart Home Assistant

### Manual

1. Copy the `custom_components/comelit_man/` folder to your HA `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Comelit Man**
3. Enter your device IP and either:
   - Your device password (token will be extracted automatically), or
   - A pre-extracted 32-character hex token

### Notification settings

After setup, you can configure the integration via **Settings → Integrations → Comelit Man → Configure**:

| Option | Default | Description |
|--------|---------|-------------|
| Enable notifications | On | Receive doorbell ring and door events. Disable if you only need video and door control, or to troubleshoot the notification connection. |

Changing this setting reloads the integration automatically.

## Known limitations

- **Single concurrent video session** — only one video stream can be active at a time. Starting a second session stops the first.
- **WiFi sleep** — the 6701W disconnects from WiFi when idle (no active call). Wake it physically (ring the bell, or press a button) before testing network connectivity. The integration reconnects automatically once the device wakes.
- **No cloud** — requires direct LAN access to the device. Remote access via VPN or a reverse proxy is your responsibility.
- **Fixed RTSP port** — the integration uses a fixed RTSP port (8557). If another process is using that port at HA startup, the video feed will not work. Change the port in HA configuration or free up 8557.
- **Door open timing** — when video is active, pressing a door button opens the door then stops video after 10 seconds. This matches the Android app's behavior.

## Removing the integration

1. Go to **Settings → Devices & Services**
2. Find **Comelit Man** and open it
3. Click the three-dot menu → **Delete**
4. Confirm deletion
5. Restart Home Assistant

The integration does not persist any state outside the config entry, so no manual cleanup is needed. The device's push-channel registration will lapse naturally once the keepalive probes stop.

## Entities

| Entity | Description |
|--------|-------------|
| `button.comelit_intercom_<door_name>` | Press to open a door or gate (e.g., `button.comelit_intercom_actuator`) |
| `button.comelit_intercom_start_video_feed` | Manually start the intercom video call |
| `button.comelit_intercom_stop_video_feed` | Stop the active video call |
| `camera.comelit_intercom_live_feed` | Live video stream from the door panel via local RTSP |
| `camera.comelit_intercom_<name>` | RTSP stream from each additional configured camera |
| `event.comelit_intercom_doorbell` | Fires `doorbell_ring` and `missed_call` events for automations |

### Lovelace Cards

Two custom cards are automatically registered on startup — both are optional.

**Intercom camera card** — snapshot with play button overlay; click to start video, stops on navigation away:

```yaml
type: custom:comelit-intercom-card
camera_entity: camera.comelit_intercom_live_feed
start_entity: button.comelit_intercom_start_video_feed  # optional
stop_entity: button.comelit_intercom_stop_video_feed
```

**Doorbell notification card** — shows a pulsing alert with Answer/Dismiss buttons when someone rings; auto-dismisses after `dismiss_after` seconds:

```yaml
type: custom:comelit-doorbell-card
doorbell_entity: event.comelit_intercom_doorbell
camera_entity: camera.comelit_intercom_live_feed
start_entity: button.comelit_intercom_start_video_feed
stop_entity: button.comelit_intercom_stop_video_feed
dismiss_after: 30  # optional, default 30s
```

States: **Idle** (thumbnail + doorbell badge) → **Ringing** (pulsing icon + Answer/Dismiss) → **Answered** (live stream + stop button).

### Doorbell Notifications

When someone rings the doorbell, `event.comelit_intercom_doorbell` fires a `doorbell_ring` event. Video does **not** start automatically — you decide what happens via automations.

**Basic notification:**

```yaml
alias: "Notify on doorbell ring"
mode: single
triggers:
  - platform: state
    entity_id: event.comelit_intercom_doorbell
    to: "doorbell_ring"
conditions: []
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "Doorbell"
      message: "Someone is at the door!"
```

**Notification with action button to open the camera view:**

```yaml
alias: "Doorbell ring with camera shortcut"
mode: single
triggers:
  - platform: state
    entity_id: event.comelit_intercom_doorbell
    to: "doorbell_ring"
conditions: []
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "Doorbell"
      message: "Someone is at the door!"
      data:
        actions:
          - action: URI
            title: "Open Camera"
            uri: /lovelace/intercom
```

**Auto-start video on ring (opt-in):**

If you want the video to start automatically when the doorbell rings:

```yaml
alias: "Doorbell ring — notify and start video"
mode: single
triggers:
  - platform: state
    entity_id: event.comelit_intercom_doorbell
    to: "doorbell_ring"
conditions: []
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "Doorbell"
      message: "Someone is at the door!"
  - action: button.press
    target:
      entity_id: button.comelit_intercom_start_video_feed
```

## Data update mechanism

The integration uses a **push-first, poll-for-health** model:

| Mechanism | What it does | Frequency |
|-----------|-------------|-----------|
| VIP event listener | Receives doorbell ring, missed call, and door-opened events as binary messages on the CTPP channel | Instant (device pushes) |
| FCM keepalive probe | Re-sends push-info registration every 90 s; device ACKs, keeping the idle TCP connection alive | Every 90 s |
| Health-check poll | Verifies the TCP connection is still alive; triggers reconnect if not | Every 30 s |
| TCP disconnect callback | Schedules an immediate health-check when the client detects a TCP drop | Instant (on drop) |

Entity availability mirrors coordinator connectivity: all entities become **unavailable** if the device is unreachable and recover automatically on reconnect.

## Protocol

The ICONA Bridge protocol runs over raw TCP on port 64100. Every message has an 8-byte header:

```
[0x00 0x06] [body_length LE16] [request_id LE16] [0x00 0x00]
```

Key operations:
- **Authentication**: Open UAUT channel → send JSON access request with token → expect code 200
- **Configuration**: Open UCFG channel → request config → parse doors, cameras, addresses
- **VIP events**: Persistent CTPP channel — binary messages for doorbell ring, door opened, renewal ACK; replaces FCM-based PUSH for reliable local event delivery
- **Door open (video active)**: Single `0x1840/0x000D` message on the existing video CTPP channel — PCAP-verified from Android app local traffic capture
- **Door open (VIP listener active, no video)**: Reuse open CTPP, fire open+confirm directly — no init overhead (~30 ms)
- **Door open (notifications disabled)**: Open transient CTPP channel → full init → 6-step binary sequence → close
- **Push channel**: Registers FCM token; also used as a 90s keepalive probe — device ACKs with JSON, preventing false reconnect cycles

## Troubleshooting

### Enable debug logging

Add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.comelit_man: debug
```

Then restart HA. Debug logs include TCP connection events, channel opens, keepalive probes, VIP events, and video session lifecycle.

### Common problems

**"Cannot connect" during setup**
- Confirm the device is awake (ping it, or physically interact with it)
- Verify the IP address and that port 64100 is reachable (`telnet <ip> 64100`)
- Check your router's firewall isn't blocking local LAN traffic

**Authentication failed**
- If using a password, confirm it matches the device web interface password (default: `comelit`)
- Re-extract the token via the web interface: `http://<device-ip>:8080`
- After a firmware update, the token may change — delete and re-add the integration

**Video doesn't start**
- Ensure no other app is using the ICONA Bridge (the device accepts only one TCP client at a time)
- Check HA logs for CTPP negotiation errors
- Port 8557 conflict: check if another process is using it (`netstat -an | grep 8557`)

**Doorbell events not firing**
- Enable notifications in **Settings → Integrations → Comelit Man → Configure**
- The VIP listener requires an active CTPP channel; check logs for "Failed to start VIP event listener"

**Entities show as unavailable**
- The integration is reconnecting to the device; wait 30–60 s
- If it stays unavailable, check the device is on the network and debug logs for reconnect errors

## Changelog

### 0.1.4

> **⚠ Breaking change — entity IDs have changed**
>
> Entity IDs are now derived from the integration's **title** instead of the hardcoded string `"Comelit Intercom"`.
> If you added the integration before this version, your entity IDs may have changed (e.g. from
> `button.comelit_intercom_actuator` to `button.comelit_192_168_1_111_actuator` if no custom name was set).
>
> **Fix:** remove and re-add the integration, giving it a friendly name (e.g. `Front Door`) in the new Name field.
> Entities will then be stable going forward (e.g. `button.front_door_actuator`).

- **Custom integration name** — new optional "Name" field in the config flow sets the integration title and entity prefix; leave blank to use the host IP
- **Options flow** — enable or disable doorbell notifications after setup via Settings → Integrations → Configure without removing and re-adding the integration
- **Reliable doorbell detection** — replaced the FCM-based PUSH mechanism with a persistent CTPP channel listener (VIP events); actual call events are now received as binary messages on the device's local TCP channel, not via cloud FCM
- **Doorbell notification card** — new `comelit-doorbell-card` auto-registered on startup; shows ring alert with Answer/Dismiss buttons and transitions to live stream when answered
- **Door open during active video** — pressing a door button while video is active sends a single message on the existing CTPP channel (PCAP-verified Android app behaviour); no second TCP connection
- **Faster door open** — when notifications are enabled, the CTPP channel is already open so door open skips the init handshake entirely (~30 ms vs ~2 s)
- **Single shared TCP connection** — video signaling, VIP event listening, and door control share the coordinator's TCP connection; eliminates conflicts when the device only accepts one client at a time
- **Door auto-stop** — pressing a door button while video is active automatically stops the video session 10 s later
- **Faster time-to-first-frame** — RTSP `PLAY` response is gated until video RTP is flowing, preventing HA's stream worker from erroring on an empty stream; RTCP Sender Reports eliminate "no reference clock" delays in go2rtc, VLC, and browsers
- **Accurate camera state** — `is_streaming` property reflects the active session so the Lovelace card transitions correctly and go2rtc attaches via WebRTC on the first video session
- **TCP keepalive probe** — push-info re-sent every 90 s keeps the connection alive during idle periods; prevents false reconnect cycles when the device is reachable but quiet

### 0.1.3
- **Video renewal** — inline re-establishment on CALL_END (~30s) without TCP reconnect; video is uninterrupted
- **Custom Lovelace card** — play-button UI auto-registered on HA startup; no manual resource configuration needed
- **Concurrent session protection** — a second video start while one is in progress is immediately rejected, preventing CTPP negotiation conflicts
- **TCP video fallback** — video works via TCP (RTPC2) when UDP is blocked by NAT/firewall
- **Consistent entity naming** — all entities use the `comelit_intercom_` prefix (e.g., `button.comelit_intercom_actuator`, `camera.comelit_intercom_live_feed`)

## Acknowledgments

Protocol knowledge derived from community reverse-engineering efforts:
- [ha-component-comelit-intercom](https://github.com/nicolas-fricke/ha-component-comelit-intercom) by Nicolas Fricke
- [comelit-client](https://github.com/madchicken/comelit-client) by Pierpaolo Follia
- [Protocol analysis](https://grdw.nl/2023/01/28/my-intercom-part-1.html) by grdw

## License

Apache 2.0
