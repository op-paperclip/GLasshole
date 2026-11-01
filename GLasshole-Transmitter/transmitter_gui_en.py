#!/usr/bin/env python3
"""
Pixel Update Screenshot Sender
Captures a monitor (defaults to the leftmost) as the video source and streams
per-pixel updates to the ESP32 receiver using the same pixel-update protocol.
"""

import argparse
import socket
import struct
import time
from typing import Optional, Sequence

import cv2
import mss
import numpy as np
import ctypes

try:
    from Quartz import CGEventCreate, CGEventGetLocation
except Exception:  # noqa: BLE001
    CGEventCreate = None  # type: ignore
    CGEventGetLocation = None  # type: ignore


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

DEFAULT_IP = "192.168.4.1"
DEFAULT_PORT = 8090
DISPLAY_WIDTH = 126
DISPLAY_HEIGHT = 126
HEADER_VERSION = 0x02  # carries frame_id in header (pixels)
RUN_HEADER_VERSION = 0x01  # version for run packets


class ScreenshotPixelSender:
    def __init__(
        self,
        ip: str,
        port: int,
        monitor_index: Optional[int],
        prefer_largest: bool,
        target_fps: float,
        threshold: int,
        full_frame: bool,
        max_updates_per_frame: int,
        rotate_deg: int,
        show_cursor: bool,
    ) -> None:
        self.ip = ip
        self.port = port
        self.monitor_index = monitor_index
        self.prefer_largest = prefer_largest
        self.target_fps = target_fps
        self.threshold = threshold
        self.full_frame = full_frame
        self.max_updates_per_frame = max_updates_per_frame
        self.rotate_deg = rotate_deg
        self.show_cursor = show_cursor

        self.sock: Optional[socket.socket] = None
        self.prev_rgb: Optional[np.ndarray] = None  # (H, W, 3) uint8
        self.sent_initial_full: bool = False
        self.frame_id: int = 0
        self.monitor: Optional[dict] = None
        self.sct: Optional[mss.mss] = None
        self.cursor_warned: bool = False
        self.cursor_backend: Optional[tuple[str, Optional[ctypes.CDLL]]] = self._init_cursor_backend()

        # GUI integration. The callback is optional, so CLI behavior remains unchanged.
        self.log_callback = None
        self.stop_requested = False

    def log(self, message: str) -> None:
        """Print to the console and, when a GUI is attached, forward to it."""
        print(message, flush=True)
        if self.log_callback is not None:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def stop(self) -> None:
        """Request a clean stop of the streaming loop."""
        self.stop_requested = True

    def _init_cursor_backend(self) -> Optional[tuple[str, Optional[ctypes.CDLL]]]:
        # Prefer Quartz if available (pyobjc); otherwise fall back to CoreGraphics via ctypes
        if CGEventCreate is not None and CGEventGetLocation is not None:
            return ("quartz", None)
        try:
            cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGEventCreate.restype = ctypes.c_void_p
            cg.CGEventGetLocation.restype = CGPoint
            cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
            return ("ctypes", cg)
        except Exception:
            return None

    # Connection helpers -------------------------------------------------
    def ensure_connection(self) -> bool:
        if self.sock:
            return True
        return self.connect()

    def connect(self, retries: int = 1) -> bool:
        for attempt in range(1, retries + 1):
            try:
                if self.sock:
                    self.sock.close()
                self.log(f"[CONNECT] Attempt {attempt}/{retries} to {self.ip}:{self.port}")
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock.settimeout(10)
                self.sock.connect((self.ip, self.port))
                self.log("[CONNECT] ✓ Connected")
                return True
            except Exception as exc:  # noqa: BLE001
                self.log(f"[CONNECT] ✗ {type(exc).__name__}: {exc}")
                if self.sock:
                    self.sock.close()
                self.sock = None
                if attempt < retries:
                    if self.stop_requested:
                        break
                    time.sleep(2)
        return False

    def disconnect(self) -> None:
        if self.sock:
            self.sock.close()
        self.sock = None
        self.log("[CONNECT] Disconnected")

    # Monitor helpers ----------------------------------------------------
    @staticmethod
    def _select_monitor(
        monitors: Sequence[dict],
        monitor_index: Optional[int],
        prefer_largest: bool,
    ) -> Optional[dict]:
        if monitor_index is not None:
            if 1 <= monitor_index < len(monitors):
                return monitors[monitor_index]
            self.log(f"[MON] Invalid monitor index {monitor_index}; available 1..{len(monitors) - 1}")
            return None

        usable_monitors = monitors[1:]  # index 0 is the virtual bounding box
        if not usable_monitors:
            return None

        if prefer_largest:
            return max(
                usable_monitors,
                key=lambda m: m.get("width", 0) * m.get("height", 0),
            )

        # Default: choose the leftmost monitor (smallest 'left' coordinate)
        usable_monitors = monitors[1:]  # index 0 is the virtual bounding box
        if not usable_monitors:
            return None
        return min(usable_monitors, key=lambda m: m.get("left", 0))

    def setup_capture(self) -> bool:
        try:
            self.sct = mss.mss()
        except Exception as exc:  # noqa: BLE001
            self.log(f"[MON] Unable to start screen capture: {exc}")
            return False

        monitor = self._select_monitor(self.sct.monitors, self.monitor_index, self.prefer_largest)
        if not monitor:
            self.log("[MON] No monitor selected or available")
            return False

        self.monitor = monitor
        print(
            f"[MON] Using monitor at ({monitor['left']}, {monitor['top']}) "
            f"{monitor['width']}x{monitor['height']}"
        )
        return True

    def grab_frame(self) -> Optional[np.ndarray]:
        if not self.sct or not self.monitor:
            return None
        try:
            shot = self.sct.grab(self.monitor)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[MON] Capture failed: {exc}")
            return None
        # mss returns BGRA; drop alpha
        frame = np.array(shot)[:, :, :3]
        return frame

    # Cursor helpers -----------------------------------------------------
    def get_cursor_global(self) -> Optional[tuple[int, int]]:
        if not self.cursor_backend:
            if self.show_cursor and not self.cursor_warned:
                print(
                    "[CURSOR] No cursor backend available (need Screen Recording permission and "
                    "either Quartz/pyobjc or CoreGraphics via ctypes)"
                )
                self.cursor_warned = True
            return None
        backend, cg = self.cursor_backend
        try:
            if backend == "quartz":
                evt = CGEventCreate(None)
                if evt is None:
                    return None
                loc = CGEventGetLocation(evt)
                return int(loc.x), int(loc.y)
            if backend == "ctypes" and cg is not None:
                evt = cg.CGEventCreate(None)
                if not evt:
                    return None
                loc = cg.CGEventGetLocation(evt)
                return int(loc.x), int(loc.y)
        except Exception:
            return None
        return None

    def map_cursor_to_local(self, cursor: tuple[int, int]) -> Optional[tuple[int, int]]:
        if not self.monitor:
            return None
        cx, cy = cursor
        left = int(self.monitor.get("left", 0))
        top = int(self.monitor.get("top", 0))
        width = int(self.monitor.get("width", 0))
        height = int(self.monitor.get("height", 0))

        if width <= 0 or height <= 0:
            return None

        local_x = cx - left
        local_y = cy - top
        if local_x < 0 or local_y < 0 or local_x >= width or local_y >= height:
            if self.show_cursor and not self.cursor_warned:
                self.log(f"[CURSOR] Cursor not on selected monitor (global {cx},{cy})")
                self.cursor_warned = True
            return None  # cursor not on this monitor

        if self.show_cursor and not self.cursor_warned:
            self.log(f"[CURSOR] Global {cx},{cy} -> local {local_x},{local_y}")
            self.cursor_warned = True
        return int(local_x), int(local_y)

    # Conversion helpers -------------------------------------------------
    @staticmethod
    def rgb888_to_rgb565(rgb: np.ndarray) -> np.ndarray:
        r = (rgb[:, :, 0] >> 3).astype(np.uint16)
        g = (rgb[:, :, 1] >> 2).astype(np.uint16)
        b = (rgb[:, :, 2] >> 3).astype(np.uint16)
        return (r << 11) | (g << 5) | b

    def draw_cursor_icon(self, frame: np.ndarray, point: tuple[int, int]) -> None:
        x, y = point
        # Simple white arrow with black outline, drawn on full-res frame
        scale = 1.8  # enlarge arrow
        pts = np.array(
            [
                (0, 0),
                (0, int(12 * scale)),
                (int(4 * scale), int(10 * scale)),
                (int(8 * scale), int(16 * scale)),
                (int(10 * scale), int(14 * scale)),
                (int(6 * scale), int(8 * scale)),
                (int(12 * scale), int(8 * scale)),
            ],
            dtype=np.int32,
        )
        pts[:, 0] += x
        pts[:, 1] += y

        cv2.fillPoly(frame, [pts], (0, 0, 0))
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 0), thickness=2, lineType=cv2.LINE_AA)
        cv2.fillPoly(frame, [pts], (255, 255, 255))
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 0), thickness=1, lineType=cv2.LINE_AA)

    def resize_and_convert(
        self, frame: np.ndarray, cursor_point_local: Optional[tuple[int, int]]
    ) -> tuple[np.ndarray, np.ndarray]:
        # Ensure contiguous buffer for OpenCV drawing (mss can return non-contiguous)
        frame = np.ascontiguousarray(frame)

        # Draw cursor on the full-res capture before rotation/scale
        if cursor_point_local:
            cx, cy = cursor_point_local
            h, w, _ = frame.shape
            if 0 <= cx < w and 0 <= cy < h:
                self.draw_cursor_icon(frame, (cx, cy))

        # Apply rotation to match device orientation
        if self.rotate_deg == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self.rotate_deg == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self.rotate_deg == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        resized = cv2.resize(frame, (DISPLAY_WIDTH, DISPLAY_HEIGHT))

        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        rgb565 = self.rgb888_to_rgb565(rgb)
        return rgb, rgb565

    # Packet creation ----------------------------------------------------
    def build_packets(self, rgb: np.ndarray, rgb565: np.ndarray) -> list[bytes]:
        # Force the first frame to be full-frame, then optionally delta-mode
        if self.full_frame or not self.sent_initial_full or self.prev_rgb is None:
            mask = np.ones((DISPLAY_HEIGHT, DISPLAY_WIDTH), dtype=bool)
        else:
            diff = np.abs(rgb.astype(np.int16) - self.prev_rgb.astype(np.int16))
            mask = diff.max(axis=2) > self.threshold

        ys, xs = np.nonzero(mask)
        colors = rgb565[ys, xs]
        count = len(colors)

        # If no pixels changed, send an empty pixel frame to keep sync
        packets: list[bytes] = []
        if count == 0:
            header = (
                b"PXUP"
                + bytes([HEADER_VERSION])
                + struct.pack("<I", self.frame_id)
                + struct.pack("<H", 0)
            )
            packets.append(header)
            self.frame_id += 1
            return packets

        # Try run-length encoding by rows; choose smaller payload
        run_packets = self._build_run_packets(mask, rgb565)
        pixel_packets = self._build_pixel_packets(xs, ys, colors, count)

        run_bytes = sum(len(p) for p in run_packets)
        pixel_bytes = sum(len(p) for p in pixel_packets)
        if run_bytes < pixel_bytes:
            self.frame_id += len(run_packets)
            return run_packets
        self.frame_id += len(pixel_packets)
        return pixel_packets

    def _build_pixel_packets(
        self, xs: np.ndarray, ys: np.ndarray, colors: np.ndarray, count: int
    ) -> list[bytes]:
        packets: list[bytes] = []
        max_per = max(1, self.max_updates_per_frame)
        start = 0
        while start < count:
            end = min(start + max_per, count)
            slice_count = end - start
            header = (
                b"PXUP"
                + bytes([HEADER_VERSION])
                + struct.pack("<I", self.frame_id)
                + struct.pack("<H", slice_count)
            )
            payload = bytearray(header)
            append = payload.extend
            for x, y, color in zip(xs[start:end], ys[start:end], colors[start:end]):
                append(struct.pack("<BBH", int(x), int(y), int(color)))
            packets.append(bytes(payload))
            start = end
        return packets

    def _build_run_packets(self, mask: np.ndarray, rgb565: np.ndarray) -> list[bytes]:
        packets: list[bytes] = []
        max_per = max(1, self.max_updates_per_frame)
        runs: list[tuple[int, int, int, int]] = []  # y, x0, length, color

        for y in range(DISPLAY_HEIGHT):
            row_mask = mask[y]
            if not row_mask.any():
                continue
            x = 0
            while x < DISPLAY_WIDTH:
                if not row_mask[x]:
                    x += 1
                    continue
                x0 = x
                color = int(rgb565[y, x0])
                x += 1
                while x < DISPLAY_WIDTH and row_mask[x] and int(rgb565[y, x]) == color:
                    x += 1
                length = x - x0
                runs.append((y, x0, length, color))

        total_runs = len(runs)
        if total_runs == 0:
            header = (
                b"PXUP"
                + bytes([HEADER_VERSION])
                + struct.pack("<I", self.frame_id)
                + struct.pack("<H", 0)
            )
            packets.append(header)
            return packets

        start = 0
        while start < total_runs:
            end = min(start + max_per, total_runs)
            slice_count = end - start
            header = (
                b"PXUR"
                + bytes([RUN_HEADER_VERSION])
                + struct.pack("<I", self.frame_id)
                + struct.pack("<H", slice_count)
            )
            payload = bytearray(header)
            append = payload.extend
            for y, x0, length, color in runs[start:end]:
                append(struct.pack("<BBBH", y, x0, length, color))
            packets.append(bytes(payload))
            start = end
        return packets

    # Main loop ----------------------------------------------------------
    def run(self) -> None:
        self.stop_requested = False
        if not self.setup_capture():
            return
        frame_delay = 1.0 / self.target_fps if self.target_fps > 0 else 0.0
        frame_count = 0
        sent_packets = 0
        sent_pixels = 0
        start_t = time.time()

        self.log("[STREAM] Starting screenshot update loop (Ctrl+C to stop)")
        try:
            while not self.stop_requested:
                frame_start = time.time()
                frame = self.grab_frame()
                if frame is None:
                    self.log("[STREAM] Capture stopped")
                    break

                cursor_point = None
                if self.show_cursor:
                    cur = self.get_cursor_global()
                    if cur:
                        cursor_point = self.map_cursor_to_local(cur)
                    else:
                        self.log("[CURSOR] Unable to read cursor position")

                rgb, rgb565 = self.resize_and_convert(frame, cursor_point)
                packets = self.build_packets(rgb, rgb565)
                self.prev_rgb = rgb

                if not self.ensure_connection():
                    self.log("[SEND] T-Glass not reachable; continuing capture")
                    continue

                for pkt in packets:
                    updates_in_frame = struct.unpack_from("<H", pkt, 9)[0]
                    self.log(f"[FRAME] id={struct.unpack_from('<I', pkt, 5)[0]} updates={updates_in_frame}")
                    try:
                        self.sock.sendall(pkt)
                        sent_packets += 1
                        sent_pixels += updates_in_frame
                        if not self.sent_initial_full:
                            self.sent_initial_full = True
                    except (BrokenPipeError, ConnectionResetError):
                        self.log("[SEND] Connection lost; attempting reconnect")
                        self.disconnect()
                        if not self.ensure_connection():
                            self.log("[SEND] Reconnect failed; continuing capture")
                            break
                        try:
                            self.sock.sendall(pkt)
                            sent_packets += 1
                            sent_pixels += updates_in_frame
                        except Exception as exc:  # noqa: BLE001
                            self.log(f"[SEND] Retry failed: {type(exc).__name__}: {exc}")
                            break
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"[SEND] Error: {type(exc).__name__}: {exc}")
                        self.disconnect()
                        break
                else:
                    frame_count += 1
                    now = time.time()
                    elapsed_frame = now - frame_start
                    if frame_delay > 0 and elapsed_frame < frame_delay:
                        remaining = frame_delay - elapsed_frame
                        end_wait = time.time() + remaining
                        while not self.stop_requested:
                            left = end_wait - time.time()
                            if left <= 0:
                                break
                            time.sleep(min(0.05, left))

                    # Stats roughly every second
                    if now - start_t >= 1.0:
                        elapsed = now - start_t
                        fps_est = frame_count / elapsed if elapsed > 0 else 0.0
                        print(
                            f"[STATS] frames:{frame_count} packets:{sent_packets} "
                            f"pixels:{sent_pixels} fps~{fps_est:.2f}"
                        )
                        start_t = now
                        frame_count = 0
                        sent_packets = 0
                        sent_pixels = 0
                    continue
                continue  # keep capture running if the connection is unavailable
        except KeyboardInterrupt:
            self.log("\n[STREAM] Interrupted by user")
        finally:
            self.disconnect()
            if self.sct:
                self.sct.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a monitor and send per-pixel updates to ESP32"
    )
    parser.add_argument("--ip", type=str, default=DEFAULT_IP, help="ESP32 IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port (default 8090)")
    parser.add_argument(
        "--monitor-index",
        type=int,
        default=None,
        help="Monitor index as reported by mss (1-based). Default: leftmost monitor",
    )
    parser.add_argument(
        "--prefer-largest",
        action="store_true",
        help="Pick the largest-resolution monitor instead of the leftmost",
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=15.0,
        help="Max capture/send rate (frames per second)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=5,
        help="Per-channel diff threshold (0-255) to consider a pixel changed",
    )
    parser.add_argument(
        "--full-frame",
        action="store_true",
        help="Send every pixel of every frame (no diffing)",
    )
    parser.add_argument(
        "--max-updates-per-frame",
        type=int,
        default=3000,
        help="Cap updates per sent frame slice (default 3000)",
    )
    parser.add_argument(
        "--rotate",
        type=int,
        choices=[0, 90, 180, 270],
        default=0,
        help="Rotate capture before scaling to match device orientation",
    )
    parser.add_argument(
        "--show-cursor",
        action="store_true",
        help="Draw the cursor location onto the captured frame (requires Quartz/pyobjc on macOS)",
    )
    return parser.parse_args(argv)



# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

import threading
import queue


PORT_OPTIONS = list(range(8090, 8100))
DEFAULT_HOSTNAME = "glasshole.local"


def resolve_hostname(hostname: str) -> Optional[str]:
    """
    Resolve a hostname using Python's normal socket resolver.

    On systems where .local is provided by mDNS/Avahi/Bonjour, this resolves
    glasshole.local without requiring a GUI-specific or Kivy-specific component.
    """
    try:
        infos = socket.getaddrinfo(
            hostname,
            None,
            socket.AF_INET,
            socket.SOCK_STREAM,
        )
        for info in infos:
            address = info[4][0]
            if address:
                return address
    except Exception:
        return None
    return None


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a monitor and send per-pixel updates to ESP32"
    )
    parser.add_argument("--ip", type=str, default=DEFAULT_IP, help="ESP32 IP or hostname")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port (default 8090)")
    parser.add_argument(
        "--monitor-index",
        type=int,
        default=None,
        help="Monitor index as reported by mss (1-based). Default: leftmost monitor",
    )
    parser.add_argument(
        "--prefer-largest",
        action="store_true",
        help="Pick the largest-resolution monitor instead of the leftmost",
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=15.0,
        help="Max capture/send rate (frames per second)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=5,
        help="Per-channel diff threshold (0-255) to consider a pixel changed",
    )
    parser.add_argument(
        "--full-frame",
        action="store_true",
        help="Send every pixel of every frame (no diffing)",
    )
    parser.add_argument(
        "--max-updates-per-frame",
        type=int,
        default=3000,
        help="Cap updates per sent frame slice (default 3000)",
    )
    parser.add_argument(
        "--rotate",
        type=int,
        choices=[0, 90, 180, 270],
        default=0,
        help="Rotate capture before scaling to match device orientation",
    )
    parser.add_argument(
        "--show-cursor",
        action="store_true",
        help="Draw the cursor location onto the captured frame",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Start the graphical interface",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in command-line mode",
    )
    return parser.parse_args(argv)


class TransmitterGUI:
    """
    Small tkinter GUI around the existing ScreenshotPixelSender.

    The controls are intentionally large and simple so the same layout is
    comfortable with a mouse, touch screen, or a future mobile front-end.
    """

    BG = "#202124"
    FG = "#f1f3f4"
    ENTRY_BG = "#303134"
    BUTTON_BG = "#3c4043"

    def __init__(self, root, initial_args: argparse.Namespace):
        self.root = root
        self.root.title("GLasshole-Transmitter")
        self.root.geometry("520x760")
        self.root.minsize(420, 620)
        self.root.configure(bg=self.BG)

        self.initial_args = initial_args
        self.sender: Optional[ScreenshotPixelSender] = None
        self.worker: Optional[threading.Thread] = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.running = False

        self.host_var = None
        self.port_var = None
        self.fps_var = None
        self.threshold_var = None
        self.monitor_var = None
        self.rotate_var = None
        self.cursor_var = None
        self.largest_var = None
        self.full_frame_var = None
        self.max_updates_var = None

        self._build_style()
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self._drain_log_queue)

        self._log("GLasshole-Transmitter GUI started")
        self._log("Display: 126 x 126")
        self._log("TCP Ports: 8090 - 8099")
        self._log("Note: glasshole.local can be used as hostname.")

    def _build_style(self):
        import tkinter.ttk as ttk

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "TLabel",
            background=self.BG,
            foreground=self.FG,
            font=("TkDefaultFont", 12),
        )
        style.configure(
            "Title.TLabel",
            background=self.BG,
            foreground=self.FG,
            font=("TkDefaultFont", 18, "bold"),
        )
        style.configure(
            "TButton",
            font=("TkDefaultFont", 13, "bold"),
            padding=(12, 10),
        )
        style.configure(
            "TCombobox",
            font=("TkDefaultFont", 13),
            padding=8,
        )
        style.configure(
            "TCheckbutton",
            background=self.BG,
            foreground=self.FG,
            font=("TkDefaultFont", 12),
        )

    def _build_ui(self):
        import tkinter as tk
        import tkinter.ttk as ttk

        outer = tk.Frame(self.root, bg=self.BG)
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        ttk.Label(
            outer,
            text="GLasshole-Transmitter",
            style="Title.TLabel",
        ).pack(anchor="w", pady=(0, 14))

        # Connection section
        connection = tk.LabelFrame(
            outer,
            text=" Connection ",
            bg=self.BG,
            fg=self.FG,
            font=("TkDefaultFont", 12, "bold"),
            bd=1,
            relief="groove",
            padx=10,
            pady=8,
        )
        connection.pack(fill="x", pady=(0, 10))

        self.host_var = tk.StringVar(value=self.initial_args.ip)
        self.port_var = tk.StringVar(value=str(self.initial_args.port))

        ttk.Label(connection, text="Host / IP").grid(
            row=0, column=0, sticky="w", padx=4, pady=5
        )

        host_entry = tk.Entry(
            connection,
            textvariable=self.host_var,
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            font=("TkDefaultFont", 14),
        )
        host_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=5, ipady=7)

        mdns_button = ttk.Button(
            connection,
            text="mDNS",
            command=self.resolve_mdns,
        )
        mdns_button.grid(row=0, column=2, sticky="ew", padx=4, pady=5, ipady=3)

        ttk.Label(connection, text="TCP Port").grid(
            row=1, column=0, sticky="w", padx=4, pady=5
        )

        port_combo = ttk.Combobox(
            connection,
            textvariable=self.port_var,
            values=[str(p) for p in PORT_OPTIONS],
            state="readonly",
            font=("TkDefaultFont", 14),
        )
        port_combo.grid(row=1, column=1, sticky="ew", padx=4, pady=5, ipady=5)

        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(2, weight=0)

        # Existing sender settings, kept simple and touch-friendly.
        settings = tk.LabelFrame(
            outer,
            text=" Settings ",
            bg=self.BG,
            fg=self.FG,
            font=("TkDefaultFont", 12, "bold"),
            bd=1,
            relief="groove",
            padx=10,
            pady=8,
        )
        settings.pack(fill="x", pady=(0, 10))

        self.fps_var = tk.StringVar(value=str(self.initial_args.target_fps))
        self.threshold_var = tk.StringVar(value=str(self.initial_args.threshold))
        self.monitor_var = tk.StringVar(
            value="" if self.initial_args.monitor_index is None else str(self.initial_args.monitor_index)
        )
        self.rotate_var = tk.StringVar(value=str(self.initial_args.rotate))
        self.max_updates_var = tk.StringVar(
            value=str(self.initial_args.max_updates_per_frame)
        )

        ttk.Label(settings, text="FPS").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        tk.Entry(
            settings,
            textvariable=self.fps_var,
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            font=("TkDefaultFont", 13),
            width=8,
        ).grid(row=0, column=1, sticky="ew", padx=4, pady=4, ipady=5)

        ttk.Label(settings, text="Threshold").grid(row=0, column=2, sticky="w", padx=4, pady=4)
        tk.Entry(
            settings,
            textvariable=self.threshold_var,
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            font=("TkDefaultFont", 13),
            width=8,
        ).grid(row=0, column=3, sticky="ew", padx=4, pady=4, ipady=5)

        ttk.Label(settings, text="Monitor").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        tk.Entry(
            settings,
            textvariable=self.monitor_var,
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            font=("TkDefaultFont", 13),
            width=8,
        ).grid(row=1, column=1, sticky="ew", padx=4, pady=4, ipady=5)

        ttk.Label(settings, text="Rotation").grid(row=1, column=2, sticky="w", padx=4, pady=4)
        ttk.Combobox(
            settings,
            textvariable=self.rotate_var,
            values=["0", "90", "180", "270"],
            state="readonly",
            font=("TkDefaultFont", 13),
            width=8,
        ).grid(row=1, column=3, sticky="ew", padx=4, pady=4, ipady=3)

        ttk.Label(settings, text="Max Updates").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        tk.Entry(
            settings,
            textvariable=self.max_updates_var,
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            font=("TkDefaultFont", 13),
        ).grid(row=2, column=1, columnspan=3, sticky="ew", padx=4, pady=4, ipady=5)

        self.largest_var = tk.BooleanVar(value=self.initial_args.prefer_largest)
        self.full_frame_var = tk.BooleanVar(value=self.initial_args.full_frame)
        self.cursor_var = tk.BooleanVar(value=self.initial_args.show_cursor)

        ttk.Checkbutton(
            settings,
            text="Use largest monitor",
            variable=self.largest_var,
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=4, pady=5)

        ttk.Checkbutton(
            settings,
            text="Full Frame",
            variable=self.full_frame_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=5)

        ttk.Checkbutton(
            settings,
            text="Show cursor",
            variable=self.cursor_var,
        ).grid(row=4, column=2, columnspan=2, sticky="w", padx=4, pady=5)

        for col in range(4):
            settings.columnconfigure(col, weight=1)

        # Start/Stop buttons
        buttons = tk.Frame(outer, bg=self.BG)
        buttons.pack(fill="x", pady=(2, 10))

        self.start_button = tk.Button(
            buttons,
            text="START",
            command=self.start,
            bg=self.BUTTON_BG,
            fg=self.FG,
            activebackground="#4b4f52",
            activeforeground=self.FG,
            relief="flat",
            font=("TkDefaultFont", 15, "bold"),
            padx=12,
            pady=12,
        )
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.stop_button = tk.Button(
            buttons,
            text="STOP",
            command=self.stop,
            state="disabled",
            bg=self.BUTTON_BG,
            fg=self.FG,
            activebackground="#4b4f52",
            activeforeground=self.FG,
            relief="flat",
            font=("TkDefaultFont", 15, "bold"),
            padx=12,
            pady=12,
        )
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Log
        log_frame = tk.LabelFrame(
            outer,
            text=" Log ",
            bg=self.BG,
            fg=self.FG,
            font=("TkDefaultFont", 12, "bold"),
            bd=1,
            relief="groove",
            padx=8,
            pady=8,
        )
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            bg="#111315",
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            wrap="word",
            font=("TkFixedFont", 10),
            state="disabled",
        )
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _log(self, message: str):
        self.log_queue.put(str(message))

    def _drain_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", message + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass

        if self.root.winfo_exists():
            self.root.after(50, self._drain_log_queue)

    def resolve_mdns(self):
        hostname = DEFAULT_HOSTNAME
        self._log(f"[mDNS] Searching for {hostname} ...")

        def worker():
            ip = resolve_hostname(hostname)
            if ip:
                self._log(f"[mDNS] {hostname} -> {ip}")
                self.root.after(0, lambda: self.host_var.set(ip))
            else:
                self._log(
                    f"[mDNS] {hostname} could not be resolved. "
                    "You can also enter the hostname directly in the field."
                )

        threading.Thread(target=worker, daemon=True).start()

    def _make_sender(self) -> ScreenshotPixelSender:
        host = self.host_var.get().strip()
        if not host:
            raise ValueError("Host/IP darf nicht leer sein.")

        port = int(self.port_var.get())

        if port not in PORT_OPTIONS:
            raise ValueError("TCP Port muss zwischen 8090 und 8099 liegen.")

        monitor_text = self.monitor_var.get().strip()
        monitor_index = int(monitor_text) if monitor_text else None

        sender = ScreenshotPixelSender(
            ip=host,
            port=port,
            monitor_index=monitor_index,
            prefer_largest=self.largest_var.get(),
            target_fps=float(self.fps_var.get()),
            threshold=int(self.threshold_var.get()),
            full_frame=self.full_frame_var.get(),
            max_updates_per_frame=int(self.max_updates_var.get()),
            rotate_deg=int(self.rotate_var.get()),
            show_cursor=self.cursor_var.get(),
        )
        sender.log_callback = self._log
        return sender

    def start(self):
        if self.running:
            return

        try:
            sender = self._make_sender()
        except Exception as exc:
            self._log(f"[GUI] Invalid settings: {exc}")
            return

        self.sender = sender
        self.running = True

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        self._log(
            f"[GUI] Starting transmission to "
            f"{sender.ip}:{sender.port}"
        )

        self.worker = threading.Thread(
            target=self._run_sender,
            daemon=True,
        )
        self.worker.start()

    def _run_sender(self):
        try:
            if self.sender is not None:
                self.sender.run()
        except Exception as exc:
            self._log(f"[GUI] Unexpected error: {type(exc).__name__}: {exc}")
        finally:
            self.root.after(0, self._sender_finished)

    def _sender_finished(self):
        self.running = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._log("[GUI] Transmission ended.")

    def stop(self):
        if not self.running or self.sender is None:
            return

        self._log("[GUI] Stop requested ...")
        self.sender.stop()

    def on_close(self):
        if self.running and self.sender is not None:
            self._log("[GUI] Stopping transmission ...")
            self.sender.stop()

        self.root.after(100, self._finish_close)

    def _finish_close(self):
        if self.worker is not None and self.worker.is_alive():
            self.root.after(100, self._finish_close)
            return
        self.root.destroy()


def run_gui(args: argparse.Namespace) -> None:
    try:
        import tkinter as tk
    except ImportError as exc:
        print("Tkinter ist nicht verfügbar.")
        print(f"Fehler: {exc}")
        print("Bitte Tkinter installieren oder mit --cli starten.")
        return

    root = tk.Tk()
    TransmitterGUI(root, args)
    root.mainloop()


def run_cli(args: argparse.Namespace) -> None:
    sender = ScreenshotPixelSender(
        ip=args.ip,
        port=args.port,
        monitor_index=args.monitor_index,
        prefer_largest=args.prefer_largest,
        target_fps=args.target_fps,
        threshold=args.threshold,
        full_frame=args.full_frame,
        max_updates_per_frame=args.max_updates_per_frame,
        rotate_deg=args.rotate,
        show_cursor=args.show_cursor,
    )
    sender.run()


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)

    # GUI is the default when available. --cli always forces command-line mode.
    if args.cli:
        run_cli(args)
        return

    if args.gui:
        run_gui(args)
        return

    try:
        import tkinter  # noqa: F401
        run_gui(args)
    except Exception as exc:
        print(f"[GUI] GUI konnte nicht gestartet werden: {type(exc).__name__}: {exc}")
        print("[GUI] Falling back to CLI mode.")
        run_cli(args)


if __name__ == "__main__":
    main()
