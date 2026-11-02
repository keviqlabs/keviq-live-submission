# Replay cohost module

## What it calls, and how it is turned on

- **Voice:** the cohost speaks through the voice built into macOS: the system `say` command (`/usr/bin/say`, the installed Vietnamese voice Linh). Without `--audio` it speaks nothing and prints its lines as JSON instead.
- **No model:** it makes no Model API call and calls no model. The package imports only the Python standard library and its own files; it has no HTTP client, no `meta/` client and no network code, so it emits no `model_call` event. The lines it says are fixed templates filled from approved card data.
- **Off by default:** nothing in the app or the worker imports it, and `deploy/.env.example` sets `COHOST_ENABLED=0`. To turn it on, run the standalone command with `COHOST_ENABLED=1` (for example `COHOST_ENABLED=1 uv run python -m worker.cohost --mode replay`, adding `--audio` to speak aloud on macOS), then send `microphone_ready` and `enable` on its input. With the variable unset or set to anything other than `1`, it stays silent.

## Task and acceptance plan

Build an isolated, default-off speech module for an operator-controlled replay or demo. It must announce itself as an AI assistant before product speech, speak only fixed templates from the current approved card snapshot, serialize output, and cancel speech on host activity. Device changes, microphone loss and session reconnect latch it off for the lifetime of the controller; a new explicit session is required to enable it again. The module must have no imports, clients or callbacks that mutate commerce state.

Implementation stays in this directory, new `tests/test_cohost_*.py` files, a short worker README entry and one default-off example variable. The existing worker does not import this module. No runtime lane or existing application file is changed. A future trusted adapter must supply approved snapshots, host activity and device lifecycle signals; production wiring is outside this change.

Validation: module behavior tests under the shared suite lock; full checks; existing smoke and integration with the flag off and on; secret and history scans. All tests use local fakes or synthetic replay, with no paid model calls. The physical microphone/playback interruption gate remains unmeasured until a supervised headphones session with reference tracks. No hardware latency claim follows from fake-player tests.

## Run a silent synthetic demo

`COHOST_ENABLED` is read once when the controller is constructed; its default is off. Changing it cannot reopen an existing session. There is no new app setting, API endpoint, worker lane or model call.

```sh
COHOST_ENABLED=1 uv run python -m worker.cohost --mode replay <<'EOF'
{"type":"microphone_ready"}
{"type":"enable"}
{"type":"wait","seconds":1.1}
{"type":"card","sequence":1,"card":{"card_id":"sample","version":1,"name":"Sample shoe","price_vnd":250000,"approved":true,"available_sizes":["38","39"]}}
{"type":"turn"}
EOF
```

This prints the fixed Vietnamese AI disclosure and the approved product template. With the flag omitted or `0`, it prints no speech. Unknown size availability is `null` and is omitted from the template; size options from a catalog must never be presented as available stock. Card names, prices and sizes are bounded, immutable values. No arbitrary speech prompt or generated answer is accepted.

A source sequence orders card switches and revocations across all cards. Versions cannot regress or change contents in place. Use a newer sequence with `"card":null` to revoke the current card. A snapshot must be offered for speech within five seconds of acceptance; duplicate updates cannot refresh this window. Each card/version speaks at most once. Product turns have a twenty-second cooldown, require at least one second of observed host silence, and are explicitly requested, never queued for later playback.

## Host and device events

- `{"type":"host","speaking":true}` cancels owned output immediately; `false` starts a new silence window. A later turn still requires an explicit request.
- `device_changed`, `microphone_lost`, `reconnected` and `off` stop output and latch the session off. Later ready/enable events cannot resume it. Restarting requires a new controller, a new explicit enable and fresh mic/card input; nothing is restored from disk.
- The controller serializes playback with a process-wide lease. A player that fails to acknowledge cancellation within one second leaves the controller off and keeps the lease until the player really finishes. A twelve-second watchdog bounds a hung speech request.
- `last_stop_ms` measures software cancellation acknowledgement only. It is not host-to-acoustic-silence latency, and a timeout has no successful stop measurement.

## Optional local audio and trust boundary

On macOS, `--audio` uses the already installed Linh voice through the local `say` process. It does not download a voice or call a provider. This option is for a supervised headphones demo only. Cancellation terminates and reaps that owned process, escalating to a kill if termination does not finish within 300 ms. It never mutes or changes the host microphone.

The JSON-lines input is an operator-owned replay interface, not a live approval/authentication service. The `approved` field represents a trusted replay assertion. A future adapter must derive it from an authenticated seller-approved card/version and supply real mic health, host activity and device-change signals. Model text, transcription speaker labels and arbitrary client messages must never supply that authority. There is no microphone detector, echo canceller, live event subscription or connection back to the event store in this module. Its transcript output must not be ingested as shop speech.

The existing smoke/integration runs with the flag off and on establish core non-interference only: the normal worker does not load this module. They do not establish live cohosting. Hardware interruption latency, voice quality and self-audio isolation are **unmeasured**. The video gate requires a separate supervised run with reference tracks: twenty interruptions, twenty stops, no incorrect sentences, and no assistant speech recognized with seller authority. Paid replay is outside this implementation run.
