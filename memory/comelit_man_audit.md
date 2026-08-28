# Comelit Man — Quality Audit

**Last full sweep:** Sweep 6 — 2026-08-28 at v1.5.0 (all 52 tier rules re-verified against code; evidence anchors refreshed). Prior full sweep: Sweep 5 — 2026-05-06 with fix bundles through 2026-05-28.

**Sweep 6 headline (2026-08-28):** coverage restored to **100%** (4014/4014 stmts, 1064 tests passed / 9 skipped, all 32 source files at 100%); strict typing hardened (0 `type: ignore`, mypy strict clean). Bronze `brands` FAIL→PASS (Core 2026.3 local brand images; `brand/icon.png` present, HACS passes). Two Gold regressions found: `docs-examples` and `docs-use-cases` PASS→FAIL — the automation YAML examples the old rows cited (README.md:91-150) were lost in the 1.x README rewrites; only prose mentions remain. `exception-translations` PASS→PARTIAL — raise sites without translation keys: `auth.py:34`, `door.py:101`, `__init__.py:115-119`, `coordinator.py:928,930`. **All three deferred by user decision 2026-08-28** (BL-039/BL-040). Gold is therefore **NOT MET** at v1.5.0.

**Deprecation sweep 2026-08-28** against the HA developer blog, cross-referenced against the code and the live 2026.8.3 instance. Resolved: config-entry update listener (would have become an error in **2026.12**) → `OptionsFlowWithReload`; `DhcpServiceInfo` canonical import; typed `LOVELACE_DATA` key; `AddConfigEntryEntitiesCallback`; minimum HA declared in `hacs.json`. Already compliant: doorbell `ring` standard event type (deadline **2027.4**, renamed in 1.1.1); all passed deadlines (`async_forward_entry_setups`, `StaticPathConfig`, 2025.6 camera WebRTC migration, Camera/Lock state enums, options-flow and reauth entry linking). **Outstanding:** `DeviceInfo(via_device=…)` → `via_device_id` in `camera.py` (deadline **2027.8**; deferred because `via_device_id` needs a device id not available at entity construction). Not applicable: legacy device tracker, `labs.async_listen`, condition/script callables, `async_initialize_triggers`, service-helper `hass` arg, `show_advanced_options`, manual `entity_id`, remaining device-registry items. The Lovelace card uses no `ha-*` components, so the 2026.4–2026.8 frontend removals do not apply.

**Version at audit:** 1.5.0
**Tier claim (CLAUDE.md):** Bronze MET; Silver MET; Gold NOT MET (2 docs FAIL + 1 PARTIAL, user-deferred); Platinum MET
**Tier verdict (audited):** **Bronze MET** (16 PASS / 2 N/A — brands now PASS); **Silver MET** (9 PASS / 1 N/A; coverage 100%); **Gold NOT MET** (16 PASS / 2 FAIL / 1 PARTIAL / 2 N/A — see BL-039/BL-040, user-deferred 2026-08-28); **Platinum MET** (3 PASS); Beyond A-D 13/14 PASS (B still PARTIAL: `video_call.py:521` bare `asyncio.create_task  # noqa: RUF006`); Beyond E 8 PASS / 14 N/A; Beyond F 4 PASS / 1 accepted-FAIL; Beyond G 4 PASS / 1 N/A; Beyond H 2 PASS (spot-checked 2026-08-28, not fully re-swept)
**Stale rows:** 0 (all tier rows re-verified 2026-08-28). When this becomes ≥1, schedule re-verification of the affected rows.
**Next review due:** +90 days from 2026-08-28, or on next minor version, whichever first
**Freshness rule:** any row is `STALE` if `Verified` date > 90 days old OR older than the current `manifest.json` minor version (`1.5.x`).

---

## Sources (drive every row below)

| Source | URL | Used by |
|---|---|---|
| HA Integration Quality Scale | https://developers.home-assistant.io/docs/core/integration-quality-scale/ | All tiers |
| HA Quality Scale checklist | https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist | Per-rule rows |
| HA Architecture Decision Records | https://github.com/home-assistant/architecture/tree/master/adr | Beyond-scale E |
| HA developer docs (file structure) | https://developers.home-assistant.io/docs/creating_integration_file_structure/ | Bronze structure |
| HA Brands repo | https://github.com/home-assistant/brands | Bronze brands, BL-014 |
| HACS publish (integration) | https://www.hacs.xyz/docs/publish/integration/ | Beyond-scale F |
| HACS validation action | https://github.com/hacs/action | Beyond-scale G |
| hassfest | https://developers.home-assistant.io/docs/creating_integration_manifest/ | Beyond-scale G |
| pytest-homeassistant-custom-component | https://github.com/MatthewFlamm/pytest-homeassistant-custom-component | BL-013 |
| HA Diagnostics platform | https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/diagnostics | Silver/Gold |
| HA Repairs platform | https://developers.home-assistant.io/docs/core/platform/repairs/ | Gold, BL-008 |

---

## Tier Summary (dashboard)

Status legend: `PASS | FAIL | PARTIAL | N/A | STALE | UNVERIFIED`
Verdict is `MET` only when every rule in the tier is `PASS` or `N/A`.

| Tier | Pass | Fail | Partial | N/A | Stale | Unverified | Total | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Bronze   | 16 | 0 | 0 | 2 | 0 | 0 | 18 | MET — `brands` FAIL→PASS 2026-08-28 (local brand images, Core 2026.3) |
| Silver   | 9 | 0 | 0 | 1 | 0 | 0 | 10 | MET — coverage 100% (4014/4014 stmts, 1064 tests) |
| Gold     | 16 | 2 | 1 | 2 | 0 | 0 | 21 | **NOT MET** — docs-examples + docs-use-cases FAIL (regressed), exception-translations PARTIAL; all user-deferred 2026-08-28 (BL-039/BL-040) |
| Platinum | 3 |  0 | 0 | 0 | 0 | 0 |  3 | MET — strict typing: 0 `type: ignore`, mypy strict clean |

Beyond-scale dashboard:

| Dimension | Pass | Fail | Partial | N/A | Stale | Unverified | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| A — Credentials & secrets | 3 | 0 | 0 | 0 | 0 | 0 | 3 |
| B — Resource lifecycle | 3 | 0 | 1 | 0 | 0 | 0 | 4 |
| C — Resilience | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| D — Logging hygiene | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| E — HA ADR compliance | 8 | 0 | 0 | 14 | 0 | 0 | 22 |
| F — HACS submission | 4 | 0 | 0 | 1 | 0 | 0 | 5 |
| G — Automated checks | 4 | 0 | 0 | 1 | 0 | 0 | 5 |
| H — LOCKED-file boundary | 2 | 0 | 0 | 0 | 0 | 0 | 2 |

---

## Bronze Rules

Rule URL pattern: `https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/<slug>`

| Rule | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| action-setup | N/A | No service actions. `grep async_register custom_components/` → only static-path registration; integration exposes entities only (7 platforms). | 2026-08-28 | — |
| appropriate-polling | PASS | `iot_class: local_push` (manifest). Coordinator 30 s poll is a connection health-check only; events arrive via `VipEventListener` on CTPP. | 2026-08-28 | — |
| brands | PASS | Core 2026.3 allows custom integrations to ship brand images locally. `custom_components/comelit_man/brand/icon.png` present; HACS brands validation passes in CI. Optional polish: add `logo.png` + `dark_`/`@2x` variants. Was accepted-FAIL 2026-05-06→2026-08-28. | 2026-08-28 | BL-014 closed |
| common-modules | PASS | `entity.py` `ComelitEntity(CoordinatorEntity)` with `_attr_has_entity_name = True` (entity.py:15) + `device_info`; used by all 7 platform files (binary_sensor, button, camera, event, image, lock, sensor). | 2026-08-28 | — |
| config-flow | PASS | `manifest.json` `"config_flow": true`; `ComelitLocalConfigFlow.async_step_user`; options flow `ComelitLocalOptionsFlow(OptionsFlowWithReload)` (config_flow.py:321); strings + en.json mirror verified identical. | 2026-08-28 | — |
| config-flow-test-coverage | PASS | config_flow.py at **100%** coverage (restored commit 0c7a854 after provisioning branch gap; verified in Sweep 6 full run). | 2026-08-28 | — |
| dependency-transparency | PASS | `"requirements": ["aiohttp>=3.9,<4", "av>=12.0.0"]`. `av` upper bound deliberately removed (c621108) to allow HA's bundled av≥16. | 2026-08-28 | — |
| docs-actions | N/A | No service actions exist (cross-link to `action-setup`). | 2026-08-28 | — |
| docs-high-level-description | PASS | `README.md:1-13` product overview + feature bullets (incl. two-way audio, passive inbound video, locks). | 2026-08-28 | — |
| docs-installation-instructions | PASS | `README.md:35-47` Installation (HACS + manual), `README.md:15-19` Requirements, `README.md:48+` Configuration. | 2026-08-28 | — |
| docs-removal-instructions | PASS | `README.md:81` "Removing the integration" section. | 2026-08-28 | — |
| entity-event-setup | PASS | Push callbacks registered in `async_added_to_hass` via `async_on_remove`/explicit removal across event.py, camera.py, binary_sensor.py, image.py, sensor.py; verified by 100%-coverage lifecycle tests. | 2026-08-28 | — |
| entity-unique-id | PASS | `_attr_unique_id` on every entity: button ×4, camera ×2, event ×2 (incl. per-caller `_doorbell_{caller}`), binary_sensor ×2, sensor ×2, lock ×1, image ×1 (grep-verified counts). | 2026-08-28 | — |
| has-entity-name | PASS | `_attr_has_entity_name = True` once in `entity.py:15`, inherited by all entities. | 2026-08-28 | — |
| runtime-data | PASS | `entry.runtime_data = coordinator` (`__init__.py`); `ComelitLocalConfigEntry` type alias; coordinator narrows `config_entry: ComelitLocalConfigEntry` (strict-typing commit 3203686). | 2026-08-28 | — |
| test-before-configure | PASS | `config_flow.py` connects + authenticates (or auto-extracts token / provisions dedicated user) before `async_create_entry`; failures map to `invalid_auth`/`cannot_connect`. | 2026-08-28 | — |
| test-before-setup | PASS | `__init__.py:113-119` wraps `coordinator.async_setup()` → `ConfigEntryAuthFailed` / `ConfigEntryNotReady`. | 2026-08-28 | — |
| unique-config-entry | PASS | `async_set_unique_id(host)` + `_abort_if_unique_id_configured()`; DHCP path uses MAC. | 2026-08-28 | — |

## Silver Rules

| Rule | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| action-exceptions | N/A | No service actions (cross-link to bronze:action-setup). | 2026-08-28 | — |
| config-entry-unloading | PASS | `async_unload_entry` → `async_unload_platforms` + `coordinator.async_shutdown()` (cancels keepalive, video session, VIP listener, RTSP server, client). Options changes reload via `OptionsFlowWithReload`. | 2026-08-28 | — |
| docs-configuration-parameters | PASS | `README.md:48-71` Configuration + "Notification settings"; strings.json options section. | 2026-08-28 | — |
| docs-installation-parameters | PASS | Setup fields (host/port/token/password) documented in README Configuration; per-field labels in strings.json. | 2026-08-28 | — |
| entity-unavailable | PASS | All entities inherit `CoordinatorEntity` availability via `ComelitEntity`. Deliberate exception: `ComelitConnectivitySensor.available` returns `True` always (binary_sensor.py:61-63) — reporting disconnection is its purpose; correct per rule intent. | 2026-08-28 | — |
| integration-owner | PASS | `manifest.json` `"codeowners": ["@mnestrud"]`. | 2026-08-28 | — |
| log-when-unavailable | PASS | `_connection_lost` edge-detection in coordinator — disconnect warns once, reconnect informs once. Covered by coordinator tests (100%). | 2026-08-28 | — |
| parallel-updates | PASS | `PARALLEL_UPDATES = 0` in all 7 platform files (grep-verified: binary_sensor, button, camera, event, image, lock, sensor). | 2026-08-28 | — |
| reauthentication-flow | PASS | `async_step_reauth` + `async_step_reauth_confirm`; validates against device then `async_update_reload_and_abort`. Config-flow coverage 100%. | 2026-08-28 | — |
| test-coverage | PASS | **100%** — 4014/4014 statements, all 32 source files at 100%, 1064 passed / 9 skipped (Sweep 6 run 2026-08-28; commits 021e2fd + e55c7d0). CI gate still `--cov-fail-under=83` — raise to 95+ when convenient (BL-041, hygiene). | 2026-08-28 | BL-041 (optional) |

## Gold Rules

| Rule | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| devices | PASS | All entities provide `DeviceInfo` via `ComelitEntity.device_info`; additional cameras use `via_device` (2027.8 deprecation deferred, see header). | 2026-08-28 | — |
| diagnostics | PASS | `diagnostics.py` — config redacted via `async_redact_data`, topology, connection + video state. 100% covered. | 2026-08-28 | — |
| discovery | PASS | DHCP discovery: `"dhcp": [{"hostname": "icona*"}, {"hostname": "*comelit*"}]`; `async_step_dhcp` sets MAC unique_id (canonical `DhcpServiceInfo` import, dep-sweep 2026-08-28). | 2026-08-28 | — |
| discovery-update-info | PASS | DHCP re-discovery updates host via `_abort_if_unique_id_configured(updates={CONF_HOST: host})`; MAC unique_id stable across IP changes. | 2026-08-28 | — |
| docs-data-update | PASS | `README.md:218` "Data update mechanism" — VIP push, keepalive probe, health poll, disconnect callback. | 2026-08-28 | — |
| docs-examples | **FAIL** | **Regressed.** The automation YAML examples the 2026-05-06 row cited (README.md:91-150) were lost in the 1.x README rewrites — that range is now the Entities table; only prose mentions of automations remain (README.md:10,100,155). **User deferred restoring examples 2026-08-28.** | 2026-08-28 | BL-039 (deferred) |
| docs-known-limitations | PASS | `README.md:73` "Known limitations". | 2026-08-28 | — |
| docs-supported-devices | PASS | `README.md:21` "Supported devices" — 6701W confirmed table + ICONA fingerprint. | 2026-08-28 | — |
| docs-supported-functions | PASS | `README.md:91` Entities table (all 7 platforms incl. locks, sensors, ringing/connectivity) + `README.md:117` Lovelace cards. | 2026-08-28 | — |
| docs-troubleshooting | PASS | `README.md:250` Troubleshooting — debug logging + common problems. | 2026-08-28 | — |
| docs-use-cases | **FAIL** | **Regressed** with docs-examples (same lost section). `README.md:153-…` "Doorbell Notifications" describes the ring flow in prose but has no worked use-case examples. **User deferred 2026-08-28.** | 2026-08-28 | BL-039 (deferred) |
| dynamic-devices | N/A | Fixed physical topology; UCFG fetched at setup + reconnect. | 2026-08-28 | — |
| entity-category | PASS | DIAGNOSTIC on video start/stop buttons (button.py:100,129) and connectivity sensor (binary_sensor.py:48). Primary entities uncategorized (correct). | 2026-08-28 | — |
| entity-device-class | PASS | DOORBELL on both event entities (event.py:63,96), CONNECTIVITY + SOUND binary sensors (binary_sensor.py:47,70), TIMESTAMP last-ring sensor (sensor.py:51). Ring-count uses `TOTAL_INCREASING` state class (no applicable device class). | 2026-08-28 | — |
| entity-disabled-by-default | PASS | `entity_registry_enabled_default = False` on video start/stop buttons; primary entities enabled. | 2026-08-28 | — |
| entity-translations | PASS | `_attr_translation_key` on every entity class in all 7 platforms (grep-verified); strings.json `entity` section covers button ×4 / camera / image / event / binary_sensor ×2 / lock / sensor ×2; en.json byte-identical mirror. | 2026-08-28 | — |
| exception-translations | **PARTIAL** | strings.json `exceptions` section exists (door_open_failed, video_call_failed, video_rtpc_not_received) and door.py:63 + video_call.py raise sites use them. **Gaps:** `auth.py:34` `AuthenticationError(f-string)`, `door.py:101` `DoorOpenError(f-string)`, `__init__.py:115-119` ConfigEntryAuthFailed/NotReady f-strings, `coordinator.py:928,930` — none carry translation keys. **User deferred 2026-08-28.** | 2026-08-28 | BL-040 (deferred) |
| icon-translations | PASS | `icons.json` covers button (door/video_start/video_stop), camera, event. New 1.3+ entities rely on device-class default icons (connectivity, sound, timestamp, doorbell, lock) — no `_attr_icon` hardcoding anywhere (grep-verified). | 2026-08-28 | — |
| reconfiguration-flow | PASS | `async_step_reconfigure` validates then `async_update_reload_and_abort`. Config-flow coverage 100%. | 2026-08-28 | — |
| repair-issues | PASS | `repairs.py` `ConfirmRepairFlow` for `auth_failed`; issue raised on auth failure, cleared on reconnect. | 2026-08-28 | — |
| stale-devices | N/A | Fixed topology (cross-link to `dynamic-devices`). | 2026-08-28 | — |

## Platinum Rules

| Rule | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| async-dependency | PASS | `aiohttp` async-native; `av` (PyAV) offloaded via `run_in_executor` in rtp_receiver.py; all internal I/O async. | 2026-08-28 | — |
| inject-websession | PASS | `token.py` uses `async_get_clientsession(hass)`; coordinator's go2rtc registration calls also use `async_get_clientsession(self.hass)` (strict-typing/coverage pass, 2026-08-28). No standalone `ClientSession` anywhere. | 2026-08-28 | — |
| strict-typing | PASS | `[tool.mypy] strict = true`; `py.typed` present; CI mypy job (mypy 2.1.0 with aiohttp/av/pytest-homeassistant stubs installed). **0 `type: ignore` comments** (12 eliminated in commit 3203686, incl. `config_entry: ComelitLocalConfigEntry` narrowing on the coordinator). | 2026-08-28 | — |

---

## Beyond-Scale Audit

Same row shape as the tier tables. Run in Sweeps 4a–4d. LOCKED-file findings (`door.py`, `video_call.py`) are tagged `Locked: YES` and `REQUIRES OWNER APPROVAL` — never auto-fixed.

### A — Credentials & secrets (Sweep 4a)

| Check | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| Token storage location (config entry data vs. options vs. plaintext) | PASS | Stored in `ConfigEntry.data[CONF_TOKEN]` (`config_flow.py:95`) — HA's standard config registry, encrypted at rest. Read at `__init__.py:99`. Held in `coordinator.py:56` as instance variable for runtime use. No file writes, no plaintext persistence. | 2026-05-06 | — |
| `grep -ni "token\|password\|cookie"` across logging paths shows no secret leakage | PASS | (a) Auth token: only `token.py:135` logs it, masked to first/last 4 chars (`%s...%s` with `token[:4]`/`token[-4:]`) — flagged with `# nosemgrep`. (b) UDPM session token at `video_call.py:295,492-493`: ephemeral 16-bit stream identifier, not a secret. (c) FCM `DEVICE_TOKEN = "comelit-local-ha-integration"` at `push.py:17`: hardcoded constant we mint, not a secret. (d) `config_flow.py:69` `_LOGGER.exception("Token extraction failed: %s", err)` — `err` from `extract_token` only contains `TokenExtractionError` messages (HTTP status, file size); no token contents — verified by reading `token.py:51,54,71,80,86,101,138,144,146`. | 2026-05-06 | — |
| Auth error paths (UAUT failures) do not echo token in exception or log | PASS | `auth.py:30-33` builds error from `response.get("response-code")` + `response.get("response-string")` only — never echoes `token`. Caller `coordinator.py:142,216` propagates the `AuthenticationError` unchanged; `__init__.py:104-107` re-raises as `ConfigEntryAuthFailed(f"Authentication failed for Comelit device: {err}")`, again not including the token. | 2026-05-06 | — |

### B — Resource lifecycle (Sweep 4a)

| Check | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| Every `asyncio.create_task` has a matching cancel on unload | PARTIAL | **Tracked tasks (cancel-on-stop verified):** `client.py:88` receive task → `task.cancel()` in `disconnect()`; coordinator keepalive → `_cancel_keepalive`; VIP listen loop → `self._task.cancel()`; RTSP server loops → `task.cancel()`; RTP receiver tasks → `task.cancel()`; `video_call.py` session tasks (LOCKED) tracked in instance vars. **Supervised fire-and-forget (BL-032 applied 2026-05-20 in Bundle A+B):** `button.py:81` (10s video-stop delay), `coordinator.py:448` (auto-restart on CALL_END), `coordinator.py:600` (reconnect refresh) all converted to `config_entry.async_create_background_task` — HA-supervised, named tasks; will be cancelled on entry unload. **One remaining untracked:** `video_call.py:521` bare `asyncio.create_task` (LOCKED — not modified). Low risk: `_run_answer_sequence` swallows exceptions and the task is short-lived. | 2026-05-21 | BL-032 (partial — LOCKED remainder) |
| RTSP server stopped on `async_unload_entry` | PASS | Chain: `__init__.py:139` → `coordinator.async_shutdown()` (`coordinator.py:242`) → `coordinator.py:250-253` calls `self._rtsp_server.stop()` and clears the reference. `rtsp_server.py:216-223` `stop()` cancels tasks and closes server socket. | 2026-05-06 | — |
| All UDP/TCP sockets closed on unload (RTP receiver, ICONA client) | PASS | TCP: `client.py:91-106` `disconnect()` cancels receive task and calls `self._writer.close()` + `await self._writer.wait_closed()`. UDP (RTP receiver): `rtp_receiver.py:589-602` `stop()` cancels keepalive + decode tasks and closes the `DatagramTransport`. Both invoked from `coordinator.async_shutdown()` via `async_stop_video()` → session.stop() (LOCKED) and `client.disconnect()` (`coordinator.py:256`). | 2026-05-06 | — |
| `async_remove_entry` defined and clears persisted state if any | PASS | `__init__.py:143` `async_remove_entry` defined (BL-002 applied 2026-05-20 in Bundle A+B). Device-side push-channel unregistration is not possible (no protocol support), but the hook logs the removal and satisfies HA's lifecycle expectations. The push registration lapses naturally once keepalive probes stop. | 2026-05-21 | BL-002 Done |

### C — Resilience (Sweep 4a)

| Check | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| Reconnect/backoff after the device's wifi-sleep disconnect | PASS | Two-layer detection: (1) Receive-loop 120s timeout in `client.py` → calls disconnect callback → `coordinator.py:573-583` `_on_client_disconnect` schedules immediate refresh. (2) `coordinator.py:585-596` `_async_update_data` runs every 30 s, checks `self._client.connected`, calls `_reconnect()` when False. Backoff inherited from HA's `DataUpdateCoordinator` framework (sufficient for this use case — no need for explicit exponential backoff on a local-network device). | 2026-05-06 | — |
| Keepalive timer reset behavior on reconnect | PASS | `coordinator.py:461-464` `_start_keepalive` cancels any previous task before creating a new one. Called at setup (`coordinator.py:168`) and after every successful reconnect (`coordinator.py:239`). The 90-second keepalive (`coordinator.py:472-503`) sends `push-info` to keep the device's TCP idle-timer reset. | 2026-05-06 | — |
| VIP listener auto-restarts on TCP drop | PASS | `coordinator.py:200-203` stops old VIP in `_reconnect`. `coordinator.py:228-237` starts new VIP after reconnect (when notifications enabled). Additional restart point: `coordinator.py:505-525` `_ensure_vip_listener` is called from `async_stop_video` (line 565) so VIP picks up the CTPP slot after a video session ends. Init timestamp preserved across restart via `self._ctpp_init_ts` (line 75) so the device's CTPP counter stays consistent. | 2026-05-06 | — |
| RTSP server idle behavior — no leak between calls, gating works | PASS | RTSP server is a singleton started once at setup (`coordinator.py:171-175`) and only stopped at shutdown (`coordinator.py:250-253`). Per-session gating: `mark_ready()` set when video starts (`coordinator.py:425-426`), `mark_not_ready()` + `disconnect_clients()` on stop (`coordinator.py:558-560`) and reconnect (`coordinator.py:196-198`). `stream_source()` waits up to 5 s on `_video_ready_event` (`camera.py:140-161`). RTCP Sender Reports every 5 s (CLAUDE.md video section). | 2026-05-06 | — |

### D — Logging hygiene (Sweep 4a)

| Check | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| All `_LOGGER.info` sites inventoried with keep/downgrade decision | PASS | **BL-007 applied 2026-05-21.** Final disposition: (a) **Setup/lifecycle (once-per-session) — keep info:** `__init__.py:86`, `coordinator.py` setup/reconnect landmarks, `vip_listener.py`, `rtsp_server.py`, `auth.py`, `push.py`, `config_reader.py`. (b) **User-action logs — keep info:** `button.py` door/video open/close feedback, `door.py:57` (LOCKED), `event.py:69`. (c) **First-of-kind per-session diagnostics — keep info:** `rtp_receiver.py` transport detection, `video_call.py:825` audio-start (LOCKED), `client.py`. (d) **Reconnect transitions — edge-detected (BL-022 done), keep info:** `coordinator.py` disconnect/reconnect warnings/info. (e) **Downgraded to debug (BL-007):** `coordinator.py` "CALL_END received" (device-driven ~30s renewal, not user action) and "VIP event listener restarted" (fires after every video stop, potentially noisy). (f) **LOCKED — read-only:** `video_call.py`, `door.py`, `vip_listener.py`, `rtsp_server.py` one-shot landmarks — all appropriate. | 2026-05-21 | BL-007 Done |
| No PII or token at any log level (cross-link to A) | PASS | Cross-references Dimension A. Apt-address strings (e.g. `SB000006`) are logged in `event.py:69` and `vip_listener.py:408` — these are building/door identifiers, not user PII. Host IP is logged at info on connect/reconnect — operational state. No user names, no GPS, no MAC addresses persisted in logs at info level. | 2026-05-06 | — |

### E — HA ADR compliance (Sweep 4b)

ADR index pulled from `https://github.com/home-assistant/architecture/tree/master/adr`. URL pattern for any specific ADR: `https://github.com/home-assistant/architecture/blob/master/adr/<filename>`.

| ADR | Title | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|---|
| 0001 | Record Architecture Decisions | N/A | Process for HA core itself; not applicable to custom integrations. | 2026-05-06 | — |
| 0002 | Minimum Supported Python Version | N/A | Superseded by ADR-0020. | 2026-05-06 | — |
| 0003 | Monitor Condition and Data Selectors | N/A | Integration registers no triggers/conditions; only entity-platform schemas. `config_flow.py:25-34` uses `voluptuous` types directly which HA renders with default selectors. | 2026-05-06 | — |
| 0004 | Webscraping | N/A | No external webscraping. `token.py` does HTTP login to the **local LAN device** to extract a backup tarball — local-device interaction, not third-party scraping. | 2026-05-06 | — |
| 0005 | Code Formatting | PASS | `validate.yml:24-33` runs `ruff check custom_components/` on every push and PR. | 2026-05-06 | — |
| 0006 | Docker Images | N/A | HA core distribution decision; custom integrations are not affected. | 2026-05-06 | — |
| 0007 | Integration Config YAML Structure | N/A | Integration is config-flow only; no YAML schema. | 2026-05-06 | — |
| 0008 | Code Owners | PASS | `manifest.json:4` `"codeowners": ["@mnestrud"]`. | 2026-05-06 | — |
| 0009 | Translations 2.0 | PASS | All translation gaps closed (BL-025/026/027 applied 2026-05-20): entity-name translations via `_attr_translation_key` on all entities; `icons.json` for entity icons; `strings.json` `"exceptions"` section for error messages. `strings.json` + `translations/en.json` in sync. | 2026-05-21 | BL-025/026/027 Done |
| 0010 | Integration Configuration | PASS | `manifest.json:5` `"config_flow": true`. Sole configuration mechanism is the UI flow at `config_flow.py:37`; no YAML configuration exists. | 2026-05-06 | — |
| 0011 | Discovery Requires Unique ID | PASS | DHCP discovery flow at `config_flow.py:115` calls `await self.async_set_unique_id(mac)` — unique ID is the device MAC address (stable across IP changes, per ADR-0011 requirement). BL-030 applied 2026-05-20. | 2026-05-21 | BL-030 Done |
| 0012 | Define Supported Installation Methods | N/A | Core distribution decision. | 2026-05-06 | — |
| 0013 | Home Assistant Container | N/A | Core distribution decision. | 2026-05-06 | — |
| 0014 | Home Assistant Supervised | N/A | Core distribution decision. | 2026-05-06 | — |
| 0015 | Home Assistant OS | N/A | Core distribution decision. | 2026-05-06 | — |
| 0016 | Home Assistant Core | N/A | Core distribution decision. | 2026-05-06 | — |
| 0017 | Hardware Screening OS | N/A | Core hardware decision. | 2026-05-06 | — |
| 0018 | Supported Databases | N/A | Core database decision; integration uses no recorder-direct or DB code. | 2026-05-06 | — |
| 0019 | GPIO | N/A | Integration does not use GPIO. | 2026-05-06 | — |
| 0020 | Minimum Supported Python Version | PASS | `manifest.json` declares `"homeassistant": "2026.1.0"` (BL-033 applied 2026-05-20 in Phase 1); CI matrix trimmed to `["3.13"]` only — aligns with HA 2026.1's Python 3.13 requirement. | 2026-05-21 | BL-033 Done |
| 0021 | YAML Integration Configuration Deprecation Policy | N/A | Integration is config-flow only; no YAML schema to deprecate. | 2026-05-06 | — |
| 0022 | Integration Quality Scale | PASS | This audit document IS the response to ADR-0022. The integration follows the quality-scale framework even though it does not yet meet any tier formally. CLAUDE.md declares the tier (Bronze, initial) and references this audit file. | 2026-05-06 | — |

### F — HACS submission compliance (Sweep 4c)

| Check | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| `hacs.json` present and valid | PASS | `hacs.json` exists at repo root: `{"name": "Comelit Man", "render_readme": true}`. Minimal but valid for a single-integration custom repo. The hacs/action job in `validate.yml:8-15` runs on every push and would flag schema errors. | 2026-05-06 | — |
| Repo topics include the HACS-required topics | PASS | Topics added 2026-05-20 (BL-035): `home-assistant`, `homeassistant`, `hacs`, `integration`, `comelit`, `doorbell`, `local-control`, `intercom`, `6701w` — verified via `gh repo view`. | 2026-05-21 | BL-035 Done |
| GitHub releases used (semver, not zip uploads) | PASS | `v1.0.1` release created 2026-05-27 (Latest); `v1.0.0` retained as prior stable tag. Both created via `gh release create --target main`. `manifest.json` version matches latest release tag. | 2026-05-27 | BL-034 Done |
| Brand registration in `home-assistant/brands` | FAIL — accepted | Verified absent (HTTP 404 on `home-assistant/brands/master/custom_integrations/comelit_man/icon.png`). **User decision 2026-05-06 (Sweep 1):** upstream PR is out of scope; local `brand/icon.png` accepted. Cross-link to `bronze:brands` row. | 2026-05-06 | (won't fix — see Sweep 1 amendment) |
| No bundled zip in repo root | PASS | Glob `*.zip` against repo root returned no matches. | 2026-05-06 | — |

### G — Automated checks coverage (Sweep 4c)

| Check | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| hassfest job present in CI | PASS | `validate.yml:17-22` `hassfest` job runs `home-assistant/actions/hassfest@master` on every push and PR. | 2026-05-06 | — |
| `hacs/action` validation job present in CI | PASS | `validate.yml:8-15` `HACS Validation` job runs `hacs/action@main` with `category: integration`. | 2026-05-06 | — |
| ruff job present in CI | PASS | `validate.yml:24-33` `Ruff` job runs `ruff check custom_components/`. (Cross-link to ADR-0005.) | 2026-05-06 | — |
| pytest matrix covers supported Python versions | PASS | CI matrix trimmed to `["3.13"]` (BL-033 applied 2026-05-20 in Phase 1). `manifest.json` declares `"homeassistant": "2026.1.0"` which mandates Python 3.13. All test files added to CI test list (BL-018 applied 2026-05-20 in Phase 1). | 2026-05-21 | BL-033 Done, BL-018 Done |
| Brands lint job (or manual check documented) | N/A | Brand registration is accepted-FAIL (out of scope per user decision 2026-05-06); a brands-lint CI step would only enforce the upstream PR which won't be filed. | 2026-05-06 | — |

### H — LOCKED-file boundary (Sweep 4d, read-only)

**Audit policy applied:** Both files were read end-to-end. Findings recorded as observations with `Locked: YES, REQUIRES OWNER APPROVAL`. **No source code modified.** The integration's most-protected protocol logic lives here; behaviour is "stable, verified" per CLAUDE.md after extensive PCAP-driven debugging.

| Check | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| `door.py` audited read-only — findings filed as `Locked: YES` | PASS | **BL-036 applied 2026-05-20 (user approved).** `opened_channel = False` initialized before `try:` at `door.py:46` — NameError guard in place. All other findings resolved: BL-037 (CLAUDE.md drift) Done; BL-038 (auth-error reauth) Done. No outstanding LOCKED-file findings remaining. | 2026-05-20 | — |
| `video_call.py` audited read-only — findings filed as `Locked: YES` | PASS — with notes | **859 lines.** Reflects a mature, protocol-faithful implementation: 11-step `start()`, separate `_ctpp_monitor_loop` with `0x1840`/`0x1860`/`0x1800` state machine, 9-step `_inline_reestablish` for CALL_END recovery without TCP reconnect, audio answer sequence, three independent counters (init_ts, call_ts, call_counter) with PCAP-verified increments (`_CTR_INCR_BYTE4`/`BYTE5`/`BOTH`). **Strengths:** every magic number has a `# PCAP-verified:` justification comment; `_ctpp_lock` correctly serialises counter mutation between CTPP monitor / door-during-video / answer sequence; `_cleanup` (line 517) cancels all tracked tasks with a 2 s timeout each (avoids the 30-40 s freeze on dead TCP observed on 3.14/aarch64); `VIDEO_CHANNEL_NAMES` enumeration prevents leaking channel registrations on cleanup. **Findings:** (1) **One untracked fire-and-forget task** — `video_call.py:521` `asyncio.create_task(self._run_answer_sequence(...))` is not assigned to any instance attribute, so `_cleanup()` cannot cancel it. The wrapping `_run_answer_sequence` already swallows exceptions, so failure mode is silent rather than crashing. Cross-link to BL-032 (filed in Sweep 4a). (2) **`_LOGGER.debug` UDPM token** at line 295 — ephemeral 16-bit stream identifier, not a secret (re-confirmed from Sweep 4a). (3) **Info-level logs** (lines 473, 500, 514, 825, 845, 854) all fire once per session in normal flow — appropriate level. (4) **Type hints** complete throughout; `"Channel"` forward-refs used at lines 124, 172, 583, 678. (5) **Tests** in CI: `tests/test_video_call.py` and `tests/test_video_signaling.py` per `validate.yml:55-57`. The 9-step `_inline_reestablish` path is the highest-risk untested branch — out-of-scope for this read-only sweep, but flagged for BL-023 test-coverage planning. | 2026-05-06 | BL-032 (already filed — covers video_call.py:521); audit observation only — no LOCKED edits proposed |

---

## Recommended Fix Sequence (Sweep 5 output)

Ordering optimised for (a) tier-by-tier achievement, (b) shared-PR efficiency, (c) external-latency parallelism (BL-034/BL-035 first because GitHub state is independent of code work).

### Phase 1 — Bronze MET + quick hygiene wins (~1 day)

| # | ID | What | Why first | Effort |
|---|---|---|---|---|
| 1 | BL-035 | `gh repo edit --add-topic ...` | External, ~2 min | S |
| 2 | BL-034 | `gh release create v0.1.4.3` from CHANGELOG.md | External, ~10 min | S |
| 3 | BL-001 | Add `integration_type: "device"` to manifest | Hassfest will start to enforce | S |
| 4 | BL-033 | Add `"homeassistant": "2026.1.0"` to manifest; trim CI matrix to 3.13 | Aligns version claims with hassfest | S |
| 5 | BL-037 | Update CLAUDE.md "Door Control" function names | 5-min docs fix | S |
| 6 | BL-017 | Add "Removing the integration" section to README | Bronze blocker | S |
| 7 | BL-018 | Fix `test_ha_component.py` imports + add to CI test list | Bronze blocker | S |
| 8 | **BL-020 + BL-021 (one PR)** | Extract `entity.py` base inheriting `CoordinatorEntity` | Bundle: BL-021 falls out of BL-020 if base inherits CoordinatorEntity. Bronze PARTIAL → PASS, Silver PARTIAL → PASS in one move. | M |

**End state:** Bronze MET (effective; `brands` accepted-FAIL); Silver `entity-unavailable` cleared.

### Phase 2 — Silver MET (~2-3 days)

| # | ID | What | Notes | Effort |
|---|---|---|---|---|
| 9 | BL-005 | `PARALLEL_UPDATES = 0` per platform | One-line per file | S |
| 10 | BL-022 | Edge-detect connection state → one-shot warn/info | After BL-007 review confirms which sites collapse | S |
| 11 | BL-004 | `async_step_reauth` in config flow | High-sev Silver blocker | M |
| 12 | BL-013 | Migrate to `pytest-homeassistant-custom-component` | **Prereq for BL-023.** Don't chase 95 % coverage on hand-rolled mocks. | L |
| 13 | BL-011 | Add `tests/test_camera_utils.py` | Folds into Phase 12; do during BL-013 retest | S |
| 14 | BL-023 | `pytest-cov` + `.coveragerc` + threshold gate; close coverage gaps | Largest Silver work item | L |

**End state:** Silver MET.

### Phase 3 — Gold + Platinum MET (~1 week)

| # | ID | What | Notes | Effort |
|---|---|---|---|---|
| 15 | BL-031 | `EntityCategory.DIAGNOSTIC` + `enabled_by_default=False` on Start/Stop Video buttons | Trivial | S |
| 16 | BL-028 | `EventDeviceClass.DOORBELL` on doorbell event | One line | S |
| 17 | BL-027 | Move `mdi:*` icons to `icons.json` | Pure refactor | S |
| 18 | BL-009 | `async_step_reconfigure` | Stacks on BL-004 | M |
| 19 | BL-029 | README expansion: 4 Gold doc rules in one PR | Single PR | M |
| 20 | BL-025 | Entity-name translations | | M |
| 21 | BL-006 | `pyproject.toml` + ruff config + dep upper bounds | **Prereq for BL-010** | S |
| 22 | BL-024 | Replace standalone `aiohttp.ClientSession` in `token.py` with `async_get_clientsession(hass)` | Plumbing only | S |
| 23 | BL-010 | `py.typed` + mypy strict + CI mypy step | Largest Platinum work item | M |
| 24 | BL-026 | Translatable exceptions ⚠ **REQUIRES OWNER APPROVAL** for any LOCKED-file edit | Try coordinator-only first; touch LOCKED files only with explicit approval | M |
| 25 | BL-008 | `repairs.py` for known recoverable failure modes | | M |
| 26 | BL-003 | `diagnostics.py` (redact token) | | M |
| 27 | BL-030 | UDP discovery (port 24199) — set `unique_id` from device MAC per ADR-0011 | Unblocks `gold:discovery-update-info` | M |

**End state:** Gold + Platinum MET (except `bronze:brands` accepted-FAIL forever).

### Phase 4 — final hygiene / optional

| ID | What | Sev |
|---|---|---|
| BL-002 | `async_remove_entry` (FCM unregister) | Medium |
| BL-038 | Door auth-error → reauth mapping (after BL-004) | Low |
| BL-007 | Info-log review (most sites already correct) | Low |
| BL-032 | Track fire-and-forget tasks on entry unload | Low |
| BL-036 | door.py NameError defensive fix — **LOCKED, REQUIRES OWNER APPROVAL** | Low |
| BL-016 | Recover audio-protocol findings doc (or remove CLAUDE.md reference) | Low |
| BL-012 | Coordinator split — DEFERRED, no quality-scale gate | Low |

### Closed during the audit

| ID | Closure reason |
|---|---|
| BL-014 | Decomposed in Sweep 4c — covered by BL-034 + BL-035 + accepted-FAIL brands |
| BL-015 | Decomposed in Sweep 5 — `hacs/action` already in CI; mypy in BL-010; brands lint N/A |
| BL-019 | Merged into BL-014 in Sweep 1 |

---

## Backlog Snapshot

Live source: `memory/comelit_man_backlog.md`. This snapshot is rebuilt at the end of each sweep.

**As of 2026-05-06 (Sweep 5):** 38 items total. 31 Confirmed (active work); 4 Closed (BL-014, BL-015, BL-019 decomposed/merged; BL-012 Deferred); BL-026 + BL-036 are LOCKED-touching items requiring owner approval. See "Recommended Fix Sequence" above for ordering.

**As of 2026-08-28 (Sweep 6):** all Sweep-5 active items closed by the 1.x fix bundles. New items: **BL-039** restore automation examples/use-cases to README (Gold docs-examples + docs-use-cases FAIL — user deferred); **BL-040** add translation keys to remaining raise sites `auth.py:34`, `door.py:101`, `__init__.py:115-119`, `coordinator.py:928,930` (Gold exception-translations PARTIAL — user deferred); **BL-041** raise CI `--cov-fail-under` from 83 to 95+ (hygiene, optional); **BL-042** `via_device` → `via_device_id` migration in camera.py before HA 2027.8 (deferred, needs device id at construction). BL-014 (brands) closed — local brand images now pass.

---

## BL-023 Step 2 — rtsp_server.py Coverage Checklist

**Goal:** raise total coverage from 89% (335 missed) to ≥95% (≤152 missed).  
**Baseline:** `rtsp_server.py` 44% (280 missed); `video_call.py` 83% (55 missed, LOCKED).  
**Coverage math:** Track A alone → ~235 missed (~92%). Track A + Track B → ~50–100 missed (~96–98%).  
Covering all 280 RTSP statements leaves only 55 missed (98%) — well clear of the Silver target.

### Track A — Direct unit tests (no TCP client required)

| # | Task | Lines | Status |
|---|---|---|---|
| A-1 | `mark_ready`, `mark_not_ready`, `disconnect_clients`, `reset` rtp_queue drain | 244, 248, 260-266, 287-289 | ☐ |
| A-2 | `_send()`, `_wait_for_teardown()`, UDP path in `_broadcast_rtp()` | 526, 557-563, 676-680 | ☐ |
| A-3 | `_prime_client_with_parameter_sets()`, `_send_initial_sr_to_client()` | 588-604, 618-640 | ☐ |
| A-4 | `_translate_video_ts()` — first call, normal advance, backward jump | 753-780 | ☐ |
| A-5 | `_drain_nal_queue_fallback()`, `_broadcast_rtcp()`, `_build_rtcp_sr()`, `_ntp_now()` | 784-803, 1025-1057, 1080-1103 | ☐ |

### Track B — Async loop and TCP protocol tests

| # | Task | Lines | Status |
|---|---|---|---|
| B-1 | `_handle_client()` via real TCP: OPTIONS→DESCRIBE→SETUP→PLAY→TEARDOWN + 405 + 503 + disconnect error paths | 347-487 | ☐ |
| B-2 | `_video_rtp_passthrough_loop()`: happy path, SPS/PPS cache, timeout→fallback, short/empty packet, CancelledError, exception | 697-749 | ☐ |
| B-3 | `_video_feed_loop()` + `_audio_feed_loop()`: happy path, start-code strip, timeout, CancelledError, exception | 816-891, 940-963 | ☐ |
| B-4 | `_rtcp_sr_loop()`: pre-loop wait, active client SR broadcast, no-client branch, CancelledError, exception | 983, 986-1014 | ☐ |

### Completion gate

| Step | Check | Status |
|---|---|---|
| Done | Run full suite, confirm ≥95% total coverage; update Silver `test-coverage` row to PASS; update BL-023 in backlog to DONE | ☐ |

---

## Audit Change Log

| Date | Sweep | Change |
|---|---|---|
| 2026-08-28 | 6 | **Full re-sweep at v1.5.0.** All 52 tier rules re-verified; every Verified date refreshed to 2026-08-28; freshness baseline moved to 1.5.x. Coverage restored to 100% (4014/4014 stmts, 1064 tests; commits 021e2fd, e55c7d0) after the 1.3–1.5 cherry-pick code opened gaps; strict typing hardened to 0 `type: ignore` (3203686). Bronze `brands` FAIL→PASS (Core 2026.3 local brand images) — **Bronze now cleanly MET**, BL-014 closed. Gold regressions found: `docs-examples` + `docs-use-cases` PASS→FAIL (automation YAML lost in README rewrites; BL-039), `exception-translations` PASS→PARTIAL (untranslated raise sites in auth.py/door.py/__init__.py/coordinator.py; BL-040) — all three **user-deferred 2026-08-28**; **Gold NOT MET** at 1.5.0. New BL-041 (optional): raise CI cov gate from 83 to 95+. Beyond-scale spot-checked only: B bare-task line moved 483→521 (`# noqa: RUF006`). Evidence anchors now prefer function names over line numbers where practical. Also 2026-08-28: MCP mechanism removed from the project (user decision) — all live-HA interaction via REST API with `~/.ha-token`. |
| 2026-05-06 | 0 | Skeleton created. Rule list pulled from HA quality-scale checklist; all rows `UNVERIFIED`; dashboard zeros; backlog seeded with BL-001..BL-016 in `Triage pending`. No source files modified. |
| 2026-05-06 | 1 | Bronze tier audited end-to-end. 12 PASS / 3 FAIL / 1 PARTIAL / 2 N/A → tier `NOT YET`. New backlog items BL-017 (README removal section), BL-018 (test_ha_component.py broken imports + missing CI inclusion), BL-019 *merged into BL-014* (brands registration is part of HACS hygiene), BL-020 (entity.py shared base). BL-002 re-scoped — Bronze docs-removal split out as BL-017; BL-002 stays as `beyond:B` lifecycle. BL-014 promoted from `gate:none` to `gate:bronze` because brand registration is required by Bronze. No source files modified. |
| 2026-05-06 | 1 (amended) | User decision: upstream brands PR is out of scope. `bronze:brands` row marked **FAIL — accepted**, BL-014 brands portion re-classified Won't fix; BL-014 demoted back to `gate:none` (HACS hygiene only). Bronze effective blockers: 2 FAIL + 1 PARTIAL (BL-017, BL-018, BL-020). |
| 2026-05-06 | 2 | Silver tier audited end-to-end. 4 PASS / 3 FAIL / 2 PARTIAL / 1 N/A → tier `NOT YET`. New backlog items BL-021 (entity-unavailable for camera/event), BL-022 (log-when-unavailable edge-detect once-only), BL-023 (test-coverage infrastructure + raise to 95%). BL-004 + BL-005 confirmed (existed in skeleton). No source files modified. |
| 2026-05-06 | 3 | Gold + Platinum tiers audited end-to-end. Gold: 4 PASS / 10 FAIL / 4 PARTIAL / 3 N/A → `NOT YET`. Platinum: 1 PASS / 2 FAIL → `NOT YET`. New backlog items BL-024 (inject-websession), BL-025 (entity-translations), BL-026 (exception-translations), BL-027 (icon-translations), BL-028 (entity-device-class), BL-029 (Gold docs expansion — limitations / troubleshooting / data-update / supported-devices), BL-030 (UDP discovery), BL-031 (entity-category + disabled-by-default for video buttons). Existing BL-003 / BL-006 / BL-008 / BL-009 / BL-010 confirmed. No source files modified. |
| 2026-05-06 | 4a | Beyond-Scale dimensions A–D audited end-to-end. A (Credentials): 3/3 PASS. B (Lifecycle): 2 PASS / 1 FAIL / 1 PARTIAL. C (Resilience): 4/4 PASS. D (Logging hygiene): 1 PASS / 1 PARTIAL. New backlog item BL-032 (track and cancel HA-supervised fire-and-forget tasks on entry unload). Existing BL-002 (lifecycle), BL-007 (info-level review), BL-022 (edge-detect reconnect logs) confirmed. **Notable:** the integration shows good defensive hygiene — token masking with nosemgrep tags, RTSP gating, keepalive cancel-on-restart, VIP listener auto-restart on reconnect. The main lifecycle gap is the absent `async_remove_entry` (BL-002), already known. No source files modified. |
| 2026-05-06 | 4b | Beyond-Scale dimension E (ADR walk) audited end-to-end. 22 ADRs reviewed. 4 PASS / 0 FAIL / 2 PARTIAL / 16 N/A. **PASS:** 0005 (code formatting), 0008 (code owners), 0010 (config-flow only), 0022 (quality scale). **PARTIAL:** 0009 (translation gaps already tracked in BL-025/026/027), 0020 (Python version mismatch — README says HA 2026.1+ but manifest has no min HA version, CI matrix tests 3.11/3.12 unnecessarily). 16 ADRs are core-distribution / GPIO / YAML — N/A for a config-flow custom integration. New backlog item BL-033. No source files modified. |
| 2026-05-06 | 4c | Beyond-Scale F (HACS submission) + G (Automated checks) audited. F: 2 PASS / 2 eff. FAIL / 1 accepted-FAIL. G: 3 PASS / 1 PARTIAL / 1 N/A. **F findings:** `hacs.json` valid (PASS), repo topics absent (FAIL → BL-035), no GitHub releases (FAIL → BL-034 — `gh api releases` returned `[]` despite manifest.json at 0.1.4.3 + CHANGELOG.md present), brands accepted-FAIL, no bundled zip (PASS). **G findings:** all required CI jobs (hassfest, hacs/action, ruff) present and run on every push/PR; pytest matrix is too wide (BL-033 already filed); brands lint N/A given the won't-fix decision. **Cosmetic note:** GitHub labels the LICENSE as "Other" despite README saying Apache 2.0 and the file content matching — likely missing the canonical first-line marker. Not filed as a backlog item but noted here. ADR-0011 note added to BL-030 body. New backlog items BL-034, BL-035. BL-014 *decomposed* — its remaining scope is fully covered by BL-034/BL-035 plus the accepted-FAIL brand portion. No source files modified. |
| 2026-05-06 | 4d | Beyond-Scale H (LOCKED-file read-only audit) audited end-to-end. **`door.py` PARTIAL** — 5 findings: latent NameError in finally (BL-036, Locked), parameter shadowing (style), auth-error reauth mapping (BL-038), CLAUDE.md drift on function names (BL-037, not Locked), logging clean. **`video_call.py` PASS with notes** — 859 lines, mature PCAP-faithful implementation: per-magic-number `# PCAP-verified:` comments, `_ctpp_lock` serializes counter mutation, `_cleanup` cancels tracked tasks with 2 s timeout, channel enumeration prevents leaks. One untracked fire-and-forget at `video_call.py:521` already covered by BL-032. **No LOCKED files modified.** New backlog items BL-036 (Locked), BL-037, BL-038. |
| 2026-05-27 | — | BL-023 Step 2 plan recorded. Step 1 complete: 89% total (335 missed), 639 tests. rtsp_server.py dominates remaining gap (280 missed, 44%). Step 2 Track A: 5 unit-test groups (~100 stmts, no TCP). Track B: 4 async/TCP groups (~130-150 stmts). Checklist added to this file. Target: ≥95% (≤152 missed total). |
| 2026-05-27 | — | Header sync for v1.0.1: version bumped 1.0.0→1.0.1; tier claim updated to match current CLAUDE.md; freshness rule minor version corrected (0.1.x→1.0.x); Beyond-F releases row updated to reference v1.0.1 as Latest. No rule statuses changed. |
| 2026-05-21 | 7 | Gold row sweep. Verified all 14 stale Gold rows against code. All Gold FAILs/PARTIALs resolved: `diagnostics` FAIL→PASS (diagnostics.py exists); `discovery`/`discovery-update-info` FAIL/N/A→PASS (DHCP in manifest + async_step_dhcp with MAC unique_id + IP-update abort); all 4 docs FAIL/PARTIAL→PASS (README data-update/known-limitations/supported-devices/troubleshooting sections present); `entity-category`/`entity-device-class`/`entity-disabled-by-default` PARTIAL/FAIL→PASS (EntityCategory.DIAGNOSTIC, EventDeviceClass.DOORBELL, enabled_default=False on video buttons); `entity-translations`/`exception-translations`/`icon-translations` FAIL→PASS (all _attr_translation_key set, exceptions.py inherits HomeAssistantError, icons.json present); `repair-issues` FAIL→PASS (repairs.py exists). Gold tier summary: 19P/0F/0P/2NA → **GOLD MET**. Beyond-scale updates: B `async_remove_entry` FAIL→PASS; F topics+releases FAIL→PASS; G pytest matrix PARTIAL→PASS; E ADR-0009/0011/0020 PARTIAL/N/A→PASS. BL-007 implemented: `coordinator.py` "CALL_END received" and "VIP event listener restarted" downgraded from info to debug (device-driven events, not user actions). D logging hygiene PARTIAL→PASS. |
| 2026-05-20 | 6 | Silver row sweep at v1.0.0. Updated 5 stale Silver rows to match code state: `entity-unavailable` PARTIAL→PASS (BL-021: ComelitEntity base class applied), `log-when-unavailable` PARTIAL→PASS (BL-022: `_connection_lost` edge-detection applied), `parallel-updates` FAIL→PASS (BL-005: PARALLEL_UPDATES=0 in all platforms), `reauthentication-flow` FAIL→PASS (BL-004: async_step_reauth added), `test-coverage` evidence updated to reflect 85%/570-test baseline. Updated Gold `reconfiguration-flow` FAIL→PASS (BL-009: async_step_reconfigure added). Silver tier summary: 4→8 PASS, 3→0 FAIL, 2→0 PARTIAL (test-coverage is sole remaining Silver FAIL). Gold tier summary: 4→5 PASS, 10→9 FAIL. manifest.json bumped to 1.0.0. |
| 2026-05-06 | 5 | Final triage. Five items moved out of `Triage pending`: BL-001 (Low/none — hygiene), BL-011 (Low/silver — folds into BL-023), BL-013 (Medium/none — prereq for BL-023), BL-016 (re-tagged developer hygiene). BL-012 delinked from `bronze:common-modules` and stays Deferred. **BL-015 decomposed** — work fully covered by BL-010 (mypy job) + already-in-CI hacs/action + accepted-FAIL brands. Added "Recommended Fix Sequence" with 4 phases mapping every Confirmed item to a tier checkpoint. Surfaced **Stale rows** total in the audit summary block (0 today) so CLAUDE.md startup banner can read it. CLAUDE.md startup checklist + banner format updated to include `STALE: <count>` (plan deliverable 3). Deliverables 1-3 from the plan are now complete. |
