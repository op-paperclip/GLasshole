#!/usr/bin/env python3
"""
GLasshole-Transmitter - Kivy frontend

Step 2 of the Android port:
- Kivy replaces the Tkinter GUI.
- transmitter_core.py contains the platform-independent TCP/protocol logic.
- desktop_capture.py contains the desktop-only mss capture backend.

Android screen capture is intentionally NOT implemented in this step.
"""

__version__ = "1.0.0"

import platform
import socket
import threading
from typing import Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from transmitter_core import TGlassTransmitterCore

DEFAULT_HOSTNAME = "glasshole.local"
PORT_OPTIONS = list(range(8090, 8100))


def is_android() -> bool:
    return platform.system().lower() == "android" or "ANDROID_ARGUMENT" in __import__("os").environ


def resolve_hostname(hostname: str) -> Optional[str]:
    try:
        return socket.gethostbyname(hostname)
    except Exception:
        return None


class TransmitterApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sender: Optional[TGlassTransmitterCore] = None
        self.worker: Optional[threading.Thread] = None
        self.capture = None
        self.running = False
        self.log_lines = []

        self.host_input = None
        self.port_spinner = None
        self.fps_input = None
        self.threshold_input = None
        self.monitor_input = None
        self.rotation_spinner = None
        self.max_updates_input = None
        self.largest_check = None
        self.full_frame_check = None
        self.cursor_check = None
        self.start_button = None
        self.stop_button = None
        self.log_label = None

    def build(self):
        self.title = "GLasshole-Transmitter"

        root = BoxLayout(
            orientation="vertical",
            padding=dp(12),
            spacing=dp(8),
        )

        title = Label(
            text="GLasshole-Transmitter",
            font_size=dp(22),
            bold=True,
            size_hint_y=None,
            height=dp(42),
        )
        root.add_widget(title)

        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(
            orientation="vertical",
            spacing=dp(8),
            size_hint_y=None,
        )
        content.bind(minimum_height=content.setter("height"))

        content.add_widget(self._connection_section())
        content.add_widget(self._settings_section())
        content.add_widget(self._button_row())
        content.add_widget(self._log_section())

        scroll.add_widget(content)
        root.add_widget(scroll)

        self.log("GLasshole-Transmitter GUI started")
        self.log("Display: 126 x 126")
        self.log("TCP Ports: 8090 - 8099")
        self.log("Note: glasshole.local can be used as hostname.")
        if is_android():
            self.log("[ANDROID] Android GUI active.")
            self.log("[ANDROID] MediaProjection screen capture backend available.")
        else:
            self.log("[DESKTOP] Kivy GUI active.")

        return root

    def _section(self, title: str):
        box = BoxLayout(
            orientation="vertical",
            spacing=dp(6),
            padding=dp(8),
            size_hint_y=None,
        )
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(Label(
            text=title,
            bold=True,
            font_size=dp(16),
            size_hint_y=None,
            height=dp(32),
            halign="left",
        ))
        return box

    def _row(self, label_text, widget):
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        row.add_widget(Label(text=label_text, size_hint_x=0.34, halign="left"))
        row.add_widget(widget)
        return row

    def _text_input(self, value: str):
        return TextInput(
            text=value,
            multiline=False,
            font_size=dp(16),
            size_hint_x=0.66,
        )

    def _connection_section(self):
        box = self._section("Connection")

        self.host_input = self._text_input("192.168.4.1")
        host_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        host_row.add_widget(Label(text="Host / IP", size_hint_x=0.34, halign="left"))
        host_row.add_widget(self.host_input)
        mdns = Button(text="mDNS", size_hint_x=None, width=dp(82))
        mdns.bind(on_release=lambda *_: self.resolve_mdns())
        host_row.add_widget(mdns)
        box.add_widget(host_row)

        self.port_spinner = Spinner(
            text="8090",
            values=[str(p) for p in PORT_OPTIONS],
            font_size=dp(16),
        )
        box.add_widget(self._row("TCP Port", self.port_spinner))
        return box

    def _settings_section(self):
        box = self._section("Settings")

        self.fps_input = self._text_input("30")
        self.threshold_input = self._text_input("8")
        self.monitor_input = self._text_input("")
        self.rotation_spinner = Spinner(
            text="0",
            values=["0", "90", "180", "270"],
            font_size=dp(16),
        )
        self.max_updates_input = self._text_input("0")

        box.add_widget(self._row("FPS", self.fps_input))
        box.add_widget(self._row("Threshold", self.threshold_input))
        box.add_widget(self._row("Monitor", self.monitor_input))
        box.add_widget(self._row("Rotation", self.rotation_spinner))
        box.add_widget(self._row("Max Updates", self.max_updates_input))

        self.largest_check = CheckBox(active=False, size_hint_x=None, width=dp(48))
        self.full_frame_check = CheckBox(active=False, size_hint_x=None, width=dp(48))
        self.cursor_check = CheckBox(active=False, size_hint_x=None, width=dp(48))

        box.add_widget(self._check_row("Use largest monitor", self.largest_check))
        box.add_widget(self._check_row("Full Frame", self.full_frame_check))
        box.add_widget(self._check_row("Show cursor", self.cursor_check))
        return box

    def _check_row(self, text, checkbox):
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        row.add_widget(checkbox)
        row.add_widget(Label(text=text, halign="left"))
        return row

    def _button_row(self):
        row = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(8))
        self.start_button = Button(text="START", font_size=dp(17), bold=True)
        self.stop_button = Button(text="STOP", font_size=dp(17), bold=True, disabled=True)
        self.start_button.bind(on_release=lambda *_: self.start())
        self.stop_button.bind(on_release=lambda *_: self.stop())
        row.add_widget(self.start_button)
        row.add_widget(self.stop_button)
        return row

    def _log_section(self):
        box = self._section("Log")
        scroll = ScrollView(do_scroll_x=False, size_hint_y=None, height=dp(220))
        self.log_label = Label(
            text="",
            halign="left",
            valign="top",
            size_hint_y=None,
            text_size=(None, None),
        )
        self.log_label.bind(texture_size=self._update_log_size)
        scroll.add_widget(self.log_label)
        box.add_widget(scroll)
        return box

    def _update_log_size(self, instance, size):
        instance.height = max(size[1], dp(220))
        instance.text_size = (instance.width, None)

    def log(self, message: str):
        message = str(message)
        self.log_lines.append(message)
        if len(self.log_lines) > 500:
            self.log_lines = self.log_lines[-500:]
        Clock.schedule_once(lambda *_: self._update_log_text(), 0)

    def _update_log_text(self):
        if self.log_label is not None:
            self.log_label.text = "\n".join(self.log_lines)

    def resolve_mdns(self):
        hostname = DEFAULT_HOSTNAME
        self.log(f"[mDNS] Searching for {hostname} ...")

        def worker():
            ip = resolve_hostname(hostname)
            if ip:
                self.log(f"[mDNS] {hostname} -> {ip}")
                Clock.schedule_once(lambda *_: self._set_host(ip), 0)
            else:
                self.log(
                    f"[mDNS] {hostname} could not be resolved. "
                    "You can also enter the hostname directly in the field."
                )

        threading.Thread(target=worker, daemon=True).start()

    def _set_host(self, value):
        self.host_input.text = value

    def _read_int(self, widget, name):
        try:
            return int(widget.text.strip())
        except Exception:
            raise ValueError(f"{name} must be an integer.")

    def _make_sender(self):
        host = self.host_input.text.strip()
        if not host:
            raise ValueError("Host/IP must not be empty.")

        port = int(self.port_spinner.text)
        if port not in PORT_OPTIONS:
            raise ValueError("TCP Port must be between 8090 and 8099.")

        monitor_text = self.monitor_input.text.strip()
        monitor_index = int(monitor_text) if monitor_text else None

        sender = TGlassTransmitterCore(
            ip=host,
            port=port,
            target_fps=float(self.fps_input.text),
            threshold=int(self.threshold_input.text),
            full_frame=self.full_frame_check.active,
            max_updates_per_frame=int(self.max_updates_input.text),
        )
        sender.log_callback = self.log
        # Capture settings stay in the desktop-only backend.
        sender.monitor_index = monitor_index
        sender.prefer_largest = self.largest_check.active
        sender.rotate_deg = int(self.rotation_spinner.text)
        sender.show_cursor = self.cursor_check.active
        return sender

    def start(self):
        if self.running:
            return

        try:
            self.sender = self._make_sender()
        except Exception as exc:
            self.log(f"[GUI] Invalid settings: {exc}")
            return

        if is_android():
            from android_capture import AndroidCapture

            self.capture = AndroidCapture(
                log_callback=self.log,
                ready_callback=self._android_capture_ready,
                denied_callback=self._android_capture_failed,
                rotate_deg=self.sender.rotate_deg,
            )

            self.running = True
            self.start_button.disabled = True
            self.stop_button.disabled = False
            self.log(
                f"[GUI] Starting Android transmission to "
                f"{self.sender.ip}:{self.sender.port}"
            )

            if not self.capture.request_permission():
                self._android_capture_failed()
            return

        self.running = True
        self.start_button.disabled = True
        self.stop_button.disabled = False
        self.log(f"[GUI] Starting transmission to {self.sender.ip}:{self.sender.port}")

        self.worker = threading.Thread(target=self._run_sender, daemon=True)
        self.worker.start()

    def _run_sender(self):
        try:
            if self.sender is not None:
                if is_android():
                    # Permission is asynchronous. Wait here until the
                    # MediaProjection backend becomes ready.
                    while (
                        self.running
                        and not self.sender.stop_requested
                        and self.capture is not None
                        and not self.capture.ready
                    ):
                        import time
                        time.sleep(0.02)

                    while (
                        self.running
                        and not self.sender.stop_requested
                        and self.capture is not None
                        and self.capture.ready
                    ):
                        frame = self.capture.grab_frame()
                        if frame is None:
                            import time
                            time.sleep(0.005)
                            continue
                        self.sender.send_frame(frame)
                        if self.sender.target_fps > 0:
                            import time
                            time.sleep(1.0 / self.sender.target_fps)
                else:
                    from desktop_capture import DesktopCapture

                    capture = DesktopCapture(
                        monitor_index=self.sender.monitor_index,
                        prefer_largest=self.sender.prefer_largest,
                        rotate_deg=self.sender.rotate_deg,
                        show_cursor=self.sender.show_cursor,
                        log_callback=self.log,
                    )
                    if not capture.setup():
                        return

                    while not self.sender.stop_requested:
                        frame = capture.grab_frame()
                        if frame is None:
                            continue
                        self.sender.send_frame(frame)
                        if self.sender.target_fps > 0:
                            import time
                            time.sleep(1.0 / self.sender.target_fps)
        except Exception as exc:
            self.log(f"[GUI] Unexpected error: {type(exc).__name__}: {exc}")
        finally:
            if self.capture is not None and not is_android():
                self.capture = None
            if self.sender is not None:
                self.sender.disconnect()
            Clock.schedule_once(lambda *_: self._sender_finished(), 0)

    def _android_capture_ready(self):
        self.log("[ANDROID] Capture permission accepted; starting frame loop.")
        if self.worker is None or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._run_sender, daemon=True)
            self.worker.start()

    def _android_capture_failed(self):
        Clock.schedule_once(lambda *_: self._android_capture_failed_ui(), 0)

    def _android_capture_failed_ui(self):
        if self.sender is not None:
            self.sender.stop()
        self.running = False
        self.start_button.disabled = False
        self.stop_button.disabled = True

    def _sender_finished(self):
        self.running = False
        self.start_button.disabled = False
        self.stop_button.disabled = True
        self.log("[GUI] Transmission ended.")

    def stop(self):
        if not self.running or self.sender is None:
            return
        self.log("[GUI] Stop requested ...")
        self.sender.stop()
        if self.capture is not None and is_android():
            try:
                self.capture.close()
            except Exception:
                pass
            self.capture = None

    def on_stop(self):
        if self.sender is not None:
            self.sender.stop()
        if self.capture is not None and is_android():
            try:
                self.capture.close()
            except Exception:
                pass


if __name__ == "__main__":
    TransmitterApp().run()
