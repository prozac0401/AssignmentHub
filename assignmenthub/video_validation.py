"""Decode the opening five seconds in a bounded, separate local process.

This checks playback and signal presence, not speech intelligibility or the
quality of the rest of the recording. No uploaded content leaves this server.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys

VALIDATION_VERSION = 1
SAMPLE_SECONDS = 5.0
TIMEOUT_SECONDS = 45
MAX_PIXELS = 33_554_432  # Up to 8K; reject unreasonable decoder allocations.
MAX_PACKETS = 50_000
FORMATS = "mov,matroska,webm,avi,asf,mpeg,mpegts,flv,ogg"


class VideoValidationError(ValueError):
    pass


class VideoValidatorUnavailable(RuntimeError):
    pass


def validate_video(path: Path, require_audio: bool = True) -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), str(path.resolve())],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoValidationError("영상 검사 시간이 초과되었습니다. 첫 5초를 재생해 보고 MP4 등으로 다시 저장해 제출하세요.") from exc
    except OSError as exc:
        raise VideoValidatorUnavailable("영상 검사기를 실행하지 못했습니다. 관리자에게 서버 점검을 요청한 뒤 다시 시도하세요.") from exc
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise VideoValidatorUnavailable("영상 검사기가 정상적으로 응답하지 않았습니다. 관리자 점검 후 다시 시도하세요.") from exc
    if result.returncode or "error" in payload:
        if payload.get("unavailable"):
            raise VideoValidatorUnavailable(payload["error"])
        raise VideoValidationError(payload.get("error", "영상 검사를 완료하지 못했습니다."))
    if payload.get("version") != VALIDATION_VERSION or payload.get("status") != "passed" or not payload.get("video_frames"):
        raise VideoValidatorUnavailable("영상 검사 결과를 확인하지 못했습니다. 관리자 점검 후 다시 시도하세요.")
    if require_audio and not payload["audio_detected"]:
        raise VideoValidationError("첫 5초에서 소리가 확인되지 않았습니다. 이 과제는 소리가 있는 영상이 필요합니다. 녹음 상태와 시작 부분의 무음을 확인하세요.")
    return payload


def inspect_video(path: Path) -> dict:
    if path.stat().st_size == 0:
        raise VideoValidationError("빈 영상 파일은 제출할 수 없습니다. 녹화한 파일을 다시 선택하세요.")
    # Import native decoders only in the worker. A bad file cannot crash the API.
    import av
    import numpy as np

    av.logging.set_level(av.logging.PANIC)

    def deny_external(*args, **kwargs):
        raise VideoValidationError("다른 파일이나 주소를 참조하는 영상은 제출할 수 없습니다.")

    def open_media(source):
        return av.open(source, mode="r", io_open=deny_external, options={
            "format_whitelist": FORMATS, "protocol_whitelist": "none",
            "max_streams": "16", "probesize": "8388608", "analyzeduration": "5000000",
            "enable_drefs": "0", "use_absolute_path": "0",
        })

    def prepare(stream):
        stream.codec_context.thread_count = 2
        stream.codec_context.options = {"err_detect": "explode", "max_pixels": str(MAX_PIXELS)}

    def frames(container, stream):
        prepare(stream)
        for index, packet in enumerate(container.demux(stream)):
            if index >= MAX_PACKETS:
                raise VideoValidationError("영상의 시작 부분을 제한 시간 내에 읽을 수 없습니다. 다시 인코딩해 제출하세요.")
            if packet.is_corrupt:
                raise VideoValidationError("영상 또는 음성 데이터가 손상되었습니다. 원본을 확인하고 다시 저장하세요.")
            for frame in packet.decode():
                if frame.is_corrupt:
                    raise VideoValidationError("영상 또는 음성 프레임이 손상되었습니다. 원본을 확인하고 다시 저장하세요.")
                yield frame

    def stream_duration(stream):
        return float(stream.duration * stream.time_base) if stream.duration is not None and stream.time_base else None

    try:
        # A Python file object and a deny-all secondary opener keep parsing tied
        # to this one uploaded file, including on Windows with Unicode paths.
        with path.open("rb") as source, open_media(source) as container:
            videos = [s for s in container.streams.video if not s.disposition & av.stream.Disposition.attached_pic]
            if not videos:
                raise VideoValidationError("재생 가능한 화면이 없습니다. 음성 파일이나 이미지를 영상 확장자로 바꾸어 제출할 수 없습니다.")
            video = videos[0]
            width, height = video.codec_context.width, video.codec_context.height
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise VideoValidationError("영상 해상도를 읽을 수 없거나 8K 검사 범위를 초과합니다.")
            start = float(video.start_time * video.time_base) if video.start_time is not None else float(container.start_time or 0) / av.time_base
            duration = stream_duration(video)
            if duration is None and container.duration is not None:
                duration = float(container.duration) / av.time_base
            target = min(SAMPLE_SECONDS, duration) if duration and duration > 0 else SAMPLE_SECONDS
            rate = float(video.average_rate or video.guessed_rate or 30)
            frame_step = 1 / rate if rate > 0 else 1 / 30
            video_end, video_frames, dark_frames = 0.0, 0, 0
            for frame in frames(container, video):
                position = float(frame.time) - start if frame.time is not None else video_end
                if position >= target:
                    break
                length = float(frame.duration * frame.time_base) if frame.duration and frame.time_base else frame_step
                if position + length <= 0:
                    continue
                if frame.width <= 0 or frame.height <= 0 or frame.width * frame.height > MAX_PIXELS:
                    raise VideoValidationError("영상 프레임의 해상도가 올바르지 않습니다.")
                thumbnail = frame.to_ndarray(width=96, height=54, format="gray")
                dark_frames += bool(np.mean(thumbnail <= 12) >= 0.99)
                video_frames += 1
                video_end = max(video_end, min(target, position + length))
            if not video_frames:
                raise VideoValidationError("첫 5초의 화면을 읽지 못했습니다. 실제 영상 파일인지 확인하세요.")
            if duration and video_end + max(0.25, frame_step * 2) < target:
                raise VideoValidationError("영상의 시작 부분이 중간에 끊겼습니다. 원본을 확인하고 다시 저장하세요.")
            audio_present = bool(container.streams.audio)

        audio_frames, peak, energy, samples, audio_end = 0, 0.0, 0.0, 0, 0.0
        if audio_present:
            with path.open("rb") as source, open_media(source) as container:
                audio = container.streams.audio[0]
                audio_duration = stream_duration(audio)
                audio_start = float(audio.start_time * audio.time_base) - start if audio.start_time is not None else 0.0
                next_position = audio_start
                for frame in frames(container, audio):
                    if not frame.sample_rate or not frame.samples:
                        raise VideoValidationError("영상의 음성 데이터를 읽을 수 없습니다.")
                    position = float(frame.time) - start if frame.time is not None else next_position
                    next_position = position + frame.samples / frame.sample_rate
                    if position >= video_end:
                        break
                    lo = max(0, math.ceil(-position * frame.sample_rate))
                    hi = min(frame.samples, math.floor((video_end - position) * frame.sample_rate + 1e-6))
                    if hi <= lo:
                        continue
                    raw = frame.to_ndarray()
                    if not frame.format.is_planar:
                        raw = raw.reshape(-1, len(frame.layout.channels)).T
                    data = raw[:, lo:hi].astype(np.float64)
                    if raw.dtype.kind == "u":
                        midpoint = (np.iinfo(raw.dtype).max + 1) / 2
                        data = (data - midpoint) / midpoint
                    elif raw.dtype.kind == "i":
                        data /= float(-np.iinfo(raw.dtype).min)
                    if not np.isfinite(data).all():
                        raise VideoValidationError("영상의 음성 신호가 손상되었습니다.")
                    peak = max(peak, float(np.max(np.abs(data))))
                    energy += float(np.sum(data * data))
                    samples += data.size
                    audio_frames += 1
                    audio_end = max(audio_end, min(video_end, next_position))
                expected_end = min(video_end, audio_start + audio_duration) if audio_duration is not None else None
                if expected_end is not None and audio_start < video_end and audio_end + 0.15 < expected_end:
                    raise VideoValidationError("영상의 시작 부분에서 음성이 중간에 끊겼습니다. 원본을 확인하세요.")
                if not audio_frames and audio_start < video_end:
                    raise VideoValidationError("음성 트랙이 있지만 첫 5초의 음성을 읽지 못했습니다.")

        rms = math.sqrt(energy / samples) if samples else 0.0
        # -60 dBFS catches silence and negligible recording noise. This is a
        # signal-presence check, not a claim that speech is understandable.
        audible = rms >= 0.001
        warnings = []
        if dark_frames == video_frames:
            warnings.append("검사 구간의 화면이 거의 검습니다. 의도한 시작 화면인지 확인하세요.")
        if not audible:
            warnings.append("검사 구간에서 소리가 확인되지 않았습니다. 무음 허용 설정으로 접수했습니다.")
        return {"version": VALIDATION_VERSION, "status": "passed", "sample_seconds": round(video_end, 3),
                "video_frames": video_frames, "width": width, "height": height,
                "audio_present": audio_present, "audio_frames": audio_frames, "audio_detected": audible,
                "audio_peak_dbfs": round(20 * math.log10(peak), 2) if peak else None,
                "audio_rms_dbfs": round(20 * math.log10(rms), 2) if rms else None, "warnings": warnings}
    except av.error.FFmpegError as exc:
        raise VideoValidationError("영상 또는 음성을 정상적으로 재생할 수 없습니다. 파일 손상과 코덱을 확인하고 MP4 등으로 다시 저장하세요.") from exc


def main():
    try:
        payload = inspect_video(Path(sys.argv[1]))
    except (ImportError, OSError):
        payload = {"error": "영상 검사 환경을 사용할 수 없습니다. 관리자에게 배포본과 저장 장치 점검을 요청하세요.", "unavailable": True}
    except VideoValidationError as exc:
        payload = {"error": str(exc)}
    except Exception:
        payload = {"error": "영상의 시작 부분을 해석하지 못했습니다. 원본을 확인하고 다시 저장하세요."}
    print(json.dumps(payload, ensure_ascii=True, allow_nan=False))
    return 1 if "error" in payload else 0


if __name__ == "__main__":
    raise SystemExit(main())
