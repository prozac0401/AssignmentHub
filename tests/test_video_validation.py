import subprocess

import av
import pytest

from assignmenthub.video_validation import VideoValidationError, VideoValidatorUnavailable, validate_video
from media_samples import make_video


@pytest.fixture(scope="module")
def recordings(tmp_path_factory):
    root = tmp_path_factory.mktemp("video-samples")
    return {
        "long": make_video(root / "normal.mp4", seconds=6),
        "short": make_video(root / "짧은 영상.mp4", seconds=0.4),
        "silent": make_video(root / "silent.mp4", audio="silent"),
        "no_audio": make_video(root / "no-audio.mp4", audio="none"),
        "audio_only": make_video(root / "audio-only.mp4", video=False),
        "late": make_video(root / "late.mp4", seconds=6, audio="late"),
        "dark": make_video(root / "dark.mp4", dark=True),
        "mkv": make_video(root / "normal.mkv", format="matroska"),
        "webm": make_video(root / "normal.webm", format="webm"),
    }


def test_actual_subprocess_decodes_only_first_five_seconds(recordings):
    result = validate_video(recordings["long"])
    assert result["status"] == "passed"
    assert result["sample_seconds"] == pytest.approx(5, abs=0.02)
    assert result["video_frames"] == 50
    assert result["audio_frames"] > 0 and result["audio_detected"]
    assert result["width"] == 96 and result["height"] == 54
    assert result["warnings"] == []


@pytest.mark.parametrize("name", ["short", "mkv", "webm"])
def test_short_video_and_other_containers(recordings, name):
    result = validate_video(recordings[name])
    assert result["sample_seconds"] == pytest.approx(0.4 if name == "short" else 1, abs=0.02)
    assert result["audio_detected"]


@pytest.mark.parametrize("name", ["silent", "no_audio", "late"])
def test_required_sound_and_explicit_silent_video_permission(recordings, name):
    with pytest.raises(VideoValidationError, match="소리"):
        validate_video(recordings[name])
    result = validate_video(recordings[name], require_audio=False)
    assert not result["audio_detected"]
    assert result["warnings"]


def test_audio_only_is_not_a_video(recordings):
    with pytest.raises(VideoValidationError, match="화면"):
        validate_video(recordings["audio_only"], require_audio=False)


def test_dark_but_decodable_intro_is_reported(recordings):
    result = validate_video(recordings["dark"])
    assert result["audio_detected"]
    assert any("검습니다" in warning for warning in result["warnings"])


@pytest.mark.parametrize("content", [b"", b"not a video", b"ffconcat version 1.0\nfile secret.txt\n", b"#EXTM3U\nhttp://127.0.0.1:9/test.ts\n"])
def test_invalid_and_external_reference_files_are_rejected(tmp_path, content):
    path = tmp_path / "fake.mp4"
    path.write_bytes(content)
    with pytest.raises(VideoValidationError):
        validate_video(path)


def test_truncated_container_is_rejected(tmp_path, recordings):
    path = tmp_path / "truncated.mp4"
    path.write_bytes(recordings["long"].read_bytes()[:1500])
    with pytest.raises(VideoValidationError):
        validate_video(path)


@pytest.mark.parametrize("stream_type", ["video", "audio"])
def test_damaged_media_packets_with_intact_container_are_rejected(tmp_path, recordings, stream_type):
    source = recordings["long"]
    with av.open(str(source)) as container:
        stream = getattr(container.streams, stream_type)[0]
        packet = next(p for p in container.demux(stream) if p.size > 0 and p.pos is not None)
        position, length = packet.pos, packet.size
    data = bytearray(source.read_bytes())
    data[position:position + length] = b"\xff" * length
    path = tmp_path / "damaged.mp4"
    path.write_bytes(data)
    with pytest.raises(VideoValidationError):
        validate_video(path)


def test_worker_timeout_is_not_success(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 45
        assert "shell" not in kwargs
        raise subprocess.TimeoutExpired(args[0], 45)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(VideoValidationError, match="시간"):
        validate_video(tmp_path / "x.mp4")


def test_missing_decoder_is_not_success(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, '{"error":"decoder unavailable","unavailable":true}', ""))
    with pytest.raises(VideoValidatorUnavailable):
        validate_video(tmp_path / "x.mp4")
