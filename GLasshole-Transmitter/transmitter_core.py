#!/usr/bin/env python3
"""Platform-independent T-Glass TCP/protocol/frame-processing core."""
import socket
import struct
import time
from typing import Optional
import numpy as np

DEFAULT_IP = "192.168.4.1"
DEFAULT_PORT = 8090
DISPLAY_WIDTH = 126
DISPLAY_HEIGHT = 126
HEADER_VERSION = 0x02
RUN_HEADER_VERSION = 0x01

class TGlassTransmitterCore:
    def __init__(self, ip=DEFAULT_IP, port=DEFAULT_PORT, target_fps=15.0,
                 threshold=5, full_frame=False, max_updates_per_frame=3000):
        self.ip = ip; self.port = port; self.target_fps = target_fps
        self.threshold = threshold; self.full_frame = full_frame
        self.max_updates_per_frame = max_updates_per_frame
        self.sock: Optional[socket.socket] = None
        self.connected_port: Optional[int] = None
        self.prev_rgb: Optional[np.ndarray] = None
        self.sent_initial_full = False; self.frame_id = 0
        self.stop_requested = False; self.log_callback = None

    def log(self, message):
        print(message, flush=True)
        if self.log_callback is not None:
            try: self.log_callback(message)
            except Exception: pass

    def stop(self): self.stop_requested = True

    def ensure_connection(self):
        if self.sock and self.connected_port == self.port:
            return True
        if self.sock:
            self.log(f"[CONNECT] Port changed to {self.port}; reconnecting")
            self.disconnect()
        return self.connect()

    def connect(self, retries=1):
        for attempt in range(1, retries + 1):
            try:
                if self.sock: self.sock.close()
                self.log(f"[CONNECT] Attempt {attempt}/{retries} to {self.ip}:{self.port}")
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock.settimeout(2); self.sock.connect((self.ip, self.port))
                self.connected_port = self.port
                self.log("[CONNECT] ✓ Connected")
                return True
            except Exception as exc:
                self.log(f"[CONNECT] ✗ {type(exc).__name__}: {exc}")
                if self.sock:
                    self.sock.close()
                self.sock = None
                self.connected_port = None
                if attempt < retries:
                    if self.stop_requested: break
                    time.sleep(2)
        return False

    def disconnect(self):
        if self.sock: self.sock.close()
        self.sock = None; self.connected_port = None; self.log("[CONNECT] Disconnected")

    @staticmethod
    def rgb888_to_rgb565(rgb):
        r = (rgb[:, :, 0] >> 3).astype(np.uint16)
        g = (rgb[:, :, 1] >> 2).astype(np.uint16)
        b = (rgb[:, :, 2] >> 3).astype(np.uint16)
        return (r << 11) | (g << 5) | b

    def prepare_frame(self, rgb):
        if rgb.shape != (DISPLAY_HEIGHT, DISPLAY_WIDTH, 3):
            raise ValueError(f"Expected RGB frame {DISPLAY_WIDTH}x{DISPLAY_HEIGHT}, got {rgb.shape}")
        if rgb.dtype != np.uint8: rgb = rgb.astype(np.uint8, copy=False)
        return self.rgb888_to_rgb565(rgb)

    def build_packets(self, rgb, rgb565=None):
        if rgb565 is None: rgb565 = self.prepare_frame(rgb)
        if self.full_frame or not self.sent_initial_full or self.prev_rgb is None:
            mask = np.ones((DISPLAY_HEIGHT, DISPLAY_WIDTH), dtype=bool)
        else:
            diff = np.abs(rgb.astype(np.int16) - self.prev_rgb.astype(np.int16))
            mask = diff.max(axis=2) > self.threshold
        ys, xs = np.nonzero(mask); colors = rgb565[ys, xs]; count = len(colors)
        if count == 0:
            header = b"PXUP" + bytes([HEADER_VERSION]) + struct.pack("<I", self.frame_id) + struct.pack("<H", 0)
            self.frame_id += 1; return [header]
        run_packets = self._build_run_packets(mask, rgb565)
        pixel_packets = self._build_pixel_packets(xs, ys, colors, count)
        if sum(map(len, run_packets)) < sum(map(len, pixel_packets)):
            self.frame_id += len(run_packets); return run_packets
        self.frame_id += len(pixel_packets); return pixel_packets

    def _build_pixel_packets(self, xs, ys, colors, count):
        packets=[]; max_per=max(1,self.max_updates_per_frame); start=0
        while start < count:
            end=min(start+max_per,count); slice_count=end-start
            header=b"PXUP"+bytes([HEADER_VERSION])+struct.pack("<I",self.frame_id)+struct.pack("<H",slice_count)
            payload=bytearray(header)
            for x,y,color in zip(xs[start:end],ys[start:end],colors[start:end]):
                payload.extend(struct.pack("<BBH",int(x),int(y),int(color)))
            packets.append(bytes(payload)); start=end
        return packets

    def _build_run_packets(self, mask, rgb565):
        packets=[]; max_per=max(1,self.max_updates_per_frame); runs=[]
        for y in range(DISPLAY_HEIGHT):
            row_mask=mask[y]
            if not row_mask.any(): continue
            x=0
            while x < DISPLAY_WIDTH:
                if not row_mask[x]: x+=1; continue
                x0=x; color=int(rgb565[y,x0]); x+=1
                while x<DISPLAY_WIDTH and row_mask[x] and int(rgb565[y,x])==color: x+=1
                runs.append((y,x0,x-x0,color))
        if not runs:
            header=b"PXUP"+bytes([HEADER_VERSION])+struct.pack("<I",self.frame_id)+struct.pack("<H",0)
            return [header]
        start=0
        while start<len(runs):
            end=min(start+max_per,len(runs)); payload=bytearray(b"PXUR"+bytes([RUN_HEADER_VERSION])+struct.pack("<I",self.frame_id)+struct.pack("<H",end-start))
            for y,x0,length,color in runs[start:end]: payload.extend(struct.pack("<BBBH",y,x0,length,color))
            packets.append(bytes(payload)); start=end
        return packets

    def send_frame(self, rgb):
        rgb565=self.prepare_frame(rgb); packets=self.build_packets(rgb,rgb565); self.prev_rgb=rgb.copy()
        if not self.ensure_connection():
            self.log("[SEND] T-Glass not reachable; continuing")
            return False
        for pkt in packets:
            updates=struct.unpack_from("<H",pkt,9)[0]; fid=struct.unpack_from("<I",pkt,5)[0]
            self.log(f"[FRAME] id={fid} updates={updates}")
            try:
                self.sock.sendall(pkt); self.sent_initial_full=True
            except (BrokenPipeError,ConnectionResetError):
                self.log("[SEND] Connection lost; attempting reconnect"); self.disconnect()
                if not self.ensure_connection():
                    self.log("[SEND] Reconnect failed; continuing")
                    return False
                try: self.sock.sendall(pkt); self.sent_initial_full=True
                except Exception as exc:
                    self.log(f"[SEND] Retry failed: {type(exc).__name__}: {exc}")
                    self.disconnect()
                    return False
            except Exception as exc:
                self.log(f"[SEND] Error: {type(exc).__name__}: {exc}"); self.disconnect(); return False
        return True

    def close(self): self.disconnect()
