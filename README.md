# Sonos Keys

A macOS menu bar app that sends the keyboard volume keys (up, down, mute) to a Sonos speaker, but only while the Mac's sound output is a chosen device. Handy when your output is a USB audio interface (such as a Focusrite Scarlett) that feeds a Sonos amp and has no software volume of its own. With any other output, the keys behave normally.

- Floating volume HUD and a menu bar status icon
- Menu override to force it on or off
- Adjustable volume step

## Setup

```sh
./setup.sh
.venv/bin/python sonos_keys.py
```

Running from a terminal needs that terminal granted Accessibility (System Settings → Privacy & Security → Accessibility).

## Build as an app

```sh
.venv/bin/pip install py2app
.venv/bin/python setup_app.py py2app
cp -R "dist/Sonos Keys.app" /Applications/
```

Grant `Sonos Keys.app` Accessibility, then relaunch it. The build is ad-hoc signed, so each rebuild needs Accessibility granted again (remove the old entry, add the new app).

## Configuration

`~/.config/sonos-keys/config.json`, also editable from the menu:

| Key | Meaning |
| --- | --- |
| `target_output` | Activate when the default output's name contains this (default `Scarlett 2i2`) |
| `sonos_ip` / `sonos_name` | The speaker to control. If unset, it auto-discovers, preferring a player with "amp" in its name |
| `volume_step` | Volume change per key press |

## Known issue

Intercepting media keys has disrupted Wispr Flow's fn key handling, so dictation may misbehave while Sonos Keys is active.
