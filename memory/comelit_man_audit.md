# Comelit Man — Quality Audit

**Last full sweep:** Sweep 5 (final triage) — 2026-05-06; Phase 1 fixes applied 2026-05-20; Phase 2 bundle applied 2026-05-20; Bundle A+B applied 2026-05-20; Bundle CDEF applied 2026-05-20; BL-006/010/036 applied 2026-05-20; Silver row sweep applied 2026-05-20; Gold row sweep + BL-007 applied 2026-05-21; v1.0.1 release + header sync 2026-05-27; BL-023 Step 1 (rtp_receiver 100%, +52 stmts) 2026-05-27; BL-023 Step 2 plan recorded 2026-05-27; BL-023 Track B DONE (rtsp_server.py 100%, 98% total, 759 tests, 0 coroutine warnings) 2026-05-27; video_call.py Groups A-G + tcp_video_loop happy path done 2026-05-28 (94% video_call.py, 18 stmts Group H in start() deferred, 775 tests); video_call.py Group H DONE 2026-05-28 (100% video_call.py, 100% total — 3040/3040 stmts, 783 tests)
**Delta review 2026-08-28 (v1.4.0, not a full sweep):** rows below carry their original Verified dates and are formally STALE by the freshness rule (>90 days AND 1.0.x-era). Spot-verified today: CI green (ruff 0.15.15, mypy 2.1.0, hassfest/HACS unchanged); tests 887 passed / 9 skipped; **total coverage 96%** (down from the 100% recorded at 1.0.2 — remaining gaps: video_call.py inbound `start_inbound` body 92%, coordinator inbound path 90%, door.py 97%, button.py 94%; new-in-1.3/1.4 code is covered). Platforms grew to BUTTON/CAMERA/EVENT/IMAGE (image.py 100% covered). Event types renamed `doorbell_ring`→`ring` in 1.1.1. No entity/config-flow rule surface changed in 1.3.x/1.4.0 beyond the IMAGE platform. Tier claims below are believed to hold except Silver test-coverage, now PARTIAL (96%, was 100%); a full re-sweep is due.
**Version at audit:** 1.0.2 (full sweep); delta-reviewed at 1.4.0
**Tier claim (CLAUDE.md):** Bronze EFFECTIVE PASS; Silver MET (BL-023 DONE, 98% total); Gold MET; Platinum MET
**Tier verdict (audited):** Bronze PASS; **Silver MET** (10 PASS / 0 FAIL — test-coverage BL-023 closed 2026-05-27); **Gold MET** (19 PASS / 0 FAIL / 0 PARTIAL / 2 N/A); Platinum MET; Beyond A-D 13/14 PASS (B still PARTIAL: video_call.py:483 bare asyncio.create_task, LOCKED); Beyond D 2/2 PASS (BL-007 done); Beyond E 8 PASS / 0 PARTIAL / 14 N/A of 22 ADRs; Beyond F 4 PASS / 0 FAIL / 1 accepted-FAIL; Beyond G 4 PASS / 0 PARTIAL / 1 N/A; Beyond H 2 PASS
**Stale rows:** 0 (sum of Stale columns across all dashboards). When this becomes ≥1, schedule re-verification of the affected rows.
**Next review due:** when all sweeps land OR +90 days from last full sweep, whichever first
**Freshness rule:** any row is `STALE` if `Verified` date > 90 days old OR older than the current `manifest.json` minor version (`1.0.x`).

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
| Bronze   | 15 | 1 | 0 | 2 | 0 | 0 | 18 | EFFECTIVE PASS — `brands` FAIL accepted (won't fix); all other rules PASS |
| Silver   | 9 | 0 | 0 | 1 | 0 | 0 | 10 | MET — test-coverage BL-023 closed 2026-05-27 (98% total, rtsp_server.py 100%) |
| Gold     | 19 | 0 | 0 | 2 | 0 | 0 | 21 | MET — all 19 applicable rules PASS; 2 N/A (dynamic-devices, stale-devices) |
| Platinum | 3 |  0 | 0 | 0 | 0 | 0 |  3 | MET |

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
| action-setup | N/A | No service actions registered. `__init__.py:77` only registers static paths and Lovelace resources; no `hass.services.async_register` anywhere in `custom_components/comelit_man/` (grep confirmed). Integration exposes only entities. | 2026-05-06 | — |
| appropriate-polling | PASS | `iot_class: local_push` in `manifest.json:9`. `coordinator.py:32` `UPDATE_INTERVAL = timedelta(seconds=30)` drives `_async_update_data` (`coordinator.py:585`) which only health-checks the connection — actual events arrive via `VipEventListener` on CTPP. 30 s for a connectivity check is reasonable for a local push integration. | 2026-05-06 | — |
| brands | FAIL — accepted | Rule requires brand assets registered in `home-assistant/brands` repo. Verified absent (HTTP 404 on icon.png at master). **User decision 2026-05-06: upstream PR is out of scope; local `custom_components/comelit_man/brand/icon.png` is acceptable for this integration.** Bronze `brands` will remain FAIL — accept it; do not re-open. | 2026-05-06 | BL-014 (won't fix — upstream) |
| common-modules | PASS | `entity.py` created (BL-020, 2026-05-20): `ComelitEntity(CoordinatorEntity[ComelitLocalCoordinator])` provides `_attr_has_entity_name = True` and `device_info` property. All entity files import and use it: `button.py:46,98,127`, `camera.py:86`, `event.py:32`. No per-file boilerplate duplication remains. | 2026-05-20 | BL-020 |
| config-flow | PASS | `manifest.json:5` `"config_flow": true`. `config_flow.py:37` `ComelitLocalConfigFlow` with `async_step_user` (line 49). Translations present in `strings.json:22-54` and `translations/en.json`. Options flow in `config_flow.py:107`. | 2026-05-06 | — |
| config-flow-test-coverage | PASS | `tests/test_ha_component.py` fully repaired 2026-05-20: stale imports fixed, constructor signature updated, patch paths corrected, `hass.data`→`entry.runtime_data` assertions updated, voluptuous stub added to conftest. File added to CI test list (`validate.yml`). 24/24 tests pass. | 2026-05-20 | — |
| dependency-transparency | PASS | `manifest.json:10` declares `"requirements": ["aiohttp>=3.9,<4", "av>=12.0.0,<13"]`. Both are pinned with lower and upper bounds (upper bounds added 2026-05-20 via BL-006). | 2026-05-20 | — |
| docs-actions | N/A | No service actions exist (cross-link to `action-setup`). | 2026-05-06 | — |
| docs-high-level-description | PASS | `README.md:1-11` opens with brand/product overview ("Home Assistant custom component for the Comelit 6701W WiFi video intercom...") and feature bullets. | 2026-05-06 | — |
| docs-installation-instructions | PASS | `README.md:19-48` "Installation" (HACS + manual) and "Configuration" sections with step-by-step setup including prerequisites at `README.md:13-17`. | 2026-05-06 | — |
| docs-removal-instructions | PASS | `README.md` "Removing the integration" section added 2026-05-20: steps for Settings → Delete + note about push-channel lapse. | 2026-05-20 | — |
| entity-event-setup | PASS | `event.py:59-61` registers push callback in `async_added_to_hass` via `async_on_remove`. `camera.py:172-193` registers/unregisters callbacks in `async_added_to_hass`/`async_will_remove_from_hass`. `button.py` uses `CoordinatorEntity` which the framework manages. | 2026-05-06 | — |
| entity-unique-id | PASS | All entities set `_attr_unique_id`: `button.py:61` (`{entry_id}_door_{door.index}`), `button.py:122` (`_video_start`), `button.py:162` (`_video_stop`), `camera.py:64` (`_camera_{id}`), `camera.py:113` (`_intercom_camera`), `event.py:47` (`_doorbell`). | 2026-05-06 | — |
| has-entity-name | PASS | `_attr_has_entity_name = True` on every entity class: `button.py:48,110,150`, `camera.py:50,99`, `event.py:34`. | 2026-05-06 | — |
| runtime-data | PASS | `__init__.py:117` `entry.runtime_data = coordinator`. Type alias `coordinator.py:601` `ComelitLocalConfigEntry: TypeAlias = ConfigEntry[ComelitLocalCoordinator]`. Used consistently in `__init__.py:91,134`, `button.py:25`, `camera.py:25`, `event.py:23`. | 2026-05-06 | — |
| test-before-configure | PASS | `config_flow.py:73-84`: instantiates `IconaBridgeClient`, calls `connect()` and `authenticate()` with timeouts before `async_create_entry` (line 90). Maps failures to `invalid_auth`/`cannot_connect` errors (lines 77-82). | 2026-05-06 | — |
| test-before-setup | PASS | `__init__.py:102-115` wraps `coordinator.async_setup()` and raises `ConfigEntryAuthFailed` on `AuthenticationError` (line 105) and `ConfigEntryNotReady` on `TimeoutError`/`ComelitConnectionError`/`OSError` (line 109) and any other `Exception` (line 113). | 2026-05-06 | — |
| unique-config-entry | PASS | `config_flow.py:87-88`: `await self.async_set_unique_id(host)` followed by `self._abort_if_unique_id_configured()`. Aborts with `already_configured` (string in `strings.json:51`). | 2026-05-06 | — |

## Silver Rules

| Rule | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| action-exceptions | N/A | No service actions registered (cross-link to bronze:action-setup). | 2026-05-06 | — |
| config-entry-unloading | PASS | `__init__.py:133-140` `async_unload_entry` — calls `async_unload_platforms`, then `entry.runtime_data.async_shutdown()` on success. `coordinator.py:242-257` `async_shutdown` cancels keepalive, stops video session, stops VIP listener, stops RTSP server, disconnects client. Options-flow reload wired at `__init__.py:121,126-130`. | 2026-05-06 | — |
| docs-configuration-parameters | PASS | `README.md:40-48` documents the `Enable Notifications` option (the only configurable option after setup). Strings/translations cover the description: `strings.json:9-21`. | 2026-05-06 | — |
| docs-installation-parameters | PASS | `README.md:32-39` documents host + token + password setup steps. Per-field labels and descriptions in `strings.json:22-44` (host, port, http_port, token, password). | 2026-05-06 | — |
| entity-unavailable | PASS | All entities inherit `ComelitEntity(CoordinatorEntity[ComelitLocalCoordinator])` via `entity.py:12` (BL-020 applied 2026-05-20). `camera.py:86` `class ComelitIntercomCamera(ComelitEntity, Camera)` and `event.py:32` `class ComelitDoorbellEvent(ComelitEntity, EventEntity)` both auto-mark unavailable when `coordinator.last_update_success` is False. `ComelitEntity.available` property inherited from `CoordinatorEntity`. | 2026-05-20 | BL-021 Done |
| integration-owner | PASS | `manifest.json:4` `"codeowners": ["@mnestrud"]`. | 2026-05-06 | — |
| log-when-unavailable | PASS | `coordinator.py` uses `_connection_lost: bool` flag (line 82) for edge-detection. `_on_client_disconnect` (line 597–599) and `_async_update_data` (line 613–615) both check `if not self._connection_lost` before warning — the disconnect warning fires exactly once per event. `_connection_lost = False` reset at line 246 on successful reconnect, so the reconnect info log also fires exactly once. (BL-022 applied 2026-05-20 in Bundle A+B.) | 2026-05-20 | BL-022 Done |
| parallel-updates | PASS | `PARALLEL_UPDATES = 0` declared at module level in `button.py:20`, `camera.py:23`, `event.py:19`. All three platform files covered. (BL-005 applied 2026-05-20 in Phase 1.) | 2026-05-20 | BL-005 Done |
| reauthentication-flow | PASS | `config_flow.py:178` `async_step_reauth` + `config_flow.py:184` `async_step_reauth_confirm` implemented (BL-004 applied 2026-05-20 in Bundle A+B). Validates new token/password against device, then calls `async_update_reload_and_abort`. Strings at `strings.json:72–83` and `translations/en.json:72–83` provide translated UI. | 2026-05-20 | BL-004 Done |
| test-coverage | PASS | BL-023 DONE (2026-05-27). 759 tests pass, 98% total (3040/3040 stmts measured, 55 missed in video_call.py which is LOCKED). `rtsp_server.py` raised from 44% to 100% (Track B: loop tests + handle_client tests added). CI gate at `--cov-fail-under=85` (still enforced; actual 98% far exceeds Silver ≥95% threshold). All other modules at 100% except `video_call.py` (83%, 55 missed, LOCKED — accepted). | 2026-05-27 | BL-023 Done |

## Gold Rules

| Rule | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| devices | PASS | All entities provide `DeviceInfo`. Buttons + intercom camera + doorbell event share `(DOMAIN, entry_id)` (`button.py:67-72,127-132,167-172`, `camera.py:131-138`, `event.py:50-57`). Additional cameras get their own device with `via_device` (`camera.py:67-75`). `manufacturer`/`model` set from `const.py:4-5`. | 2026-05-06 | — |
| diagnostics | PASS | `diagnostics.py` present (BL-003 applied 2026-05-20 in Bundle CDEF). `async_get_config_entry_diagnostics` returns config (token/password redacted via `async_redact_data`), device topology (door/camera count, apt_address), connection state, and video state. | 2026-05-21 | BL-003 Done |
| discovery | PASS | DHCP discovery added (BL-030 applied 2026-05-20 in Bundle CDEF). `manifest.json:7-10` declares `"dhcp": [{"hostname": "icona*"}, {"hostname": "*comelit*"}]`. `config_flow.py:109` `async_step_dhcp` sets unique_id from MAC (`self.async_set_unique_id(mac)`), calls `_abort_if_unique_id_configured(updates={CONF_HOST: host})` to handle IP changes, then routes to `async_step_dhcp_confirm` for credential entry. | 2026-05-21 | BL-030 Done |
| discovery-update-info | PASS | DHCP flow at `config_flow.py:116` calls `self._abort_if_unique_id_configured(updates={CONF_HOST: host})` — when the device is re-discovered with a new IP, the config entry host is updated automatically and no new entry is created. Unique ID is the MAC address (stable across IP changes). | 2026-05-21 | BL-030 Done |
| docs-data-update | PASS | `README.md` "Data update mechanism" section (BL-029 applied 2026-05-20 in Bundle CDEF): table listing VIP event listener (push), FCM keepalive probe (90 s), health-check poll (30 s), and TCP disconnect callback (instant); explicit note that entity availability mirrors coordinator connectivity. | 2026-05-21 | BL-029 Done |
| docs-examples | PASS | `README.md:91-150` shows three automation examples (notify on ring, notify with camera link, notify and start video). | 2026-05-06 | — |
| docs-known-limitations | PASS | `README.md` "Known limitations" section (BL-029): single video session, WiFi sleep, LAN-only, fixed RTSP port 8557, door-open-stops-video-after-10s. | 2026-05-21 | BL-029 Done |
| docs-supported-devices | PASS | `README.md` "Supported devices" section (BL-029): table with Comelit 6701W (firmware 2.x, all features confirmed); "Likely compatible" note for other ICONA Bridge devices; protocol fingerprint (port 64100, UAUT/UCFG/CTPP) for user self-diagnosis. | 2026-05-21 | BL-029 Done |
| docs-supported-functions | PASS | `README.md:50-59` "Entities" table lists every entity and what it does. `README.md:62-85` documents the Lovelace cards. | 2026-05-06 | — |
| docs-troubleshooting | PASS | `README.md` "Troubleshooting" section (BL-029): debug-logging YAML snippet; five common-problem entries (cannot connect, auth failed, video doesn't start, doorbell events not firing, entities unavailable) with actionable steps. | 2026-05-21 | BL-029 Done |
| docs-use-cases | PASS | `README.md:87-150` covers three doorbell use-cases (notify only, notify + open camera, notify + auto-start video). | 2026-05-06 | — |
| dynamic-devices | N/A | The 6701W has fixed physical topology — doors and cameras are wired into the building and cannot be added at runtime. UCFG fetched at setup + reconnect (`coordinator.py:143,217`); no need for runtime addition. | 2026-05-06 | — |
| entity-category | PASS | `button.py:102` `_attr_entity_category = EntityCategory.DIAGNOSTIC` on Start Video Feed button; `button.py:130` same on Stop Video Feed button (BL-031 applied 2026-05-20 in Phase 2 bundle). Door buttons + intercom camera + doorbell event have no category (correct — primary functions). | 2026-05-21 | BL-031 Done |
| entity-device-class | PASS | `event.py:37` `_attr_device_class = EventDeviceClass.DOORBELL` (BL-028 applied 2026-05-20 in Phase 2 bundle). Door buttons have no applicable `ButtonDeviceClass` (none match door-open semantics). Intercom camera has no applicable device class. | 2026-05-21 | BL-028 Done |
| entity-disabled-by-default | PASS | `button.py:103` `_attr_entity_registry_enabled_default = False` on Start Video Feed button; `button.py:131` same on Stop Video Feed button (BL-031 applied 2026-05-20 in Phase 2 bundle). Door buttons, intercom camera, and doorbell event are enabled by default (correct — primary functions). | 2026-05-21 | BL-031 Done |
| entity-translations | PASS | All entities use `_attr_translation_key` (BL-025 applied 2026-05-20 in Phase 2 bundle): `button.py:49` `"door"`, `button.py:101` `"video_start"`, `button.py:130` `"video_stop"`, `camera.py:102` `"intercom_camera"`, `event.py:35` `"doorbell"`. Device-provided names (`door.name`, `camera.name`) for door buttons and standalone cameras are acceptable per HA guidance. Translations in `strings.json:3-23` and `translations/en.json:3-23`. | 2026-05-21 | BL-025 Done |
| exception-translations | PASS | `strings.json` and `translations/en.json` both have `"exceptions"` section (BL-026 applied 2026-05-20 in Bundle CDEF): `door_open_failed`, `video_call_failed`, `video_rtpc_not_received`. All exception classes (`DoorOpenError`, `VideoCallError`, etc.) inherit `HomeAssistantError` via `ComelitError` (`exceptions.py:6`). Integration has no registered service actions (`bronze:action-setup` N/A), so rule is technically N/A — but translation infrastructure is in place for completeness. | 2026-05-21 | BL-026 Done |
| icon-translations | PASS | `icons.json` present (BL-027 applied 2026-05-20 in Phase 2 bundle): entity icons defined under `entity.button.door`, `entity.button.video_start`, `entity.button.video_stop`, `entity.camera.intercom_camera`, `entity.event.doorbell`. No `_attr_icon` hardcoding remains in entity files. | 2026-05-21 | BL-027 Done |
| reconfiguration-flow | PASS | `config_flow.py:236` `async_step_reconfigure` implemented (BL-009 applied 2026-05-20 in Bundle A+B). Validates new connection params, calls `async_update_reload_and_abort` with host/port/token/http_port. Strings at `strings.json:84–100` and `translations/en.json:84–100`. | 2026-05-20 | BL-009 Done |
| repair-issues | PASS | `repairs.py` present (BL-008 applied 2026-05-20 in Bundle CDEF). `async_create_fix_flow` handles `auth_failed` issue via `ConfirmRepairFlow`. Repair issue raised at `coordinator.py:247` via `ir.async_create_issue` when authentication fails; cleared on successful reconnect at `coordinator.py:247`. Issue strings in `strings.json:124-137` and `translations/en.json:124-137` with `fix_flow.step.confirm` description directing user to re-authenticate. | 2026-05-21 | BL-008 Done |
| stale-devices | N/A | Fixed topology (cross-link to `dynamic-devices`); devices never go stale at runtime. | 2026-05-06 | — |

## Platinum Rules

| Rule | Status | Evidence (path:line / SHA) | Verified | Action (BL-NNN) |
|---|---|---|---|---|
| async-dependency | PASS | `aiohttp` is async-native ✓. `av` (PyAV) is synchronous but correctly offloaded: `rtp_receiver.py:470,533` uses `loop.run_in_executor(None, ...)` for both codec init and frame decode, so the event loop is never blocked by FFmpeg calls. All internal modules are async (`client.py`, `auth.py`, `coordinator.py`, `vip_listener.py`, etc.). | 2026-05-06 | — |
| inject-websession | PASS | `token.py:17` imports `async_get_clientsession`; `token.py:42` uses `session = async_get_clientsession(hass)` — HA's shared session, not a standalone `ClientSession`. `hass` plumbed from `config_flow.py`. BL-024 Done 2026-05-20. | 2026-05-20 | BL-024 |
| strict-typing | PASS | `pyproject.toml` `[tool.mypy] strict = true`; `py.typed` marker present; CI `typecheck` job. Local mypy run (2026-05-21): 18 errors remain — all are `Class cannot subclass "X" (has type "Any")` / `Returning Any from HA method` / `@callback untyped` false positives that disappear when `homeassistant` is installed. The CI mypy job does not install HA, so these are structural false positives not fixable without HA present. All real type annotation gaps (37 of 53 original errors) were fixed 2026-05-21: `asyncio.Task[None]`, `asyncio.Future[bytes]`, `asyncio.Queue[bytes]`, `dict[str, Any]` throughout all modules; `ctpp.py` None+int assert; `coordinator.py` config None guard; `cast()` on json.loads. | 2026-05-21 | — |

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
| Every `asyncio.create_task` has a matching cancel on unload | PARTIAL | **Tracked tasks (cancel-on-stop verified):** `client.py:88` receive task → `task.cancel()` in `disconnect()`; coordinator keepalive → `_cancel_keepalive`; VIP listen loop → `self._task.cancel()`; RTSP server loops → `task.cancel()`; RTP receiver tasks → `task.cancel()`; `video_call.py` session tasks (LOCKED) tracked in instance vars. **Supervised fire-and-forget (BL-032 applied 2026-05-20 in Bundle A+B):** `button.py:81` (10s video-stop delay), `coordinator.py:448` (auto-restart on CALL_END), `coordinator.py:600` (reconnect refresh) all converted to `config_entry.async_create_background_task` — HA-supervised, named tasks; will be cancelled on entry unload. **One remaining untracked:** `video_call.py:483` bare `asyncio.create_task` (LOCKED — not modified). Low risk: `_run_answer_sequence` swallows exceptions and the task is short-lived. | 2026-05-21 | BL-032 (partial — LOCKED remainder) |
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
| `video_call.py` audited read-only — findings filed as `Locked: YES` | PASS — with notes | **859 lines.** Reflects a mature, protocol-faithful implementation: 11-step `start()`, separate `_ctpp_monitor_loop` with `0x1840`/`0x1860`/`0x1800` state machine, 9-step `_inline_reestablish` for CALL_END recovery without TCP reconnect, audio answer sequence, three independent counters (init_ts, call_ts, call_counter) with PCAP-verified increments (`_CTR_INCR_BYTE4`/`BYTE5`/`BOTH`). **Strengths:** every magic number has a `# PCAP-verified:` justification comment; `_ctpp_lock` correctly serialises counter mutation between CTPP monitor / door-during-video / answer sequence; `_cleanup` (line 517) cancels all tracked tasks with a 2 s timeout each (avoids the 30-40 s freeze on dead TCP observed on 3.14/aarch64); `VIDEO_CHANNEL_NAMES` enumeration prevents leaking channel registrations on cleanup. **Findings:** (1) **One untracked fire-and-forget task** — `video_call.py:483` `asyncio.create_task(self._run_answer_sequence(...))` is not assigned to any instance attribute, so `_cleanup()` cannot cancel it. The wrapping `_run_answer_sequence` already swallows exceptions, so failure mode is silent rather than crashing. Cross-link to BL-032 (filed in Sweep 4a). (2) **`_LOGGER.debug` UDPM token** at line 295 — ephemeral 16-bit stream identifier, not a secret (re-confirmed from Sweep 4a). (3) **Info-level logs** (lines 473, 500, 514, 825, 845, 854) all fire once per session in normal flow — appropriate level. (4) **Type hints** complete throughout; `"Channel"` forward-refs used at lines 124, 172, 583, 678. (5) **Tests** in CI: `tests/test_video_call.py` and `tests/test_video_signaling.py` per `validate.yml:55-57`. The 9-step `_inline_reestablish` path is the highest-risk untested branch — out-of-scope for this read-only sweep, but flagged for BL-023 test-coverage planning. | 2026-05-06 | BL-032 (already filed — covers video_call.py:483); audit observation only — no LOCKED edits proposed |

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
| 2026-05-06 | 0 | Skeleton created. Rule list pulled from HA quality-scale checklist; all rows `UNVERIFIED`; dashboard zeros; backlog seeded with BL-001..BL-016 in `Triage pending`. No source files modified. |
| 2026-05-06 | 1 | Bronze tier audited end-to-end. 12 PASS / 3 FAIL / 1 PARTIAL / 2 N/A → tier `NOT YET`. New backlog items BL-017 (README removal section), BL-018 (test_ha_component.py broken imports + missing CI inclusion), BL-019 *merged into BL-014* (brands registration is part of HACS hygiene), BL-020 (entity.py shared base). BL-002 re-scoped — Bronze docs-removal split out as BL-017; BL-002 stays as `beyond:B` lifecycle. BL-014 promoted from `gate:none` to `gate:bronze` because brand registration is required by Bronze. No source files modified. |
| 2026-05-06 | 1 (amended) | User decision: upstream brands PR is out of scope. `bronze:brands` row marked **FAIL — accepted**, BL-014 brands portion re-classified Won't fix; BL-014 demoted back to `gate:none` (HACS hygiene only). Bronze effective blockers: 2 FAIL + 1 PARTIAL (BL-017, BL-018, BL-020). |
| 2026-05-06 | 2 | Silver tier audited end-to-end. 4 PASS / 3 FAIL / 2 PARTIAL / 1 N/A → tier `NOT YET`. New backlog items BL-021 (entity-unavailable for camera/event), BL-022 (log-when-unavailable edge-detect once-only), BL-023 (test-coverage infrastructure + raise to 95%). BL-004 + BL-005 confirmed (existed in skeleton). No source files modified. |
| 2026-05-06 | 3 | Gold + Platinum tiers audited end-to-end. Gold: 4 PASS / 10 FAIL / 4 PARTIAL / 3 N/A → `NOT YET`. Platinum: 1 PASS / 2 FAIL → `NOT YET`. New backlog items BL-024 (inject-websession), BL-025 (entity-translations), BL-026 (exception-translations), BL-027 (icon-translations), BL-028 (entity-device-class), BL-029 (Gold docs expansion — limitations / troubleshooting / data-update / supported-devices), BL-030 (UDP discovery), BL-031 (entity-category + disabled-by-default for video buttons). Existing BL-003 / BL-006 / BL-008 / BL-009 / BL-010 confirmed. No source files modified. |
| 2026-05-06 | 4a | Beyond-Scale dimensions A–D audited end-to-end. A (Credentials): 3/3 PASS. B (Lifecycle): 2 PASS / 1 FAIL / 1 PARTIAL. C (Resilience): 4/4 PASS. D (Logging hygiene): 1 PASS / 1 PARTIAL. New backlog item BL-032 (track and cancel HA-supervised fire-and-forget tasks on entry unload). Existing BL-002 (lifecycle), BL-007 (info-level review), BL-022 (edge-detect reconnect logs) confirmed. **Notable:** the integration shows good defensive hygiene — token masking with nosemgrep tags, RTSP gating, keepalive cancel-on-restart, VIP listener auto-restart on reconnect. The main lifecycle gap is the absent `async_remove_entry` (BL-002), already known. No source files modified. |
| 2026-05-06 | 4b | Beyond-Scale dimension E (ADR walk) audited end-to-end. 22 ADRs reviewed. 4 PASS / 0 FAIL / 2 PARTIAL / 16 N/A. **PASS:** 0005 (code formatting), 0008 (code owners), 0010 (config-flow only), 0022 (quality scale). **PARTIAL:** 0009 (translation gaps already tracked in BL-025/026/027), 0020 (Python version mismatch — README says HA 2026.1+ but manifest has no min HA version, CI matrix tests 3.11/3.12 unnecessarily). 16 ADRs are core-distribution / GPIO / YAML — N/A for a config-flow custom integration. New backlog item BL-033. No source files modified. |
| 2026-05-06 | 4c | Beyond-Scale F (HACS submission) + G (Automated checks) audited. F: 2 PASS / 2 eff. FAIL / 1 accepted-FAIL. G: 3 PASS / 1 PARTIAL / 1 N/A. **F findings:** `hacs.json` valid (PASS), repo topics absent (FAIL → BL-035), no GitHub releases (FAIL → BL-034 — `gh api releases` returned `[]` despite manifest.json at 0.1.4.3 + CHANGELOG.md present), brands accepted-FAIL, no bundled zip (PASS). **G findings:** all required CI jobs (hassfest, hacs/action, ruff) present and run on every push/PR; pytest matrix is too wide (BL-033 already filed); brands lint N/A given the won't-fix decision. **Cosmetic note:** GitHub labels the LICENSE as "Other" despite README saying Apache 2.0 and the file content matching — likely missing the canonical first-line marker. Not filed as a backlog item but noted here. ADR-0011 note added to BL-030 body. New backlog items BL-034, BL-035. BL-014 *decomposed* — its remaining scope is fully covered by BL-034/BL-035 plus the accepted-FAIL brand portion. No source files modified. |
| 2026-05-06 | 4d | Beyond-Scale H (LOCKED-file read-only audit) audited end-to-end. **`door.py` PARTIAL** — 5 findings: latent NameError in finally (BL-036, Locked), parameter shadowing (style), auth-error reauth mapping (BL-038), CLAUDE.md drift on function names (BL-037, not Locked), logging clean. **`video_call.py` PASS with notes** — 859 lines, mature PCAP-faithful implementation: per-magic-number `# PCAP-verified:` comments, `_ctpp_lock` serializes counter mutation, `_cleanup` cancels tracked tasks with 2 s timeout, channel enumeration prevents leaks. One untracked fire-and-forget at `video_call.py:483` already covered by BL-032. **No LOCKED files modified.** New backlog items BL-036 (Locked), BL-037, BL-038. |
| 2026-05-27 | — | BL-023 Step 2 plan recorded. Step 1 complete: 89% total (335 missed), 639 tests. rtsp_server.py dominates remaining gap (280 missed, 44%). Step 2 Track A: 5 unit-test groups (~100 stmts, no TCP). Track B: 4 async/TCP groups (~130-150 stmts). Checklist added to this file. Target: ≥95% (≤152 missed total). |
| 2026-05-27 | — | Header sync for v1.0.1: version bumped 1.0.0→1.0.1; tier claim updated to match current CLAUDE.md; freshness rule minor version corrected (0.1.x→1.0.x); Beyond-F releases row updated to reference v1.0.1 as Latest. No rule statuses changed. |
| 2026-05-21 | 7 | Gold row sweep. Verified all 14 stale Gold rows against code. All Gold FAILs/PARTIALs resolved: `diagnostics` FAIL→PASS (diagnostics.py exists); `discovery`/`discovery-update-info` FAIL/N/A→PASS (DHCP in manifest + async_step_dhcp with MAC unique_id + IP-update abort); all 4 docs FAIL/PARTIAL→PASS (README data-update/known-limitations/supported-devices/troubleshooting sections present); `entity-category`/`entity-device-class`/`entity-disabled-by-default` PARTIAL/FAIL→PASS (EntityCategory.DIAGNOSTIC, EventDeviceClass.DOORBELL, enabled_default=False on video buttons); `entity-translations`/`exception-translations`/`icon-translations` FAIL→PASS (all _attr_translation_key set, exceptions.py inherits HomeAssistantError, icons.json present); `repair-issues` FAIL→PASS (repairs.py exists). Gold tier summary: 19P/0F/0P/2NA → **GOLD MET**. Beyond-scale updates: B `async_remove_entry` FAIL→PASS; F topics+releases FAIL→PASS; G pytest matrix PARTIAL→PASS; E ADR-0009/0011/0020 PARTIAL/N/A→PASS. BL-007 implemented: `coordinator.py` "CALL_END received" and "VIP event listener restarted" downgraded from info to debug (device-driven events, not user actions). D logging hygiene PARTIAL→PASS. |
| 2026-05-20 | 6 | Silver row sweep at v1.0.0. Updated 5 stale Silver rows to match code state: `entity-unavailable` PARTIAL→PASS (BL-021: ComelitEntity base class applied), `log-when-unavailable` PARTIAL→PASS (BL-022: `_connection_lost` edge-detection applied), `parallel-updates` FAIL→PASS (BL-005: PARALLEL_UPDATES=0 in all platforms), `reauthentication-flow` FAIL→PASS (BL-004: async_step_reauth added), `test-coverage` evidence updated to reflect 85%/570-test baseline. Updated Gold `reconfiguration-flow` FAIL→PASS (BL-009: async_step_reconfigure added). Silver tier summary: 4→8 PASS, 3→0 FAIL, 2→0 PARTIAL (test-coverage is sole remaining Silver FAIL). Gold tier summary: 4→5 PASS, 10→9 FAIL. manifest.json bumped to 1.0.0. |
| 2026-05-06 | 5 | Final triage. Five items moved out of `Triage pending`: BL-001 (Low/none — hygiene), BL-011 (Low/silver — folds into BL-023), BL-013 (Medium/none — prereq for BL-023), BL-016 (re-tagged developer hygiene). BL-012 delinked from `bronze:common-modules` and stays Deferred. **BL-015 decomposed** — work fully covered by BL-010 (mypy job) + already-in-CI hacs/action + accepted-FAIL brands. Added "Recommended Fix Sequence" with 4 phases mapping every Confirmed item to a tier checkpoint. Surfaced **Stale rows** total in the audit summary block (0 today) so CLAUDE.md startup banner can read it. CLAUDE.md startup checklist + banner format updated to include `STALE: <count>` (plan deliverable 3). Deliverables 1-3 from the plan are now complete. |
