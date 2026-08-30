# Samsung IP Control for real power control

Research for [Issue #24](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/issues/24), captured 2026-08-30. This note is intentionally narrower than a feature design: it records the verified protocol boundary, model caveats, and the smallest safe implementation package for this integration.

## Conclusion

Samsung IP Control is a credible local path for explicit panel power on/off and reboot on recent Frame TVs. It is a separate HTTPS JSON-RPC service from the existing WebSocket channels. The first rollout is split into three ordered packages: an isolated client, an interactive per-TV pairing step, and explicit targeted `power_on`, `power_off`, and `reboot` actions. It should **not** yet replace `media_player.turn_on`/`turn_off`, poll power state, or move Art Mode away from the established WebSocket path.

That boundary keeps the current behavior unchanged for unpaired users and makes hardware validation possible before the new channel controls core entity semantics.

## Facts supported by primary sources

### What Samsung documents

Samsung's official 2020 IP command list includes Power Off, Power On, Reboot, and Frame-only Art Mode Control. It says IP commands require a secure token and notes that some installations need Wake-on-LAN/Wake-on-Wireless for power-on. It does **not** publish the consumer-TV JSON-RPC envelope or the token bootstrap method. The official list therefore confirms the capability family, not the complete wire protocol. [Samsung 2020 IP Command List (PDF)](https://image-us.samsung.com/SamsungUS/tv-ci-resources/2020-control-code/2020_IP_command_list.pdf)

### Transport and request shapes

The current `TheFab21/ha-samsungtv-smart` implementation and its empirical Frame protocol reference agree on this contract:

- HTTPS `POST` to `https://<tv>:1516/` on 2020-and-newer devices. Its implementation also probes 1515 for older devices.
- JSON-RPC 2.0 with a request id and method name.
- A self-signed TV certificate. The reference client disables hostname and chain verification.
- `createAccessToken` omits `params`; every later request places `AccessToken` inside `params`.
- A getter calls `powerControl` with only the token. A setter adds `power` with `powerOn`, `powerOff`, or `reboot`.

```json
{"jsonrpc":"2.0","id":1,"method":"createAccessToken"}
{"jsonrpc":"2.0","id":1,"method":"powerControl","params":{"AccessToken":"..."}}
{"jsonrpc":"2.0","id":1,"method":"powerControl","params":{"AccessToken":"...","power":"powerOff"}}
```

The decompiled 2025 Frame internals in the same upstream repository independently corroborate POST-only HTTP, the JSON-RPC version, `createAccessToken`, token-in-params authentication, and the 1515/1516 year split. [Protocol reference](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/IP_Control_Protocol_Reference.md), [decompiled protocol internals](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/notes/QN55LS03FAFXZA/RPC_INTERNALS.md#1516-json-rpc)

### Pairing and token persistence

`createAccessToken` waits for an on-screen approval and returns `result.AccessToken`. The upstream client uses a 30-second pairing timeout and 5-second normal-command timeout. Its reconfigure flow:

1. asks the user to enable **IP Remote** and put the TV in normal viewing rather than Art Mode;
2. tries 1516, then 1515 only after a transport failure;
3. stores the token and successful port per config entry;
4. optionally records model and firmware information.

The selected port must not advance after an application-level response because doing so can create a second pairing prompt. [IP Control client](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/api/ipcontrol.py#L218-L254), [pairing flow](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/config_flow.py#L869-L1023)

The token is a credential. Upstream stores it separately from the WebSocket token, per TV, and redacts it from diagnostics. This integration should follow the same rule: never log the token, expose it in state/action responses, or include it unredacted in future diagnostics. [Upstream constants](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/const.py#L188-L225), [upstream diagnostics redaction](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/diagnostics.py#L1-L43), [Home Assistant diagnostics guidance](https://developers.home-assistant.io/docs/core/integration/diagnostics/)

### Response and error handling

The upstream client accepts both standard nested errors (`{"error":{"code":...}}`) and a firmware-observed flat shape (`{"code":...,"message":...}`). It classifies:

| Condition | Interpretation for the first package |
|---|---|
| `-32010` | token rejected; re-pair required |
| `-32700` when a token was sent | firmware-observed stale/unrecognized token; re-pair required |
| `-32002` | command refused in current state; potentially transient |
| `-32601` | unavailable now **or** unsupported on this model; do not permanently cache unsupported |
| timeout, TLS, reset, unreachable host | transport failure, distinct from auth/protocol failure |
| invalid JSON or missing expected result field | protocol/response failure |

These meanings are defensive mappings of observed Samsung firmware, not a public consumer-TV specification. In particular, `-32601` has been observed both for absent methods and for methods unavailable in the current display state. [Upstream parsing and error mapping](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/api/ipcontrol.py#L681-L765)

### TLS, blocking I/O, and concurrency

The reference implementation builds an `SSLContext`, disables hostname/certificate verification for the TV's self-signed certificate, performs blocking `http.client.HTTPSConnection` work in Home Assistant's executor, always closes the connection, and retries a `dh key too small` failure with a lower OpenSSL security level for older TVs. It also serializes calls per host because observed TVs reset overlapping port-1516 connections. [Upstream transport](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/api/ipcontrol.py#L628-L817)

Home Assistant explicitly requires blocking network/file-backed SSL operations to stay off the event loop. [Home Assistant blocking-operation guidance](https://developers.home-assistant.io/docs/asyncio_blocking_operations/)

Because verification is disabled, traffic is encrypted but the endpoint identity is not authenticated. IP Control should therefore be documented for trusted local networks only. This is a security inference from `CERT_NONE`, not a Samsung guarantee.

### What TheFab21 currently implements

At commit `cb2f980420a1a002fb911de1d3fe492bbbdeee92`, TheFab21 has:

- a dedicated IP Control client with pair/read/off/on/reboot methods;
- pairing under **Reconfigure → IP Control** with per-entry token/port storage;
- separate auth, state/mode, unsupported, and transport exceptions;
- a reboot button which powers on first when necessary;
- per-host request serialization and token redaction.

One important discrepancy exists: current `media_player.async_turn_off` still prefers SmartThings and then WebSocket; it does not currently route turn-off through IP Control, despite broader release/reference prose. IP Control power-on is an optional fallback after WebSocket failure. We should copy the tested client boundary and pairing/error patterns, not assume all stated entity routing is present in upstream source. [Current media-player power code](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/media_player.py#L2912-L3050), [current reboot button](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/custom_components/samsungtv_smart/button.py#L106-L152)

## Hardware observations, not universal guarantees

- Issue #24's contributor measured a 2026 Frame Pro (`QE75LS03HWUXZU`, firmware family `26_RSM_FTV`): WebSocket power keys only toggle Art/TV or wake standby, while IP Control `powerOff` and `powerOn` produced real standby/on transitions. Port 1516 stayed reachable in standby. [Issue #24](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration/issues/24)
- TheFab21 reports successful pairing and power control on 2024/2025 Frames, pairing only from normal viewing, and tokens surviving power cycles. [Empirical protocol reference](https://github.com/TheFab21/ha-samsungtv-smart/blob/cb2f980420a1a002fb911de1d3fe492bbbdeee92/IP_Control_Protocol_Reference.md)
- Home Assistant Core issue #156635 independently reports that a 2024 Frame's normal power command leaves Art Mode for an input screen or enters Art Mode instead of fully powering down. [Home Assistant Core #156635](https://github.com/home-assistant/core/issues/156635)
- Power-on from standby is model, firmware, network, and energy-setting dependent. Some panels remain reachable; Samsung's official list allows for WoL/WoW, and older integration observations include TVs that disappear from the network. Do not promise IP `powerOn` on every Frame until tested.
- Method availability and error meanings differ by model and current mode. Art Mode control over IP Control has adverse-firmware reports in upstream. Issue #24 and this package should keep Art Mode on the existing WebSocket channel.

## Fit with this repository

The repository currently describes the TV boundary as exclusively WebSocket-based and owns one `SamsungFrameClient` per loaded config entry. Blocking Samsung calls are contained off-loop, target resolution is centralized, saved authentication failures already feed Home Assistant reauthentication, and the reconfigure flow currently handles host/name only. [Architecture](../../ARCHITECTURE.md), [`runtime.py`](../../custom_components/samsung_frame_art_director/runtime.py), [`targets.py`](../../custom_components/samsung_frame_art_director/targets.py), [`config_flow.py`](../../custom_components/samsung_frame_art_director/config_flow.py)

Adding IP Control therefore requires an explicit second TV boundary in the architecture, not hidden logic inside the Art WebSocket methods. The IP token must be named and persisted separately from the existing WebSocket token.

Home Assistant distinguishes user-initiated reconfiguration from automatic reauthentication after a credential failure. A rejected saved IP token should start a linked reauth/re-pair path rather than repeatedly prompt the TV or merely retry forever. [Home Assistant reconfigure guidance](https://developers.home-assistant.io/blog/2024/03/21/config-entry-reconfigure-step/), [authentication failure handling](https://developers.home-assistant.io/docs/integration_setup_failures/#handling-expired-credentials)

## Recommended smallest safe package for Issue #24

### In scope

1. Add an isolated `ip_control.py` client with a deliberately small public API: `async_pair`, `async_get_power_state`, `async_power_on`, `async_power_off`, and `async_reboot`.
2. Use short-lived HTTPS connections, explicit total/socket timeouts, per-host serialization, deterministic close, bounded response reads, strict JSON/result validation, and distinct auth/state/unsupported/transport exceptions.
3. Add a human-interactive **Reconfigure → IP Control** pairing step. Store `ip_control_token` and the selected port in `ConfigEntry.data`, separately per TV and separately from the WebSocket token. Try another known port only after a transport failure.
4. Add explicit targeted Home Assistant actions `power_on`, `power_off`, and `reboot`. Resolve targets through the existing `async_resolve_action_targets` path and return clear validation errors when a selected Frame is not paired.
5. Start linked reauthentication/re-pairing on a classified token rejection. Never automatically call `createAccessToken` during startup, polling, or an action.
6. Document prerequisites, trusted-LAN TLS implications, model variability, and the difference between leaving Art Mode and true panel power.
7. Add unit tests for exact envelopes, omitted pairing params, getter/setter shapes, nested and flat errors, timeout mapping, response-size/JSON validation, deterministic close, host serialization, port fallback only on transport, per-entry token isolation/redaction, target resolution, and no behavior change when unpaired.
8. Require contributor hardware validation for true off, on, reboot, token reuse after restart/power cycle, action behavior from Art Mode, and reachability while off before merging or enabling broader routing.

### Explicitly deferred

- changing `media_player.turn_on` or `media_player.turn_off` semantics;
- automatic power-state polling, a sensor, or coordinator integration;
- IP Control Art Mode getters/setters;
- routing uploads, slideshow, remote keys, picture, sound, or sources over port 1516;
- assuming every model remains reachable while off;
- generalizing weak-TLS fallback beyond models that actually need it.

### Acceptance gate

The package is safe to merge when all protocol/action/config-flow tests pass, tokens are absent from logs and diagnostics, existing WebSocket behavior remains unchanged for unpaired users, and the Issue #24 contributor confirms off/on/reboot on the reported 2026 Frame Pro. Only after that evidence should a follow-up consider opt-in routing of `media_player.turn_off` to IP Control.
