# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python3 pixbar_panel.py                       # local console at http://127.0.0.1:8000
python3 pixbar_panel.py --device <IP> --start agent   # set the device up front, auto-start a plugin
python3 plugins/<name>/plugin.py --dry-run    # run one plugin standalone, print frames, push nothing
python3 plugins/<name>/plugin.py --device <IP> --once

python3 -m unittest tests.test_mqtt           # Python tests (stdlib unittest; no pytest installed)
python3 -m unittest tests.test_mqtt.TestCodec.test_remlen_multibyte   # single test

cd web && npm install                         # not installed by default; needed for every npm command below
cd web && npm test                            # vitest
cd web && npx vitest run src/__tests__/grid.test.ts   # single file
cd web && npm run typecheck                   # vue-tsc --noEmit
cd web && npm run build                       # vue-tsc + vite build -> web/dist
```

`web/dist` is **committed and is what the Python server serves**. A change under `web/src` has no effect on
`http://127.0.0.1:8000` until `npm run build` runs and the new `dist` is committed. While iterating, run
`npm run dev` instead: vite serves the SPA on `:5173` and proxies `/api` to a running `pixbar_panel.py`.

## Architecture

Browser → local Python server (`127.0.0.1:8000`, pure stdlib) → device. The browser never talks to the
clock; the server runs plugin threads, serves the SPA and proxies the device API.

**Three files carry everything:** `pixbar_core.py` (frame helpers, transport, plugin discovery and the
default loop), `pixbar_panel.py` (HTTP console, `Runner`/`Attachment` threads, device reconciliation),
`pixbar_mqtt.py` (hand-rolled MQTT 3.1.1 publisher + subscriber).

**Transports are interchangeable.** The frame JSON is identical either way: HTTP `POST /api/custom?name=<app>`
or MQTT publish to `<prefix>/custom/<app>`. `core.configure_transport()` picks one process-wide; every plugin
works under both because they only call `core.push()`.

**A plugin owns one DIY component on the device.** Pushing a frame creates or updates the component named
`plugin.APP`; pushing `{}` deletes it. That is the whole lifecycle — so "what the clock is showing" is
decided by the last component created, not by the panel's toggles. `reconcile()` (run on each `/api/status`)
deletes components whose plugin is not running, and `main()` clears them all at startup, keeping device state
equal to the switches. `_pushonce_at` / `PUSHONCE_GRACE` exempt a just-previewed component from that sweep.

**Two plugin kinds**, both auto-discovered from `plugins/<name>/plugin.py` (see the `pixbar_core` module
docstring for the full contract): a main plugin declares `APP/NAME/GROUP/DESC/ITEMS/frame_for()` and gets
`core.run_loop` unless it ships its own `run_loop`; an attachment sets `ATTACH = True`, has no component of
its own, and is mounted on a host plugin. An attachment's `inject()` **preempts** the host component for N
seconds — ordinary host pushes are dropped during that window while `force=True` pushes still land, and the
host's next frame naturally paints over it afterwards.

**Device restart / address change is detected, not assumed.** A plugin loop keeps pushing after the clock
reboots, which recreates the component and snatches the display back; the panel therefore stops plugins
instead. Two signals, one per transport: HTTP polls `/api/customList` and only judges a component it has
*seen* and then lost twice in a row (`_seen_on_device`, `WATCHDOG_MISS`) — unreachable ≠ restarted, and a
plugin whose first frame has not landed must never be killed; MQTT subscribes to `<prefix>/status` (the
device's LWT), where a **retained** `online` is replayed state and a **live** `online` means the device just
reconnected. `maybe_rediscover()` finds a moved IP over ARP — MQTT cannot, because every signal there
presumes the device already reached the broker. Because a `Runner` thread captures the device address when it
starts, an address change stops the running plugins rather than trying to swap it underneath them. Note the
coupling: ARP matches the MAC suffix carried by the **MQTT topic prefix**, so that setting has to stay filled
in even under HTTP, where the gear hides its input.

**Why a plugin stopped decides whether it comes back.** A sustained outage (`UNREACHABLE_STOP` ticks with no
answer — you walked out with the laptop) stops the plugins but records the intent in `_autostopped`, and the
first reachable tick starts them again at the same interval. A device restart stops them without that record:
the screen has been handed back to the clock, so only the user decides when to take it again. Manual toggling
clears the record, and the clock itself cannot be cleaned up once out of reach — the stock firmware ignores a
custom app's `lifetime`, so a component keeps showing its last frame until something deletes it.

**Almost nothing persists.** `.pixbar.json` (gitignored) holds only the device address and transport settings.
Plugin on/off, interval, options and all attachments are in-memory and gone on restart — `--start` is the only
way to bring a plugin up automatically. `_load_config()` returns `{}` on any read error, so a save after a bad
read silently drops the other keys.

## Invariants worth not breaking

- **Python side is pure standard library** — the README promises `python3 pixbar_panel.py` with no `pip install`.
  `web/` may use npm, the server may not.
- **Server binds `127.0.0.1`, checks the `Host` header, and `valid_device()` accepts private IPv4 only.** The
  server fetches whatever address it is given, so that check is the SSRF guard; `valid_broker()` additionally
  allows loopback because a local broker is normal.
- **Device font is ASCII, uppercase, non-scrolling, 52×16.** Run user text through `core.ascii_upper()` and use
  `core.text_frame()` / `core.bitmap_frame()` rather than hand-building elements.
- **MQTT `retain` must stay off** for restart detection to mean anything: a retained frame is replayed to the
  device on reconnect and restores the component by itself.
- **Stock firmware only.** Everything rides the official Custom App HTTP protocol (`/api/custom`,
  `/api/customList`, `/getBase`, `/getConfig`); custom firmware would not serve it.
- Comments and docstrings are Chinese, dense, and explain *why*; match that register instead of adding
  English narration.
