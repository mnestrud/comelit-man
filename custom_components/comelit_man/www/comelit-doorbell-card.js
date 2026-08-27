/**
 * Comelit Doorbell Card — answer station for doorbell ring events.
 *
 * Idle:     Camera thumbnail with a doorbell icon overlay.
 * Ringing:  Live WebRTC video (muted) + pulsing icon + "Answer" / "Dismiss".
 *           Auto-dismisses after `dismiss_after` seconds (default 30).
 *           Video starts automatically (passive inbound — the call is not
 *           answered; other stations keep ringing until Answer is pressed).
 * Answered: Live video, RX audio unmuted, mic transmitting when available.
 *
 * Built-in WebRTC player — no third-party card dependency:
 *   - Signaling rides HA's authenticated WebSocket (camera/webrtc/offer via
 *     the integration's native provider), identical on local http and cloud
 *     https URLs.
 *   - ICE servers come from camera/webrtc/get_client_config, which includes
 *     Nabu Casa TURN when the cloud integration is active — remote media
 *     works away from home.
 *   - Microphone: getUserMedia requires a secure context (https). On a plain
 *     http origin the mic is impossible in any browser/webview (Chromium
 *     rule, no workaround — home-assistant/android#3512); the card then
 *     shows a "mic needs HTTPS" chip and still delivers see-and-hear.
 *
 * Install:
 *   The Lovelace resource is registered automatically on HA startup.
 *
 *   Add card to dashboard (YAML):
 *     type: custom:comelit-doorbell-card
 *     doorbell_entity: event.comelit_intercom_doorbell
 *     camera_entity:   camera.comelit_intercom_live_feed
 *     answer_entity:   button.comelit_intercom_answer_doorbell
 *     stop_entity:     button.comelit_intercom_stop_video_feed
 *     door_entity:     button.comelit_intercom_entrance_lock   # optional
 *     dismiss_after:   30   # optional, seconds
 *     always_live:     false # optional — true keeps the stream up when idle
 */
class ComelitDoorbellCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._config = null;
    this._state = "idle"; // idle | ringing | answered
    this._lastEventTs = null;
    this._dismissTimer = null;
    this._onLocationChanged = null;
    // WebRTC session state
    this._pc = null;
    this._unsub = null;
    this._micStream = null;
    this._micAvailable = false;
    this._starting = false;
    this._retryTimer = null;
    this._mediaStream = null;
    this._sessionId = null;
    this._candQueue = [];
    this.attachShadow({ mode: "open" });
  }

  // ---------------------------------------------------------------------------
  // Lovelace lifecycle
  // ---------------------------------------------------------------------------

  setConfig(config) {
    if (!config.doorbell_entity) {
      throw new Error("Missing required config: doorbell_entity");
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._checkDoorbellState();
    if (this._state === "idle") this._refreshThumbnail();
  }

  connectedCallback() {
    this._onLocationChanged = () => {
      setTimeout(() => {
        if (!this.isConnected || !this._isVisible()) this._dismiss();
      }, 0);
    };
    window.addEventListener("location-changed", this._onLocationChanged);
  }

  disconnectedCallback() {
    window.removeEventListener("location-changed", this._onLocationChanged);
    this._onLocationChanged = null;
    this._clearDismissTimer();
    if (this._state !== "idle") this._callStop();
    this._teardownWebrtc();
    this._state = "idle";
  }

  getCardSize() {
    return 4;
  }

  static getStubConfig() {
    return {
      doorbell_entity: "event.comelit_intercom_doorbell",
      camera_entity: "camera.comelit_intercom_live_feed",
      answer_entity: "button.comelit_intercom_answer_doorbell",
      stop_entity: "button.comelit_intercom_stop_video_feed",
      dismiss_after: 30,
    };
  }

  // ---------------------------------------------------------------------------
  // Doorbell state detection
  // ---------------------------------------------------------------------------

  _checkDoorbellState() {
    if (!this._hass || !this._config) return;
    const entity = this._hass.states[this._config.doorbell_entity];
    if (!entity) return;

    const lastChanged = entity.last_changed;
    if (lastChanged === this._lastEventTs) return;
    this._lastEventTs = lastChanged;

    if (entity.attributes?.event_type !== "ring") return;

    // Ignore stale events (older than dismiss_after) — e.g. on HA restart
    const dismissMs = (this._config.dismiss_after ?? 30) * 1000;
    const age = Date.now() - new Date(lastChanged).getTime();
    if (age > dismissMs) return;

    if (this._state !== "answered") this._showRinging();
  }

  // ---------------------------------------------------------------------------
  // State transitions
  // ---------------------------------------------------------------------------

  async _showRinging() {
    this._state = "ringing";
    this._updateView();
    this._clearDismissTimer();
    const dismissMs = (this._config.dismiss_after ?? 30) * 1000;
    this._dismissTimer = setTimeout(() => this._dismiss(), dismissMs);
    this._startWebrtc();
  }

  async _answer() {
    this._clearDismissTimer();
    this._state = "answered";
    this._updateView();

    // Unmute RX audio — this click is the user gesture autoplay policy wants.
    const video = this.shadowRoot.getElementById("stream");
    if (video) {
      video.muted = false;
      video.play().catch(() => {});
    }
    // Enable the mic track (captured at connect when the origin allows it).
    this._setMicEnabled(true);

    // Device-side answer: opens the audio RTPC, starts PCMA TX.
    if (this._config.answer_entity) {
      this._hass.callService("button", "press", {
        entity_id: this._config.answer_entity,
      });
    }
  }

  _dismiss() {
    this._clearDismissTimer();
    this._callStop();
    this._teardownWebrtc();
    this._state = "idle";
    this._updateView();
    this._refreshThumbnail();
  }

  _openDoor() {
    if (this._hass && this._config?.door_entity) {
      this._hass.callService("button", "press", {
        entity_id: this._config.door_entity,
      });
    }
  }

  _callStop() {
    if (this._hass && this._config?.stop_entity) {
      this._hass.callService("button", "press", {
        entity_id: this._config.stop_entity,
      });
    }
  }

  _clearDismissTimer() {
    if (this._dismissTimer) {
      clearTimeout(this._dismissTimer);
      this._dismissTimer = null;
    }
  }

  // ---------------------------------------------------------------------------
  // WebRTC player (native HA signaling)
  // ---------------------------------------------------------------------------

  async _startWebrtc() {
    if (this._pc || this._starting) return;
    const entityId = this._config?.camera_entity;
    if (!entityId || !this._hass) return;
    this._starting = true;
    this._setStatus("Connecting…");

    try {
      // ICE config from HA — includes Nabu Casa TURN when cloud is active.
      let rtcConfig = {};
      try {
        const cc = await this._hass.connection.sendMessagePromise({
          type: "camera/webrtc/get_client_config",
          entity_id: entityId,
        });
        rtcConfig = cc?.configuration ?? {};
      } catch (err) {
        // Older HA or transient error — proceed with defaults.
      }

      const pc = new RTCPeerConnection(rtcConfig);
      this._pc = pc;

      pc.addTransceiver("video", { direction: "recvonly" });

      // Mic: only possible on a secure origin (https / localhost).  On http
      // navigator.mediaDevices is undefined by Chromium design — degrade to
      // listen-only and say so.
      this._micAvailable = false;
      if (navigator.mediaDevices?.getUserMedia) {
        try {
          this._micStream = await navigator.mediaDevices.getUserMedia({
            audio: true,
          });
          const track = this._micStream.getAudioTracks()[0];
          track.enabled = false; // transmit only after Answer
          pc.addTransceiver(track, { direction: "sendrecv" });
          this._micAvailable = true;
        } catch (err) {
          // Permission denied / no device — listen-only.
          pc.addTransceiver("audio", { direction: "recvonly" });
        }
      } else {
        pc.addTransceiver("audio", { direction: "recvonly" });
      }
      this._updateMicChip();

      const video = this.shadowRoot.getElementById("stream");
      this._mediaStream = new MediaStream();
      pc.ontrack = (ev) => {
        if (!video) return;
        // go2rtc answers don't always carry stream (msid) associations —
        // accumulate tracks into our own MediaStream when ev.streams is empty.
        const stream = ev.streams[0] ?? this._mediaStream;
        if (!ev.streams[0]) this._mediaStream.addTrack(ev.track);
        if (video.srcObject !== stream) {
          video.srcObject = stream;
        }
        video.muted = this._state !== "answered";
        video.play().catch(() => {});
        this._setStatus("");
      };

      pc.onconnectionstatechange = () => {
        if (!this._pc) return;
        if (["failed", "closed"].includes(pc.connectionState)) {
          this._scheduleRetry();
        }
      };

      // Trickle ICE: candidates are forwarded through camera/webrtc/candidate
      // once the session id arrives; queue any that beat it.
      this._sessionId = null;
      this._candQueue = [];
      pc.onicecandidate = (ev) => {
        if (!ev.candidate?.candidate) return;
        this._sendCandidate(ev.candidate);
      };

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      this._unsub = await this._hass.connection.subscribeMessage(
        (msg) => this._onSignal(msg),
        {
          type: "camera/webrtc/offer",
          entity_id: entityId,
          offer: pc.localDescription.sdp,
        }
      );
    } catch (err) {
      this._setStatus(`Stream error: ${err?.message ?? err}`);
      this._scheduleRetry();
    } finally {
      this._starting = false;
    }
  }

  _onSignal(msg) {
    const pc = this._pc;
    if (!pc) return;
    if (msg.type === "session") {
      this._sessionId = msg.session_id;
      const queued = this._candQueue.splice(0);
      queued.forEach((c) => this._sendCandidate(c));
    } else if (msg.type === "answer") {
      pc.setRemoteDescription({ type: "answer", sdp: msg.answer }).catch(
        (err) => this._setStatus(`Stream error: ${err?.message ?? err}`)
      );
    } else if (msg.type === "candidate" && msg.candidate) {
      pc.addIceCandidate(msg.candidate).catch(() => {});
    } else if (msg.type === "error") {
      this._setStatus(`Stream error: ${msg.message ?? msg.code}`);
      this._scheduleRetry();
    }
  }

  _sendCandidate(candidate) {
    if (!this._sessionId) {
      this._candQueue.push(candidate);
      return;
    }
    this._hass.connection
      .sendMessagePromise({
        type: "camera/webrtc/candidate",
        entity_id: this._config.camera_entity,
        session_id: this._sessionId,
        candidate: {
          candidate: candidate.candidate,
          sdpMid: candidate.sdpMid,
          sdpMLineIndex: candidate.sdpMLineIndex,
        },
      })
      .catch(() => {});
  }

  _scheduleRetry() {
    // A doorbell must not silently spin: tear down and retry once shortly,
    // as long as the card is still in an active state.
    this._teardownWebrtc();
    if (this._state === "idle" || this._retryTimer) return;
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      if (this._state !== "idle") this._startWebrtc();
    }, 2000);
  }

  _teardownWebrtc() {
    if (this._retryTimer) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
    if (this._unsub) {
      try {
        this._unsub();
      } catch (err) {
        /* connection may be gone */
      }
      this._unsub = null;
    }
    if (this._micStream) {
      this._micStream.getTracks().forEach((t) => t.stop());
      this._micStream = null;
    }
    if (this._pc) {
      try {
        this._pc.close();
      } catch (err) {
        /* already closed */
      }
      this._pc = null;
    }
    const video = this.shadowRoot?.getElementById("stream");
    if (video) video.srcObject = null;
    this._mediaStream = null;
    this._sessionId = null;
    this._candQueue = [];
  }

  _setMicEnabled(enabled) {
    if (!this._micStream) return;
    this._micStream.getAudioTracks().forEach((t) => {
      t.enabled = enabled;
    });
    this._updateMicChip();
  }

  _micEnabled() {
    return !!this._micStream?.getAudioTracks().some((t) => t.enabled);
  }

  _toggleMic() {
    if (!this._micAvailable) return;
    this._setMicEnabled(!this._micEnabled());
  }

  _updateMicChip() {
    const chip = this.shadowRoot.getElementById("mic-chip");
    if (!chip) return;
    if (this._micAvailable) {
      const on = this._micEnabled();
      chip.textContent = on ? "🎤 mic on" : "🎤 mic muted — tap to talk";
      chip.classList.toggle("mic-on", on);
      chip.classList.remove("mic-unavailable");
      chip.style.cursor = "pointer";
    } else {
      chip.textContent = "🎤 mic needs HTTPS — use the cloud URL to talk";
      chip.classList.add("mic-unavailable");
      chip.classList.remove("mic-on");
      chip.style.cursor = "default";
    }
  }

  _setStatus(text) {
    const el = this.shadowRoot.getElementById("status");
    if (el) {
      el.textContent = text;
      el.style.display = text ? "" : "none";
    }
  }

  // ---------------------------------------------------------------------------
  // View update
  // ---------------------------------------------------------------------------

  _updateView() {
    const idle = this.shadowRoot.getElementById("idle");
    const active = this.shadowRoot.getElementById("active");
    const ringOverlay = this.shadowRoot.getElementById("ring-overlay");
    const answeredOverlay = this.shadowRoot.getElementById("answered-overlay");
    if (!idle || !active || !ringOverlay || !answeredOverlay) return;

    const isActive = this._state !== "idle";
    idle.style.display = isActive ? "none" : "";
    active.style.display = isActive ? "" : "none";
    ringOverlay.style.display = this._state === "ringing" ? "" : "none";
    answeredOverlay.style.display = this._state === "answered" ? "" : "none";
    this._updateMicChip();
  }

  _refreshThumbnail() {
    if (!this._hass || !this._config?.camera_entity) return;
    const state = this._hass.states[this._config.camera_entity];
    const token = state?.attributes?.access_token;
    if (!token) return;
    const url = `/api/camera_proxy/${this._config.camera_entity}?token=${token}&t=${Date.now()}`;
    const img = this.shadowRoot.getElementById("thumbnail");
    if (img) img.src = url;
  }

  _isVisible() {
    const rect = this.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0;
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }

        .view {
          position: relative;
          background: #111;
          aspect-ratio: 5 / 3;
          width: 100%;
        }
        .thumbnail {
          width: 100%; height: 100%;
          object-fit: cover; display: block;
        }

        /* Idle overlay — subtle doorbell badge */
        .idle-overlay {
          position: absolute;
          bottom: 0; left: 0; right: 0;
          padding: 8px 12px;
          background: linear-gradient(transparent, rgba(0,0,0,0.55));
          color: rgba(255,255,255,0.85);
          font-size: 12px;
          display: flex; align-items: center; gap: 6px;
        }
        .idle-overlay svg { flex-shrink: 0; }

        /* Active view (ringing + answered): stream fills the area */
        #active {
          display: none;
          position: relative;
          background: #111;
          aspect-ratio: 5 / 3;
          width: 100%;
        }
        #stream {
          width: 100%; height: 100%;
          object-fit: cover; display: block;
          background: #111;
        }
        #status {
          position: absolute; top: 8px; left: 8px;
          padding: 4px 10px; border-radius: 12px;
          background: rgba(0,0,0,0.6); color: #fff;
          font-size: 12px; z-index: 5;
        }

        /* Ringing overlay — sits on top of live stream */
        #ring-overlay {
          position: absolute;
          inset: 0;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 16px;
          background: rgba(0, 0, 0, 0.45);
          pointer-events: auto;
        }
        .ring-icon {
          width: 68px; height: 68px;
          border-radius: 50%;
          background: var(--primary-color, #03a9f4);
          display: flex; align-items: center; justify-content: center;
          animation: pulse 1.1s ease-in-out infinite;
        }
        .ring-icon svg { fill: #fff; width: 36px; height: 36px; }
        @keyframes pulse {
          0%, 100% {
            transform: scale(1);
            box-shadow: 0 0 0 0 rgba(3, 169, 244, 0.55);
          }
          50% {
            transform: scale(1.08);
            box-shadow: 0 0 0 14px rgba(3, 169, 244, 0);
          }
        }
        .ring-label {
          color: #fff;
          font-size: 16px; font-weight: 500;
          text-shadow: 0 1px 4px rgba(0, 0, 0, 0.8);
        }
        .ring-actions { display: flex; gap: 12px; }
        .btn {
          padding: 10px 26px;
          border: none; border-radius: 24px;
          font-size: 14px; font-weight: 500;
          cursor: pointer; transition: opacity 0.15s;
        }
        .btn:hover { opacity: 0.85; }
        .btn-answer { background: #4caf50; color: #fff; }
        .btn-door { background: #ff9800; color: #fff; }
        .btn-dismiss {
          background: rgba(255, 255, 255, 0.15);
          color: #fff;
          border: 1px solid rgba(255, 255, 255, 0.45);
        }

        /* Answered overlay — controls along the edges, video visible */
        #answered-overlay {
          position: absolute;
          inset: 0;
          pointer-events: none;
        }
        .stop-btn {
          position: absolute; top: 8px; right: 8px;
          width: 32px; height: 32px;
          border-radius: 50%;
          background: rgba(0, 0, 0, 0.55);
          border: none; cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          z-index: 10; transition: background 0.15s;
          pointer-events: auto;
        }
        .stop-btn:hover { background: rgba(180, 0, 0, 0.75); }
        .stop-btn svg { fill: #fff; width: 16px; height: 16px; }
        .answered-bar {
          position: absolute; bottom: 8px; left: 8px; right: 8px;
          display: flex; align-items: center; gap: 8px;
          pointer-events: auto;
        }
        #mic-chip {
          padding: 6px 12px; border-radius: 14px;
          background: rgba(0,0,0,0.6); color: #fff;
          font-size: 12px; user-select: none;
        }
        #mic-chip.mic-on { background: rgba(76,175,80,0.85); }
        #mic-chip.mic-unavailable { background: rgba(0,0,0,0.6); color: rgba(255,255,255,0.7); }
        .door-btn-small {
          margin-left: auto;
          padding: 8px 16px;
          border: none; border-radius: 18px;
          background: rgba(255,152,0,0.9); color: #fff;
          font-size: 13px; font-weight: 500; cursor: pointer;
        }
      </style>

      <ha-card>
        <!-- Idle: thumbnail + subtle badge -->
        <div class="view" id="idle">
          <img class="thumbnail" id="thumbnail" />
          <div class="idle-overlay">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="rgba(255,255,255,0.85)">
              <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/>
            </svg>
            Doorbell
          </div>
        </div>

        <!-- Active: WebRTC stream with ringing or answered overlay -->
        <div id="active">
          <video id="stream" autoplay playsinline muted></video>
          <div id="status" style="display:none"></div>

          <!-- Ringing overlay -->
          <div id="ring-overlay">
            <div class="ring-icon">
              <svg viewBox="0 0 24 24">
                <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/>
              </svg>
            </div>
            <div class="ring-label">Someone at the door</div>
            <div class="ring-actions">
              <button class="btn btn-answer" id="answer-btn">Answer</button>
              <button class="btn btn-door" id="ring-door-btn" style="display:none">Open Door</button>
              <button class="btn btn-dismiss" id="dismiss-btn">Dismiss</button>
            </div>
          </div>

          <!-- Answered overlay -->
          <div id="answered-overlay" style="display:none">
            <button class="stop-btn" id="stop-btn" title="Hang up">
              <svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>
            </button>
            <div class="answered-bar">
              <div id="mic-chip"></div>
              <button class="door-btn-small" id="door-btn" style="display:none">Open Door</button>
            </div>
          </div>
        </div>
      </ha-card>
    `;

    this.shadowRoot
      .getElementById("answer-btn")
      .addEventListener("click", () => this._answer());
    this.shadowRoot
      .getElementById("dismiss-btn")
      .addEventListener("click", () => this._dismiss());
    this.shadowRoot
      .getElementById("stop-btn")
      .addEventListener("click", () => this._dismiss());
    this.shadowRoot
      .getElementById("mic-chip")
      .addEventListener("click", () => this._toggleMic());
    const doorBtn = this.shadowRoot.getElementById("door-btn");
    const ringDoorBtn = this.shadowRoot.getElementById("ring-door-btn");
    if (this._config?.door_entity) {
      doorBtn.style.display = "";
      ringDoorBtn.style.display = "";
      doorBtn.addEventListener("click", () => this._openDoor());
      ringDoorBtn.addEventListener("click", () => this._openDoor());
    }
  }
}

if (!customElements.get("comelit-doorbell-card")) {
  customElements.define("comelit-doorbell-card", ComelitDoorbellCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "comelit-doorbell-card",
  name: "Comelit Doorbell",
  description:
    "Doorbell answer station — built-in WebRTC (native HA signaling + cloud TURN), two-way audio on HTTPS origins.",
});
