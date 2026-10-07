#!/usr/bin/env python3
"""
Sonos Keys - Control your Sonos volume with the keyboard volume keys.
Activates automatically when the default sound output is the target device.
"""

import ctypes
import ctypes.util
import json
import os
import struct
import sys
import threading

import rumps
import soco
from AppKit import (
    NSWindow, NSView, NSColor, NSFont, NSTextField, NSScreen,
    NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
    NSFloatingWindowLevel, NSMakeRect, NSEvent,
)
from Foundation import NSTimer
from Quartz import (
    CGEventTapCreate,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    kCGSessionEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    CGEventMaskBit,
    kCFRunLoopCommonModes,
    NSSystemDefined,
)
from HIServices import AXIsProcessTrusted

CONFIG_PATH = os.path.expanduser("~/.config/sonos-keys/config.json")
VOLUME_STEP = 2

NX_SUBTYPE_AUX_CONTROL_BUTTONS = 8
NX_KEYDOWN = 0x0A
MEDIA_KEY_ACTIONS = {
    0: "_volume_up",
    1: "_volume_down",
    7: "_mute_toggle",
}


def log(msg):
    print("[sonos-keys] {}".format(msg), flush=True)


def load_config():
    defaults = {
        "sonos_ip": None,
        "sonos_name": None,
        "target_output": "Scarlett 2i2",
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


_coreaudio = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
_cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
_cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_cf.CFRelease.argtypes = [ctypes.c_void_p]


class _PropertyAddress(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


def _fourcc(code):
    return struct.unpack(">I", code.encode())[0]


_coreaudio.AudioObjectGetPropertyData.argtypes = [
    ctypes.c_uint32, ctypes.POINTER(_PropertyAddress), ctypes.c_uint32,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
]

def get_default_output_name():
    addr = _PropertyAddress(_fourcc("dOut"), _fourcc("glob"), 0)
    device = ctypes.c_uint32()
    size = ctypes.c_uint32(ctypes.sizeof(device))
    if _coreaudio.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(device)):
        return None
    addr = _PropertyAddress(_fourcc("lnam"), _fourcc("glob"), 0)
    name_ref = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(name_ref))
    if _coreaudio.AudioObjectGetPropertyData(device.value, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(name_ref)):
        return None
    buf = ctypes.create_string_buffer(256)
    ok = _cf.CFStringGetCString(name_ref, buf, len(buf), 0x08000100)
    _cf.CFRelease(name_ref)
    return buf.value.decode() if ok else None


def has_target_output(config):
    name = get_default_output_name()
    return bool(name) and config["target_output"].lower() in name.lower()


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
        self.keys_item = rumps.MenuItem("Keys: Volume Down/Up/Mute")
        self.toggle_item = rumps.MenuItem("Override: Auto", callback=self.toggle_override)
        self.set_output_item = rumps.MenuItem("Set target output...", callback=self.set_target_output)
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
            self.set_output_item,
            self.set_device_item,
        ]

        self._override = None

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

    def _evaluate_state(self):
        if self._override is not None:
            should_activate = self._override
        else:
            should_activate = has_target_output(self.config) and self.sonos is not None

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
            log("Active - volume keys controlling Sonos")

    def _deactivate(self):
        with self.lock:
            if not self.active:
                return
            self.active = False
            if self.tap:
                CGEventTapEnable(self.tap, False)
            log("Inactive - volume keys pass through normally")

    def _create_event_tap(self):
        # Intercepting NSSystemDefined has disrupted Wispr Flow's fn key hook,
        # so the tap is only enabled while active.
        self.tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            CGEventMaskBit(NSSystemDefined),
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
        if event_type in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
            if self.active:
                CGEventTapEnable(self.tap, True)
            return event

        if event_type != NSSystemDefined or not self.active:
            return event

        try:
            ns_event = NSEvent.eventWithCGEvent_(event)
            if ns_event is None or ns_event.subtype() != NX_SUBTYPE_AUX_CONTROL_BUTTONS:
                return event

            data1 = ns_event.data1()
            key_code = (data1 & 0xFFFF0000) >> 16
            key_state = (data1 & 0xFF00) >> 8

            action = MEDIA_KEY_ACTIONS.get(key_code)
            if action is None:
                return event

            if key_state == NX_KEYDOWN:
                threading.Thread(target=getattr(self, action), daemon=True).start()

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
        output_matches = has_target_output(self.config)
        if self.active:
            self.title = "\U0001F50A"
            self.status_item.title = "Status: active - volume keys control Sonos"
        elif not self.sonos:
            self.title = "\U0001F508"
            self.status_item.title = "Status: Sonos not found"
        elif not output_matches:
            self.title = "\U0001F508"
            self.status_item.title = "Status: output is {}".format(get_default_output_name())
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

    def set_target_output(self, sender):
        response = rumps.Window(
            "Activate when the sound output name contains:",
            "Set Target Output",
            default_text=self.config["target_output"],
            dimensions=(200, 24),
        ).run()
        if response.clicked and response.text.strip():
            self.config["target_output"] = response.text.strip()
            save_config(self.config)
            self._evaluate_state()

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

    @rumps.timer(2)
    def output_check(self, _):
        self._evaluate_state()

    @rumps.timer(30)
    def periodic_check(self, _):
        if not self.sonos:
            threading.Thread(target=self._find_sonos, daemon=True).start()
        self._evaluate_state()


if __name__ == "__main__":
    log("Starting Sonos Keys (pid={})".format(os.getpid()))
    SonosKeysApp().run()
