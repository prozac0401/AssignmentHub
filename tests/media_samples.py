"""Small deterministic local recordings for actual decoder tests (no downloads)."""
from fractions import Fraction

import av
import numpy as np


def make_video(path, seconds=1.0, audio="tone", video=True, dark=False, format="mp4"):
    with av.open(str(path), "w", format=format) as output:
        packets = []
        if video:
            stream = output.add_stream("libvpx-vp9" if format == "webm" else "mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 96, 54, "yuv420p"
            for index in range(round(seconds * 10)):
                pixels = np.zeros((54, 96, 3), dtype=np.uint8)
                if not dark:
                    pixels[:, :, 0] = 100
                    pixels[:, index % 90:index % 90 + 6, 1] = 255
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                frame.pts, frame.time_base = index, Fraction(1, 10)
                packets.extend(stream.encode(frame))
            packets.extend(stream.encode(None))
        if audio != "none":
            stream = output.add_stream("libopus" if format == "webm" else "aac", rate=48000)
            stream.layout = "mono"
            total = round(seconds * 48000)
            for offset in range(0, total, 1024):
                times = np.arange(offset, min(total, offset + 1024)) / 48000
                values = 0.25 * np.sin(2 * np.pi * 440 * times)
                if audio == "silent":
                    values[:] = 0
                if audio == "late":
                    values[times < 5.5] = 0
                frame = av.AudioFrame.from_ndarray(values.astype(np.float32)[None, :], format="fltp", layout="mono")
                frame.sample_rate, frame.pts, frame.time_base = 48000, offset, Fraction(1, 48000)
                packets.extend(stream.encode(frame))
            packets.extend(stream.encode(None))
        packets.sort(key=lambda packet: float(packet.dts * packet.time_base))
        for packet in packets:
            output.mux(packet)
    return path
