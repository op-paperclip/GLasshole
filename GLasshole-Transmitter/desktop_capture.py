"""Desktop screen capture backend for the GLasshole-transmitter."""

import sys

import cv2
import mss
import numpy as np


DISPLAY_WIDTH = 126
DISPLAY_HEIGHT = 126


class DesktopCapture:
    def __init__(
        self,
        monitor_index=None,
        prefer_largest=False,
        rotate_deg=0,
        show_cursor=False,
        log_callback=None,
    ):
        self.monitor_index = monitor_index
        self.prefer_largest = prefer_largest
        self.rotate_deg = rotate_deg
        self.show_cursor = show_cursor
        self.log_callback = log_callback

        self.monitor = None
        self.sct = None

        self.cursor_backend = self._init_cursor_backend()

    def _log(self, message):
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def _init_cursor_backend(self):
        """Initialize the platform-specific cursor backend.

        Currently the cursor position is supported on macOS via Quartz.
        On other platforms this returns None.
        """
        if sys.platform != "darwin":
            return None

        try:
            from Quartz import CGEventGetLocation, CGEventCreate

            return {
                "CGEventCreate": CGEventCreate,
                "CGEventGetLocation": CGEventGetLocation,
            }
        except Exception:
            return None

    def setup(self):
        """Initialize the desktop capture backend and select a monitor."""
        try:
            self.sct = mss.mss()
            monitors = self.sct.monitors

            # monitors[0] is the virtual/all-monitors entry in mss.
            if len(monitors) <= 1:
                self._log("[CAPTURE] No desktop monitor found.")
                return False

            if self.prefer_largest:
                self.monitor = max(
                    monitors[1:],
                    key=lambda m: m["width"] * m["height"],
                )

            elif self.monitor_index is not None:
                index = int(self.monitor_index)

                if index < 1 or index >= len(monitors):
                    self._log(
                        f"[CAPTURE] Invalid monitor index: "
                        f"{self.monitor_index}"
                    )
                    return False

                self.monitor = monitors[index]

            else:
                self.monitor = monitors[1]

            self._log(
                "[CAPTURE] Using monitor: "
                f"left={self.monitor['left']} "
                f"top={self.monitor['top']} "
                f"width={self.monitor['width']} "
                f"height={self.monitor['height']}"
            )

            return True

        except Exception as exc:
            self._log(
                f"[CAPTURE] Setup error: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    def _get_cursor_position(self):
        """Return the current cursor position in desktop coordinates."""
        if not self.cursor_backend:
            return None

        try:
            event = self.cursor_backend["CGEventCreate"](None)
            point = self.cursor_backend["CGEventGetLocation"](event)

            return float(point.x), float(point.y)

        except Exception:
            return None

    def _draw_cursor(self, frame):
        """Draw a simple cursor representation into the frame."""
        position = self._get_cursor_position()

        if position is None or self.monitor is None:
            return

        x = int(position[0] - self.monitor["left"])
        y = int(position[1] - self.monitor["top"])

        if (
            x < 0
            or y < 0
            or x >= frame.shape[1]
            or y >= frame.shape[0]
        ):
            return

        # Simple white mouse pointer.
        cv2.line(
            frame,
            (x, y),
            (x + 10, y + 16),
            (255, 255, 255),
            2,
        )

        cv2.line(
            frame,
            (x, y),
            (x + 4, y + 18),
            (255, 255, 255),
            2,
        )

        cv2.line(
            frame,
            (x + 4, y + 18),
            (x + 10, y + 16),
            (255, 255, 255),
            2,
        )

    def capture(self):
        """Capture one desktop frame.

        Returns:
            numpy.ndarray:
                RGB image with shape (126, 126, 3) and dtype uint8,
                or None if capturing fails.
        """
        if self.sct is None or self.monitor is None:
            if not self.setup():
                return None

        try:
            # mss returns BGRA data.
            raw = np.asarray(self.sct.grab(self.monitor))

            # Drop the alpha channel.
            frame = raw[:, :, :3]

            if self.show_cursor:
                self._draw_cursor(frame)

            # Apply requested rotation.
            rotation = int(self.rotate_deg) % 360

            if rotation == 90:
                frame = cv2.rotate(
                    frame,
                    cv2.ROTATE_90_CLOCKWISE,
                )

            elif rotation == 180:
                frame = cv2.rotate(
                    frame,
                    cv2.ROTATE_180,
                )

            elif rotation == 270:
                frame = cv2.rotate(
                    frame,
                    cv2.ROTATE_90_COUNTERCLOCKWISE,
                )

            # Resize to the GLasshole display resolution.
            frame = cv2.resize(
                frame,
                (DISPLAY_WIDTH, DISPLAY_HEIGHT),
                interpolation=cv2.INTER_AREA,
            )

            # Convert BGR -> RGB.
            frame = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB,
            )

            return frame

        except Exception as exc:
            self._log(
                f"[CAPTURE] Capture error: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

    def grab_frame(self):
        """Common capture-backend interface.

        The Kivy frontend uses grab_frame() for both desktop
        and Android capture backends.
        """
        return self.capture()

    def close(self):
        """Release the desktop capture resources."""
        if self.sct is not None:
            try:
                self.sct.close()
            except Exception:
                pass

        self.sct = None
        self.monitor = None
