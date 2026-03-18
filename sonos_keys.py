#!/usr/bin/env python3
"""
Sonos Keys - Control your Sonos Amp volume with Ctrl+volume keys.
Activates automatically when a target external display is connected.
"""

import json
import os
import sys
import threading
import time

import rumps
import soco
from AppKit import (
    NSWindow, NSView, NSColor, NSFont, NSTextField, NSScreen,
    NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
    NSFloatingWindowLevel, NSMakeRect, NSEvent,
)
from Foundation import NSTimer
from Quartz import (
    CGDisplayRegisterReconfigurationCallback,
    CGDisplayVendorNumber,
    CGDisplayModelNumber,
    CGDisplaySerialNumber,
    CGGetActiveDisplayList,
    CGEventTapCreate,
    CGEventTapEnable,
    CGEventGetIntegerValueField,
    CGEventGetFlags,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    kCGDisplayBeginConfigurationFlag,
    kCGSessionEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    CGEventMaskBit,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGKeyboardEventKeycode,
    kCFRunLoopCommonModes,
    kCGEventFlagMaskControl,
    NSSystemDefined,
)
from HIServices import AXIsProcessTrusted

CONFIG_PATH = os.path.expanduser("~/.config/sonos-keys/config.json")
VOLUME_STEP = 2

KEYCODE_F10 = 109
KEYCODE_F11 = 103
KEYCODE_F12 = 111


def log(msg):
    print("[sonos-keys] {}".format(msg), flush=True)


def load_config():
    defaults = {
        "sonos_ip": None,
        "sonos_name": None,
        "target_display": None,
        "volume_step": VOLUME_STEP,
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
            defaults.update(saved)
    return defaults


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def discover_sonos_amp(config):
    if config.get("sonos_ip"):
        try:
            device = soco.SoCo(config["sonos_ip"])
            _ = device.player_name
            return device
        except Exception:
            pass

    if config.get("sonos_name"):
        try:
            device = soco.discovery.by_name(config["sonos_name"])
            if device:
                return device
        except Exception:
            pass

    try:
        devices = soco.discover(timeout=5)
        if devices:
            for d in devices:
                if "amp" in d.player_name.lower():
                    return d
            return next(iter(devices))
    except Exception:
        pass

    return None


def get_connected_displays():
    err, display_ids, count = CGGetActiveDisplayList(10, None, None)
    displays = []
    if err == 0 and display_ids:
        for did in display_ids:
            vendor = CGDisplayVendorNumber(did)
            model = CGDisplayModelNumber(did)
            serial = CGDisplaySerialNumber(did)
            displays.append({"vendor": vendor, "model": model, "serial": serial, "id": did})
    return displays


def has_target_display(config):
    target = config.get("target_display")
    displays = get_connected_displays()

    if target is None:
        return len(displays) > 0

    for d in displays:
        if d["vendor"] == target["vendor"] and d["model"] == target["model"]:
            return True
    return False


class VolumeHUD:
    """Floating HUD window that shows volume level."""

    def __init__(self):
        self._window = None
        self._bar_fill = None
        self._icon_label = None
        self._hide_timer = None

    def _ensure_window(self):
        if self._window is not None:
            return

        screen = NSScreen.mainScreen()
        sf = screen.frame()
        hud_w = 220
        hud_h = 40
        x = (sf.size.width - hud_w) / 2
        y = sf.size.height * 0.18

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, hud_w, hud_h),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self._window.setOpaque_(False)
        self._window.setBackgroundColor_(NSColor.clearColor())
        self._window.setLevel_(NSFloatingWindowLevel + 1)
        self._window.setIgnoresMouseEvents_(True)
        self._window.setCollectionBehavior_(1 << 4)
        self._window.setHasShadow_(False)

        content = self._window.contentView()

        bg = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, hud_w, hud_h))
        bg.setWantsLayer_(True)
        bg.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.1, 0.1, 0.1, 0.85).CGColor()
        )
        bg.layer().setCornerRadius_(12)
        content.addSubview_(bg)

        self._icon_label = NSTextField.labelWithString_("\U0001F50A")
        self._icon_label.setFont_(NSFont.systemFontOfSize_(20))
        self._icon_label.setFrame_(NSMakeRect(10, 6, 36, 28))
        self._icon_label.setTextColor_(NSColor.whiteColor())
        self._icon_label.setBackgroundColor_(NSColor.clearColor())
        self._icon_label.setBezeled_(False)
        self._icon_label.setEditable_(False)
        content.addSubview_(self._icon_label)

        bar_x, bar_y, bar_w, bar_h = 50, 14, 150, 10
        bar_bg = NSView.alloc().initWithFrame_(NSMakeRect(bar_x, bar_y, bar_w, bar_h))
        bar_bg.setWantsLayer_(True)
        bar_bg.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.4, 0.4, 0.4, 0.6).CGColor()
        )
        bar_bg.layer().setCornerRadius_(5)
        content.addSubview_(bar_bg)

        self._bar_fill = NSView.alloc().initWithFrame_(NSMakeRect(bar_x, bar_y, 0, bar_h))
        self._bar_fill.setWantsLayer_(True)
        self._bar_fill.layer().setBackgroundColor_(NSColor.whiteColor().CGColor())
        self._bar_fill.layer().setCornerRadius_(5)
        content.addSubview_(self._bar_fill)

    def show(self, volume, muted=False):
        try:
            self._ensure_window()

            icon = "\U0001F507" if muted else ("\U0001F509" if volume < 33 else "\U0001F50A")
            self._icon_label.setStringValue_(icon)

            fill_w = 0 if muted else 150 * (volume / 100.0)
            frame = self._bar_fill.frame()
            frame.size.width = fill_w
            self._bar_fill.setFrame_(frame)

            self._window.setAlphaValue_(1.0)
            self._window.orderFrontRegardless()
        except Exception as e:
            log("HUD error: {}".format(e))

        if self._hide_timer is not None:
            self._hide_timer.invalidate()

        self._hide_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.5, False, lambda t: self._fade_out()
        )

    def _fade_out(self):
        if self._window is not None:
            self._window.orderOut_(None)


class SonosKeysApp(rumps.App):
    def __init__(self):
        super().__init__("Sonos Keys", title="\U0001F508")
        self.config = load_config()
        self.sonos = None
        self.active = False
        self.tap = None
        self.lock = threading.Lock()
        self._hud = VolumeHUD()

        if not AXIsProcessTrusted():
            log("WARNING: Accessibility permission not granted - key capture will not work")

        step = self.config.get("volume_step", VOLUME_STEP)
        self.status_item = rumps.MenuItem("Status: starting...")
        self.device_item = rumps.MenuItem("Device: searching...")
        self.keys_item = rumps.MenuItem("Keys: Ctrl+Vol Down/Up/Mute")
        self.toggle_item = rumps.MenuItem("Override: Auto", callback=self.toggle_override)
        self.displays_item = rumps.MenuItem("Show displays...", callback=self.show_displays)
        self.set_display_item = rumps.MenuItem("Set target display...", callback=self.set_target_display)
        self.set_device_item = rumps.MenuItem("Set Sonos device...", callback=self.set_sonos_device)
        self.step_up_item = rumps.MenuItem(
            "Volume step: {} (click to change)".format(step), callback=self.change_step
        )

        self.menu = [
            self.status_item,
            self.device_item,
            self.keys_item,
            None,
            self.toggle_item,
            self.step_up_item,
            None,
            self.displays_item,
            self.set_display_item,
            self.set_device_item,
        ]

        self._override = None

        CGDisplayRegisterReconfigurationCallback(self._display_changed, None)
        self._create_event_tap()
        threading.Thread(target=self._setup, daemon=True).start()

    def _setup(self):
        self._find_sonos()
        self._evaluate_state()

    def _find_sonos(self):
        self.device_item.title = "Device: searching..."
        device = discover_sonos_amp(self.config)
        if device:
            self.sonos = device
            self.device_item.title = "Device: {} (vol {})".format(device.player_name, device.volume)
            log("Sonos: {} at {}".format(device.player_name, device.ip_address))
            self.config["sonos_ip"] = device.ip_address
            self.config["sonos_name"] = device.player_name
            save_config(self.config)
        else:
            self.device_item.title = "Device: not found"
            self.sonos = None

    def _display_changed(self, display_id, flags, userinfo):
        if flags & kCGDisplayBeginConfigurationFlag:
            return
        threading.Thread(target=self._delayed_evaluate, daemon=True).start()

    def _delayed_evaluate(self):
        time.sleep(1)
        self._evaluate_state()

    def _evaluate_state(self):
        if self._override is not None:
            should_activate = self._override
        else:
            should_activate = has_target_display(self.config) and self.sonos is not None

        if should_activate and not self.active:
            self._activate()
        elif not should_activate and self.active:
            self._deactivate()

        self._update_status()

    def _activate(self):
        with self.lock:
            if self.active:
                return
            self.active = True
            if self.tap:
                CGEventTapEnable(self.tap, True)
            log("Active - Ctrl+volume keys controlling Sonos")

    def _deactivate(self):
        with self.lock:
            if not self.active:
                return
            self.active = False
            log("Inactive - volume keys pass through normally")

    def _create_event_tap(self):
        mask = (CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
                | CGEventMaskBit(NSSystemDefined))

        self.tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            mask,
            self._tap_callback,
            None,
        )

        if self.tap is None:
            log("ERROR: Failed to create event tap (check Accessibility permissions)")
            return

        source = CFMachPortCreateRunLoopSource(None, self.tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(self.tap, False)

    def _tap_callback(self, proxy, event_type, event, refcon):
        if event_type == NSSystemDefined:
            return self._handle_media_key(event)

        if event_type not in (kCGEventKeyDown, kCGEventKeyUp):
            return event

        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

        if keycode not in (KEYCODE_F10, KEYCODE_F11, KEYCODE_F12):
            return event

        flags = CGEventGetFlags(event)
        if not (flags & kCGEventFlagMaskControl):
            return event

        if not self.active:
            return event

        is_down = event_type == kCGEventKeyDown
        if is_down:
            if keycode == KEYCODE_F12:
                threading.Thread(target=self._volume_up, daemon=True).start()
            elif keycode == KEYCODE_F11:
                threading.Thread(target=self._volume_down, daemon=True).start()
            elif keycode == KEYCODE_F10:
                threading.Thread(target=self._mute_toggle, daemon=True).start()

        return None  # suppress

    def _handle_media_key(self, event):
        try:
            ns_event = NSEvent.eventWithCGEvent_(event)
            if ns_event is None or ns_event.subtype() != 8:
                return event

            data1 = ns_event.data1()
            key_code = (data1 & 0xFFFF0000) >> 16
            key_state = (data1 & 0xFF00) >> 8

            if key_code not in (0, 1, 7):
                return event

            flags = CGEventGetFlags(event)
            if not (flags & kCGEventFlagMaskControl):
                return event

            if not self.active:
                return event

            if key_state == 0x0A:  # key down
                if key_code == 0:
                    threading.Thread(target=self._volume_up, daemon=True).start()
                elif key_code == 1:
                    threading.Thread(target=self._volume_down, daemon=True).start()
                elif key_code == 7:
                    threading.Thread(target=self._mute_toggle, daemon=True).start()

            return None

        except Exception:
            return event

    def _show_hud(self, volume, muted=False):
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self._hud.show, volume, muted)

    def _volume_up(self):
        if not self.sonos:
            return
        step = self.config.get("volume_step", VOLUME_STEP)
        try:
            self.sonos.set_relative_volume(step)
            vol = self.sonos.volume
            self.device_item.title = "Device: {} (vol {})".format(self.sonos.player_name, vol)
            self._show_hud(vol)
        except Exception:
            threading.Thread(target=self._find_sonos, daemon=True).start()

    def _volume_down(self):
        if not self.sonos:
            return
        step = self.config.get("volume_step", VOLUME_STEP)
        try:
            self.sonos.set_relative_volume(-step)
            vol = self.sonos.volume
            self.device_item.title = "Device: {} (vol {})".format(self.sonos.player_name, vol)
            self._show_hud(vol)
        except Exception:
            threading.Thread(target=self._find_sonos, daemon=True).start()

    def _mute_toggle(self):
        if not self.sonos:
            return
        try:
            self.sonos.mute = not self.sonos.mute
            muted = self.sonos.mute
            vol = 0 if muted else self.sonos.volume
            state = "muted" if muted else "vol {}".format(vol)
            self.device_item.title = "Device: {} ({})".format(self.sonos.player_name, state)
            self._show_hud(vol, muted=muted)
        except Exception:
            threading.Thread(target=self._find_sonos, daemon=True).start()

    def _update_status(self):
        display_connected = has_target_display(self.config)
        if self.active:
            self.title = "\U0001F50A"
            self.status_item.title = "Status: active - Ctrl+volume keys control Sonos"
        elif not self.sonos:
            self.title = "\U0001F508"
            self.status_item.title = "Status: Sonos not found"
        elif not display_connected:
            self.title = "\U0001F508"
            self.status_item.title = "Status: display not connected"
        else:
            self.title = "\U0001F508"
            self.status_item.title = "Status: inactive"

    def toggle_override(self, sender):
        if self._override is None:
            self._override = True
            self.toggle_item.title = "Override: Force ON"
        elif self._override is True:
            self._override = False
            self.toggle_item.title = "Override: Force OFF"
        else:
            self._override = None
            self.toggle_item.title = "Override: Auto"
        self._evaluate_state()

    def show_displays(self, sender):
        displays = get_connected_displays()
        if not displays:
            rumps.alert("No displays detected")
            return
        lines = []
        for d in displays:
            lines.append(
                "Vendor: {}  Model: {}  Serial: {}".format(d["vendor"], d["model"], d["serial"])
            )
        rumps.alert("Connected Displays", "\n".join(lines))

    def set_target_display(self, sender):
        displays = get_connected_displays()
        if not displays:
            rumps.alert("No displays detected")
            return

        if len(displays) == 1:
            target = {"vendor": displays[0]["vendor"], "model": displays[0]["model"]}
            self.config["target_display"] = target
            save_config(self.config)
            rumps.alert("Target display set", "Vendor: {}  Model: {}".format(target["vendor"], target["model"]))
            self._evaluate_state()
        else:
            lines = []
            for i, d in enumerate(displays):
                lines.append("{}. Vendor: {}  Model: {}".format(i + 1, d["vendor"], d["model"]))
            rumps.alert(
                "Multiple displays",
                "Connect only your target display and try again.\n\n" + "\n".join(lines),
            )

    def set_sonos_device(self, sender):
        response = rumps.Window(
            "Enter Sonos IP address (or leave blank to auto-discover):",
            "Set Sonos Device",
            dimensions=(200, 24),
        ).run()
        if response.clicked:
            ip = response.text.strip()
            if ip:
                self.config["sonos_ip"] = ip
                self.config["sonos_name"] = None
            else:
                self.config["sonos_ip"] = None
                self.config["sonos_name"] = None
            save_config(self.config)
            threading.Thread(target=self._find_sonos, daemon=True).start()
            threading.Thread(target=self._evaluate_state, daemon=True).start()

    def change_step(self, sender):
        response = rumps.Window(
            "Enter volume step (1-10):",
            "Volume Step",
            default_text=str(self.config.get("volume_step", VOLUME_STEP)),
            dimensions=(100, 24),
        ).run()
        if response.clicked:
            try:
                step = max(1, min(10, int(response.text.strip())))
                self.config["volume_step"] = step
                save_config(self.config)
                self.step_up_item.title = "Volume step: {} (click to change)".format(step)
            except ValueError:
                pass

    @rumps.timer(30)
    def periodic_check(self, _):
        if not self.sonos:
            threading.Thread(target=self._find_sonos, daemon=True).start()
        self._evaluate_state()


if __name__ == "__main__":
    log("Starting Sonos Keys (pid={})".format(os.getpid()))
    SonosKeysApp().run()
