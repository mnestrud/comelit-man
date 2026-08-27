/**
 * Comelit Doorbell Card — Notification card for doorbell ring events.
 *
 * Idle:     Camera thumbnail with a doorbell icon overlay.
 * Ringing:  Live video + pulsing icon overlay + "Answer" / "Dismiss" buttons.
 *           Auto-dismisses after `dismiss_after` seconds (default 30).
 *           Video starts automatically (passive inbound — the call is not
 *           answered; other stations keep ringing until Answer is pressed).
 * Answered: Live video + stop button only. Answer starts two-way audio.
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
 *     dismiss_after:   30   # optional, seconds
 */
class ComelitDoorbellCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._config = null;
    this._state = "idle"; // idle | ringing | answered
    this._lastEventTs = null;
    this._dismissTimer = null;
    this._liveCard = null;
    this._onLocationChanged = null;
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
    if (this._liveCard) this._liveCard.hass = hass;
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
    if (this._state !== "idle") {
      this._callStop();
      this._teardownLiveCard();
    }
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

    // Create live card once — persists into answered state
    if (this._config.camera_entity && !this._liveCard) {
      const helpers = await window.loadCardHelpers();
      this._liveCard = await helpers.createCardElement({
        type: "picture-entity",
        entity: this._config.camera_entity,
        camera_view: "live",
        show_name: false,
        show_state: false,
      });
      this._liveCard.hass = this._hass;
      const slot = this.shadowRoot.getElementById("stream-slot");
      if (slot) slot.appendChild(this._liveCard);
    }
  }

  _answer() {
    this._clearDismissTimer();
    this._state = "answered";
    this._updateView();

    // Audio only — passive video is already streaming from the ring
    if (this._config.answer_entity) {
      this._hass.callService("button", "press", {
        entity_id: this._config.answer_entity,
      });
    }
  }

  _dismiss() {
    this._clearDismissTimer();
    this._callStop();
    this._teardownLiveCard();
    this._state = "idle";
    this._updateView();
    this._refreshThumbnail();
  }

  _callStop() {
    if (this._hass && this._config?.stop_entity) {
      this._hass.callService("button", "press", {
        entity_id: this._config.stop_entity,
      });
    }
  }

  _teardownLiveCard() {
    const slot = this.shadowRoot.getElementById("stream-slot");
    if (slot) slot.innerHTML = "";
    this._liveCard = null;
  }

  _clearDismissTimer() {
    if (this._dismissTimer) {
      clearTimeout(this._dismissTimer);
      this._dismissTimer = null;
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
        #stream-slot {
          width: 100%; height: 100%;
        }
        #stream-slot > * {
          width: 100%; height: 100%; display: block;
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
        .btn-dismiss {
          background: rgba(255, 255, 255, 0.15);
          color: #fff;
          border: 1px solid rgba(255, 255, 255, 0.45);
        }

        /* Answered overlay — just the stop button */
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

        <!-- Active: live stream with ringing or answered overlay -->
        <div id="active">
          <div id="stream-slot"></div>

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
              <button class="btn btn-dismiss" id="dismiss-btn">Dismiss</button>
            </div>
          </div>

          <!-- Answered overlay -->
          <div id="answered-overlay" style="display:none">
            <button class="stop-btn" id="stop-btn" title="Stop video">
              <svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>
            </button>
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
    "Doorbell notification card — live video preview on ring, Answer starts two-way audio.",
});
