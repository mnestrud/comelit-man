/**
 * Comelit Intercom Card — Play-to-start intercom camera card.
 *
 * Shows the camera snapshot with a play button overlay. Clicking play
 * starts the video session. Navigating away stops it automatically.
 *
 * Install:
 *   The Lovelace resource is registered automatically on HA startup.
 *
 *   Add card to dashboard (YAML):
 *      type: custom:comelit-intercom-card
 *      camera_entity: camera.comelit_intercom_live_feed
 *      start_entity: button.comelit_intercom_start_video   # optional
 *      stop_entity:  button.comelit_intercom_stop_video_feed
 *
 *   Optional quick-open chips (v2): a row of buttons under the video, one per
 *   destination. Each chip calls the comelit_man.open_entrance service, which
 *   routes the panel's camera/door selector to that camera and fires the door
 *   relay (starting the video session first if needed). With select_entity
 *   set, the chip whose label matches the selector's current option is
 *   highlighted.
 *      open_options: ["Urbanización", "Casa"]
 *      select_entity: select.comelit_intercom_camera_selector   # optional
 */
class ComelitIntercomCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._config = null;
    // _streaming mirrors the camera entity state ("streaming" vs "idle").
    // This is the source of truth for which view to show — driving the UI
    // from a local flag caused picture-entity to be mounted before
    // stream_source() had a URL ready, which made HA cache MJPEG as the
    // transport and never upgrade to MSE/WebRTC.
    this._streaming = false;
    // _startRequested is set when the user presses play.  While true and
    // camera state has not yet flipped to "streaming", we show a loading
    // overlay instead of the play button — avoids a confusing double-click.
    this._startRequested = false;
    this._liveCard = null;
    this._onLocationChanged = null;
    this._pendingChip = null;
    this.attachShadow({ mode: "open" });
  }

  // -------------------------------------------------------------------------
  // Lovelace lifecycle
  // -------------------------------------------------------------------------

  setConfig(config) {
    if (!config.camera_entity) {
      throw new Error("Missing required config: camera_entity");
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._liveCard) this._liveCard.hass = hass;
    this._syncFromState();
  }

  _syncFromState() {
    if (!this._hass || !this._config) return;
    const state = this._hass.states[this._config.camera_entity];
    const nowStreaming = state?.state === "streaming";
    const firstSync = this._lastState === undefined;
    const changed = nowStreaming !== this._streaming;
    this._lastState = state?.state;
    if (changed) {
      this._streaming = nowStreaming;
      if (nowStreaming) {
        this._startRequested = false;
        this._showLive();
      } else {
        this._showIdle();
      }
      return;
    }
    // Populate the thumbnail once on first render so the idle view isn't blank.
    if (firstSync && !nowStreaming) this._refreshThumbnail();
    this._syncChips();
  }

  connectedCallback() {
    // Listen for HA navigation to stop video when user leaves the view.
    // Many Lovelace panel types hide views with CSS instead of removing
    // elements from the DOM, so disconnectedCallback alone is not enough.
    this._onLocationChanged = () => {
      setTimeout(() => {
        if (!this.isConnected || !this._isVisible()) {
          this._requestStop();
        }
      }, 0);
    };
    window.addEventListener("location-changed", this._onLocationChanged);
  }

  disconnectedCallback() {
    window.removeEventListener("location-changed", this._onLocationChanged);
    this._onLocationChanged = null;
    this._requestStop();
  }

  getCardSize() {
    return 4;
  }

  static getStubConfig() {
    return {
      camera_entity: "camera.comelit_intercom_live_feed",
      start_entity: "button.comelit_intercom_start_video",
      stop_entity: "button.comelit_intercom_stop_video_feed",
      select_entity: "select.comelit_intercom_camera_selector",
      open_options: ["Camera 1", "Camera 2"],
    };
  }

  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }

        /* Idle view — thumbnail + play button */
        .idle {
          position: relative;
          background: #000;
          /* Maintain 800×480 (5:3) aspect ratio */
          aspect-ratio: 5 / 3;
          width: 100%;
          cursor: pointer;
        }
        .thumbnail {
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
        }
        .play-btn {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .play-circle {
          width: 72px;
          height: 72px;
          border-radius: 50%;
          background: rgba(0, 0, 0, 0.55);
          border: 2.5px solid rgba(255, 255, 255, 0.85);
          display: flex;
          align-items: center;
          justify-content: center;
          transition: background 0.15s, transform 0.15s;
        }
        .idle:hover .play-circle {
          background: rgba(0, 0, 0, 0.8);
          transform: scale(1.08);
        }
        .play-circle svg {
          fill: #fff;
          width: 34px;
          height: 34px;
          margin-left: 5px; /* optical centering for play triangle */
        }

        /* Loading spinner shown between play click and streaming state */
        .loading-circle {
          width: 72px;
          height: 72px;
          border-radius: 50%;
          background: rgba(0, 0, 0, 0.55);
          border: 2.5px solid rgba(255, 255, 255, 0.85);
          border-top-color: transparent;
          animation: spin 1s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Live view — stream + stop button */
        .live { display: none; position: relative; }
        .stop-btn {
          position: absolute;
          top: 8px;
          right: 8px;
          width: 32px;
          height: 32px;
          border-radius: 50%;
          background: rgba(0, 0, 0, 0.55);
          border: none;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 10;
          transition: background 0.15s;
        }
        .stop-btn:hover { background: rgba(180, 0, 0, 0.75); }
        .stop-btn svg { fill: #fff; width: 16px; height: 16px; }

        /* Quick-open chips (v2) — one button per destination */
        .chips {
          display: none; /* shown when open_options is configured */
          gap: 8px;
          flex-wrap: wrap;
          padding: 8px 10px;
        }
        .chip {
          padding: 6px 14px;
          border-radius: 18px;
          border: none;
          background: rgba(0, 0, 0, 0.55);
          color: #fff;
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
          transition: background 0.15s, opacity 0.15s;
        }
        .chip:hover { background: rgba(0, 0, 0, 0.8); }
        .chip.active { background: var(--primary-color, #03a9f4); }
        .chip.pending {
          opacity: 0.6;
          pointer-events: none;
        }
      </style>

      <ha-card>
        <!-- Idle state: snapshot + play button (or spinner while starting) -->
        <div class="idle" id="idle">
          <img class="thumbnail" id="thumbnail" />
          <div class="play-btn" id="play-btn">
            <div class="play-circle" id="play-circle">
              <!-- Material play icon -->
              <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
            </div>
          </div>
        </div>

        <!-- Live state: stream card + stop button -->
        <div class="live" id="live">
          <button class="stop-btn" id="stop-btn" title="Stop video">
            <!-- Material stop icon -->
            <svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>
          </button>
          <div id="stream-slot"></div>
        </div>

        <!-- Quick-open chips (populated from open_options, if configured) -->
        <div class="chips" id="chips"></div>
      </ha-card>
    `;

    this.shadowRoot.getElementById("idle").addEventListener("click", () => {
      this._requestStart();
    });
    this.shadowRoot.getElementById("stop-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      this._requestStop();
    });
    this._renderChips();
  }

  // -------------------------------------------------------------------------
  // Quick-open chips (v2)
  // -------------------------------------------------------------------------

  _renderChips() {
    const container = this.shadowRoot.getElementById("chips");
    if (!container) return;
    container.innerHTML = "";
    const options = Array.isArray(this._config?.open_options)
      ? this._config.open_options
      : [];
    if (!options.length) return;
    container.style.display = "flex";
    for (const option of options) {
      const btn = document.createElement("button");
      btn.className = "chip";
      btn.textContent = option;
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._openEntrance(option, btn);
      });
      container.appendChild(btn);
    }
    this._syncChips();
  }

  _syncChips() {
    if (!this._hass) return;
    const container = this.shadowRoot.getElementById("chips");
    if (!container) return;
    const selectState = this._config?.select_entity
      ? this._hass.states[this._config.select_entity]?.state
      : null;
    for (const btn of container.querySelectorAll(".chip")) {
      btn.classList.toggle("active", !!selectState && btn.textContent === selectState);
    }
  }

  async _openEntrance(option, btn) {
    if (!this._hass || this._pendingChip) return;
    this._pendingChip = option;
    if (btn) btn.classList.add("pending");
    try {
      await this._hass.callService("comelit_man", "open_entrance", { label: option });
    } catch (err) {
      console.error(`comelit-intercom-card: open_entrance('${option}') failed`, err);
    } finally {
      this._pendingChip = null;
      if (btn) btn.classList.remove("pending");
      this._syncChips();
    }
  }

  // -------------------------------------------------------------------------
  // Video state — the camera entity state ("streaming" vs "idle") is the
  // source of truth; button presses are just triggers that ask the backend
  // to flip it.  The card reacts through _syncFromState when state arrives.
  // -------------------------------------------------------------------------

  _requestStart() {
    if (this._streaming || this._startRequested) return;
    if (!this._hass || !this._config || !this._config.start_entity) return;
    this._startRequested = true;
    this._showStartingSpinner();
    this._hass.callService("button", "press", {
      entity_id: this._config.start_entity,
    });
  }

  _requestStop() {
    this._startRequested = false;
    if (this._hass && this._config && this._config.stop_entity) {
      this._hass.callService("button", "press", {
        entity_id: this._config.stop_entity,
      });
    }
  }

  async _showLive() {
    // Build the inner live card using HA helpers so the element is fully
    // upgraded before setConfig is called (document.createElement alone
    // returns an unupgraded element without setConfig).  Built lazily here,
    // not eagerly on play click, so stream_source() already has a URL —
    // picture-entity then picks MSE/WebRTC instead of caching MJPEG.
    const slot = this.shadowRoot.getElementById("stream-slot");
    if (!slot) return;
    slot.innerHTML = "";
    const helpers = await window.loadCardHelpers();
    const card = await helpers.createCardElement({
      type: "picture-entity",
      entity: this._config.camera_entity,
      camera_view: "live",
      show_name: false,
      show_state: false,
    });
    card.hass = this._hass;
    // Guard against late arrival — if state flipped back to idle while we
    // were awaiting the helpers, don't mount a stale live card.
    if (!this._streaming) return;
    this._liveCard = card;
    slot.appendChild(card);
    this.shadowRoot.getElementById("idle").style.display = "none";
    this.shadowRoot.getElementById("live").style.display = "block";
  }

  _showIdle() {
    // Fully tear down the live card — leaving picture-entity mounted after
    // stream_source() starts returning None puts it in an infinite retry
    // loop, which is what the user sees as "camera keeps trying to load".
    const slot = this.shadowRoot.getElementById("stream-slot");
    if (slot) slot.innerHTML = "";
    this._liveCard = null;
    this.shadowRoot.getElementById("live").style.display = "none";
    this.shadowRoot.getElementById("idle").style.display = "";
    this._showPlayButton();
    this._refreshThumbnail();
  }

  _showStartingSpinner() {
    const playBtn = this.shadowRoot.getElementById("play-circle");
    if (!playBtn) return;
    playBtn.className = "loading-circle";
    playBtn.innerHTML = "";
  }

  _showPlayButton() {
    const playBtn = this.shadowRoot.getElementById("play-circle");
    if (!playBtn) return;
    playBtn.className = "play-circle";
    playBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  _refreshThumbnail() {
    if (!this._hass || !this._config) return;
    const state = this._hass.states[this._config.camera_entity];
    const token = state?.attributes?.access_token;
    const img = this.shadowRoot.getElementById("thumbnail");
    if (img && token) {
      img.src = `/api/camera_proxy/${this._config.camera_entity}?token=${token}&t=${Date.now()}`;
    }
  }

  _isVisible() {
    // getBoundingClientRect() returns zero dimensions for elements hidden
    // anywhere in their ancestor chain (including across shadow DOM).
    const rect = this.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0;
  }
}

customElements.define("comelit-intercom-card", ComelitIntercomCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "comelit-intercom-card",
  name: "Comelit Intercom Camera",
  description: "Intercom camera with play button — click to start, auto-stops on navigation.",
});
