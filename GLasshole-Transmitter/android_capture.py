"""
Android screen-capture backend for the T-Glass transmitter.

Uses Android MediaProjection + ImageReader and exposes the same grab_frame()
interface as desktop_capture.py.

The backend is intentionally limited to capture/resize. TCP/protocol handling
remains in transmitter_core.py.
"""

import threading

import numpy as np
from kivy.clock import Clock


DISPLAY_WIDTH = 126
DISPLAY_HEIGHT = 126
REQUEST_CODE = 9001



class AndroidCapture:
    def __init__(self, log_callback=None, ready_callback=None, denied_callback=None, rotate_deg=0):
        self.log_callback = log_callback
        self.ready_callback = ready_callback
        self.denied_callback = denied_callback
        self.rotate_deg = rotate_deg
        self.activity = None
        self.projection_manager = None
        self.media_projection = None
        self.virtual_display = None
        self.image_reader = None
        self.surface = None
        self.projection_callback = None

        self.capture_width = 0
        self.capture_height = 0
        self.density_dpi = 0
        self.ready = False
        self.permission_pending = False
        self.closed = False
        self.lock = threading.Lock()

        self._activity_result_bound = False
        self._service_started = False

    def _log(self, message):
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def request_permission(self):
        """Ask Android for screen-capture permission."""
        try:
            from android import activity
            from jnius import autoclass

            self.activity = autoclass(
                "org.kivy.android.PythonActivity"
            ).mActivity
            MediaProjectionManager = autoclass(
                "android.media.projection.MediaProjectionManager"
            )

            self.projection_manager = self.activity.getSystemService(
                "media_projection"
            )

            if not self._activity_result_bound:
                activity.bind(on_activity_result=self._on_activity_result)
                self._activity_result_bound = True

            intent = self.projection_manager.createScreenCaptureIntent()
            self.permission_pending = True
            self._log("[ANDROID] Requesting screen-capture permission ...")
            self.activity.startActivityForResult(intent, REQUEST_CODE)
            return True

        except Exception as exc:
            self._log(
                f"[ANDROID] Screen-capture setup error: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code != REQUEST_CODE:
            return

        self.permission_pending = False

        try:
            from jnius import autoclass

            Activity = autoclass("android.app.Activity")
            if result_code != Activity.RESULT_OK or intent is None:
                self._log("[ANDROID] Screen-capture permission denied.")
                if self.denied_callback:
                    self.denied_callback()
                return

            # Android 14+ requires the media-projection foreground service to
            # be started after user consent and before getMediaProjection().
            if not self._start_foreground_service():
                self._log("[ANDROID] Could not start capture foreground service.")
                return

            # Give Android a moment to put the service into the foreground.
            Clock.schedule_once(
                lambda *_: self._create_projection(result_code, intent),
                0.5,
            )

        except Exception as exc:
            self._log(
                f"[ANDROID] Permission-result error: "
                f"{type(exc).__name__}: {exc}"
            )

    def _start_foreground_service(self):
        if self._service_started:
            return True

        try:
            from jnius import autoclass

            Service = autoclass(
                "org.tglass.tglasstransmitter.ServiceMediaprojection"
            )
            Service.start(self.activity, "")
            self._service_started = True
            self._log("[ANDROID] Capture foreground service started.")
            return True
        except Exception as exc:
            self._log(
                f"[ANDROID] Foreground-service error: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    def _create_projection(self, result_code, intent):
        if self.closed:
            return

        try:
            from jnius import autoclass

            WindowManager = autoclass("android.view.WindowManager")
            DisplayMetrics = autoclass("android.util.DisplayMetrics")
            DisplayManager = autoclass("android.hardware.display.DisplayManager")
            ImageFormat = autoclass("android.graphics.ImageFormat")
            ImageReader = autoclass("android.media.ImageReader")

            metrics = DisplayMetrics()
            wm = self.activity.getSystemService("window")
            display = wm.getDefaultDisplay()
            display.getRealMetrics(metrics)

            self.capture_width = DISPLAY_WIDTH
            self.capture_height = DISPLAY_HEIGHT
            self.density_dpi = int(metrics.densityDpi)

            self.media_projection = self.projection_manager.getMediaProjection(
                result_code, intent
            )

            from jnius import autoclass

            MediaProjectionCallback = autoclass(
                "org.tglass.tglasstransmitter.MediaProjectionCallback"
            )

            self.projection_callback = MediaProjectionCallback()

            Looper = autoclass("android.os.Looper")
            Handler = autoclass("android.os.Handler")

            main_handler = Handler(Looper.getMainLooper())

            self.media_projection.registerCallback(
                self.projection_callback, main_handler
            )

            self.image_reader = ImageReader.newInstance(
                self.capture_width,
                self.capture_height,
                1,
                2
            )
            self.surface = self.image_reader.getSurface()

            self.virtual_display = self.media_projection.createVirtualDisplay(
                "TGlassScreenCapture",
                self.capture_width,
                self.capture_height,
                self.density_dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                self.surface,
                None,
                None,
            )

            self.ready = True
            self._log(
                "[ANDROID] Screen capture ready: "
                f"{self.capture_width}x{self.capture_height}"
            )
            if self.ready_callback:
                self.ready_callback()

        except Exception as exc:
            self.ready = False
            self._log(
                f"[ANDROID] MediaProjection error: "
                f"{type(exc).__name__}: {exc}"
            )
            if self.denied_callback:
                self.denied_callback()
            self.close()

    def grab_frame(self):
        """Return an RGB uint8 frame resized to exactly 126x126."""
        if not self.ready or self.image_reader is None:
            return None

        image = None
        try:
            image = self.image_reader.acquireLatestImage()
            if image is None:
                return None

            planes = image.getPlanes()
            plane = planes[0]
            buffer = plane.getBuffer()
            pixel_stride = int(plane.getPixelStride())
            row_stride = int(plane.getRowStride())

            width = int(image.getWidth())
            height = int(image.getHeight())

            # PyJNIus accepts a Python bytearray for java.nio.ByteBuffer.get().
            buffer.rewind()
            data_size = int(buffer.remaining())
            raw = bytearray(data_size)
            buffer.get(raw)

            arr = np.frombuffer(raw, dtype=np.uint8)
            row_bytes = row_stride
            arr = arr.reshape((height, row_bytes))
            arr = arr[:, : width * pixel_stride]
            arr = arr.reshape((height, width, pixel_stride))

            # ImageReader RGBA_8888 gives R,G,B,A. Drop alpha.
            rgb = np.ascontiguousarray(arr[:, :, :3])

            # Apply the same rotation choices as the desktop backend.
            rotation = int(self.rotate_deg) % 360
            if rotation == 90:
                rgb = np.rot90(rgb, k=3)
            elif rotation == 180:
                rgb = np.rot90(rgb, k=2)
            elif rotation == 270:
                rgb = np.rot90(rgb, k=1)


            return np.ascontiguousarray(rgb, dtype=np.uint8)

        except Exception as exc:
            self._log(
                f"[ANDROID] Frame capture error: "
                f"{type(exc).__name__}: {exc}"
            )
            return None
        finally:
            if image is not None:
                try:
                    image.close()
                except Exception:
                    pass

    def _projection_stopped(self):
        self._log("[ANDROID] Screen capture stopped by Android.")
        self.ready = False

    def close(self):
        self.ready = False

        for obj_name in ("virtual_display", "image_reader"):
            obj = getattr(self, obj_name, None)
            if obj is not None:
                try:
                    obj.close() if obj_name == "image_reader" else obj.release()
                except Exception:
                    pass
                setattr(self, obj_name, None)

        if self.media_projection is not None:
            try:
                self.media_projection.stop()
            except Exception:
                pass
            self.media_projection = None

        self.surface = None

        if self._service_started and self.activity is not None:
            try:
                from jnius import autoclass
                Service = autoclass("org.tglass.tglasstransmitter.ServiceMediaprojection")
                Service.stop(self.activity)
            except Exception:
                pass
            self._service_started = False

        if self._activity_result_bound:
            try:
                from android import activity
                activity.unbind(on_activity_result=self._on_activity_result)
            except Exception:
                pass
            self._activity_result_bound = False

    def stop(self):
        self.closed = True
        self.close()
