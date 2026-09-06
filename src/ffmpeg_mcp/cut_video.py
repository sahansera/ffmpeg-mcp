import os
import platform
import shlex
from enum import Enum
from typing import List

import ffmpeg_mcp.ffmpeg as ffmpeg
import ffmpeg_mcp.utils as utils


DEFAULT_VIDEO_TIMEOUT = 1200


def _format_seconds(value):
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _clip_times(start=None, end=None, duration=None):
    start_sec = 0.0 if start is None else utils.convert_to_seconds(start)
    if start_sec < 0:
        raise ValueError("start must be zero or greater")

    duration_sec = None
    if duration is not None:
        duration_sec = utils.convert_to_seconds(duration)
    elif end is not None:
        end_sec = utils.convert_to_seconds(end)
        duration_sec = end_sec - start_sec

    if duration_sec is not None and duration_sec <= 0:
        raise ValueError("end must be after start and duration must be greater than zero")

    return start_sec, duration_sec


def _videotoolbox_encoder(video_path):
    """Choose a macOS hardware encoder that preserves common source codecs."""
    if platform.system() != "Darwin":
        return None

    fmt_ctx = ffmpeg.media_format_ctx(video_path)
    if fmt_ctx is None or not fmt_ctx.video_streams:
        return None

    codec = (fmt_ctx.video_streams[0].codec_name or "").lower()
    if codec in {"hevc", "h265"}:
        return "hevc_videotoolbox"
    if codec in {"h264", "avc"}:
        return "h264_videotoolbox"
    return "h264_videotoolbox"


def _build_clip_command(
    video_path,
    output_path,
    start_sec,
    duration_sec,
    fast=True,
    hardware_acceleration=True,
):
    parts = []

    # Input-side seek avoids decoding everything before the requested segment.
    if start_sec > 0:
        parts.extend(["-ss", _format_seconds(start_sec)])

    encoder = None
    if not fast and hardware_acceleration:
        encoder = _videotoolbox_encoder(video_path)
        if encoder:
            parts.extend(["-hwaccel", "videotoolbox"])

    parts.extend(["-i", shlex.quote(video_path)])

    if duration_sec is not None:
        parts.extend(["-t", _format_seconds(duration_sec)])

    if fast:
        # Stream copy is ideal for ordinary trims: no 4K HEVC decode/re-encode.
        # The trade-off is that the exact start can be limited by keyframe placement.
        parts.extend(["-c", "copy"])
    elif encoder:
        parts.extend(["-c:v", encoder, "-c:a", "copy"])
        if encoder == "hevc_videotoolbox":
            parts.extend(["-tag:v", "hvc1"])

    parts.extend(["-y", shlex.quote(output_path)])
    return " ".join(parts), encoder


def clip_video_ffmpeg(
    video_path,
    start=None,
    end=None,
    duration=None,
    output_path=None,
    time_out=DEFAULT_VIDEO_TIMEOUT,
    fast=True,
    hardware_acceleration=True,
):
    """Trim a local video with a fast stream-copy path and an exact re-encode path.

    fast=True performs a stream copy and is recommended for most trims, including
    large 4K HEVC files. It is very fast but the start point may align to a nearby
    keyframe. Set fast=False for frame-accurate transcoding. On macOS, exact mode
    attempts VideoToolbox hardware acceleration and falls back to software if the
    bundled/system FFmpeg build does not support it.
    """
    try:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Input video does not exist: {video_path}")

        base, ext = os.path.splitext(video_path)
        if output_path is None:
            output_path = f"{base}_clip{ext or '.mp4'}"

        start_sec, duration_sec = _clip_times(start, end, duration)
        cmd, encoder = _build_clip_command(
            video_path,
            output_path,
            start_sec,
            duration_sec,
            fast=fast,
            hardware_acceleration=hardware_acceleration,
        )
        print(cmd)
        status_code, log = ffmpeg.run_ffmpeg(cmd, timeout=time_out)

        # Some bundled FFmpeg builds may not include VideoToolbox encoders.
        # Retry exact mode in software instead of reporting a misleading timeout.
        if status_code != 0 and not fast and encoder:
            fallback_cmd, _ = _build_clip_command(
                video_path,
                output_path,
                start_sec,
                duration_sec,
                fast=False,
                hardware_acceleration=False,
            )
            print(f"VideoToolbox failed; retrying with software encoding: {fallback_cmd}")
            fallback_status, fallback_log = ffmpeg.run_ffmpeg(
                fallback_cmd, timeout=time_out
            )
            log = f"{log}\nVideoToolbox fallback:\n{fallback_log}"
            status_code = fallback_status

        print(log)
        return status_code, log, output_path
    except Exception as exc:
        print(f"Clip failed: {exc}")
        return -1, str(exc), ""


def concat_videos(input_files: List[str], output_path: str = None, fast: bool = True):
    """Concatenate videos, using stream copy when inputs are compatible."""
    if output_path is None:
        base, _ = os.path.splitext(input_files[0])
        output_path = f"{base}_clip.mp4"

    for file in input_files:
        if not os.path.exists(file):
            raise FileNotFoundError(f"Input file does not exist: {file}")

    if fast:
        temp_list_file = utils.create_temp_file()
        try:
            with open(temp_list_file, "w", encoding="utf-8") as handle:
                for file in input_files:
                    abs_path = os.path.abspath(file)
                    escaped = abs_path.replace("'", "'\\''")
                    handle.write(f"file '{escaped}'\n")

            cmd = (
                f"-f concat -safe 0 -i {shlex.quote(temp_list_file)} "
                f"-c copy -y {shlex.quote(output_path)}"
            )
            return ffmpeg.run_ffmpeg(cmd)
        finally:
            if os.path.exists(temp_list_file):
                os.remove(temp_list_file)

    inputs = []
    filter_str = ""
    fmt_ctx = ffmpeg.media_format_ctx(input_files[0])
    if fmt_ctx is None:
        return -1, f"Failed to inspect {input_files[0]}"

    map_args = ""
    if len(fmt_ctx.video_streams) > 0:
        width = fmt_ctx.video_streams[0].width
        height = fmt_ctx.video_streams[0].height
        aspect = float(width) / float(height)

        for i, file in enumerate(input_files):
            inputs.extend(["-i", shlex.quote(file)])
            if i == 0:
                filter_str += f"[{i}:v]setsar=1[{i}v];"
                continue

            tmp_fmt_ctx = ffmpeg.media_format_ctx(file)
            if tmp_fmt_ctx is None:
                return -1, f"Failed to inspect {input_files[i]}"
            if len(tmp_fmt_ctx.video_streams) == 0:
                return -1, f"{input_files[i]} does not contain a video stream"

            tmp_width = tmp_fmt_ctx.video_streams[0].width
            tmp_height = tmp_fmt_ctx.video_streams[0].height
            tmp_aspect = float(tmp_width) / float(tmp_height)

            if tmp_width == width and tmp_height == height:
                filter_str += f"[{i}:v]setsar=1[{i}v];"
            elif tmp_aspect == aspect:
                filter_str += f"[{i}:v]scale={width}:{height},setsar=1[{i}v];"
            elif abs(tmp_aspect - aspect) < 0.15:
                filter_str += (
                    f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"setsar=1,crop=x=(iw-{width})/2:y=({height}-ih)/2:"
                    f"w={width}:h={height},setsar=1[{i}v];"
                )
            else:
                filter_str += (
                    f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"setsar=1,pad={width}:{height}:({width}-iw)/2:"
                    f"({height}-ih)/2,setsar=1[{i}v];"
                )

        for i, _ in enumerate(input_files):
            if len(fmt_ctx.audio_streams) > 0:
                filter_str += f"[{i}v][{i}:a]"
            else:
                filter_str += f"[{i}v]"

        audio_count = 0
        map_args = "-map '[outv]'"
        out = "[outv]"
        if len(fmt_ctx.audio_streams) > 0:
            audio_count = 1
            map_args = "-map '[outv]' -map '[outa]'"
            out = "[outv][outa]"
        filter_str += f"concat=n={len(input_files)}:v=1:a={audio_count}{out}"

    elif len(fmt_ctx.audio_streams) > 0:
        for i, file in enumerate(input_files):
            inputs.extend(["-i", shlex.quote(file)])
        filter_str += f"concat=n={len(input_files)}:a=1:v=0[outa]"
        map_args = "-map '[outa]'"

    if not filter_str:
        return -1, f"{input_files[0]} contains no audio or video streams"

    inputs_str = " ".join(inputs)
    cmd = (
        f"{inputs_str} -lavfi '{filter_str}' {map_args} "
        f"-y {shlex.quote(output_path)}"
    )
    return ffmpeg.run_ffmpeg(cmd)


def get_video_info(video_path: str):
    cmd = f"-v error -show_streams -of json -i {shlex.quote(video_path)}"
    return ffmpeg.run_ffprobe(cmd, timeout=60)


def video_play(video_path: str, speed, loop):
    speed = float(speed)
    loop = int(loop)
    cmd = f"-loop {loop} "
    if loop != 0:
        cmd += "-autoexit "

    audio_filter_str = ""
    video_filter_str = ""
    if speed != 1:
        fmt_ctx = ffmpeg.media_format_ctx(video_path)
        if fmt_ctx and len(fmt_ctx.audio_streams) > 0:
            audio_filter_str = f"-af atempo={speed}"
        if fmt_ctx and len(fmt_ctx.video_streams) > 0:
            video_filter_str = f"-vf setpts={1 / speed}*PTS"

    cmd = f"{cmd} {audio_filter_str} {video_filter_str} -i {shlex.quote(video_path)}"
    print(cmd)
    return ffmpeg.run_ffplay(cmd, timeout=60)


class Position(Enum):
    TopLeft = 1
    TopCenter = 2
    TopRight = 3
    RightCenter = 4
    BottomRight = 5
    BottomCenter = 6
    BottomLeft = 7
    LeftCenter = 8
    Center = 9


def overlay_video(
    background_video,
    overlay_video,
    output_path: str = None,
    position: int = 1,
    dx=0,
    dy=0,
):
    try:
        base, ext = os.path.splitext(background_video)
        if output_path is None:
            output_path = f"{base}_clip{ext or '.mp4'}"

        x = ""
        y = ""
        if position == 1:
            x, y = f"{dx}", f"{dy}"
        elif position in {Position.LeftCenter, 8}:
            x, y = f"{dx}", f"(H-h)/2+{dy}"
        elif position == 7:
            x, y = f"{dx}", f"(H-h)+{dy}"
        elif position == 6:
            x, y = f"(W-w)/2+{dx}", f"(H-h)+{dy}"
        elif position == 5:
            x, y = f"(W-w)+{dx}", f"(H-h)+{dy}"
        elif position == 4:
            x, y = f"(W-w)+{dx}", f"(H-h)/2+{dy}"
        elif position == 3:
            x, y = f"(W-w)+{dx}", f"{dy}"
        elif position == 2:
            x, y = f"(W-w)/2+{dx}", f"{dy}"
        elif position == 9:
            x, y = f"(W-w)/2+{dx}", f"(H-h)/2+{dy}"
        else:
            raise ValueError(f"Unsupported overlay position: {position}")

        cmd = (
            f"-i {shlex.quote(background_video)} -i {shlex.quote(overlay_video)} "
            f"-filter_complex \"[0:v][1:v]overlay=x={x}:y={y}[ov];"
            f"[0:a][1:a]amix=inputs=2:weights='3 1'[oa]\" "
            f"-map '[ov]' -map '[oa]' -y {shlex.quote(output_path)}"
        )
        print(cmd)
        status_code, log = ffmpeg.run_ffmpeg(cmd, timeout=1000)
        print(log)
        return status_code, log, output_path
    except Exception as exc:
        print(f"Overlay failed: {exc}")
        return -1, str(exc), ""


def scale_video(video_path, width, height=-2, output_path: str = None):
    try:
        base, ext = os.path.splitext(video_path)
        if output_path is None:
            output_path = f"{base}_clip{ext or '.mp4'}"

        cmd = (
            f"-i {shlex.quote(video_path)} -filter_complex \"scale={width}:{height}\" "
            f"-y {shlex.quote(output_path)}"
        )
        print(cmd)
        status_code, log = ffmpeg.run_ffmpeg(cmd, timeout=1000)
        print(log)
        return status_code, log, output_path
    except Exception as exc:
        print(f"Scale failed: {exc}")
        return -1, str(exc), ""


def extract_frames_from_video(
    video_path, fps=0, output_folder=None, format=0, total_frames=0
):
    if output_folder is None:
        output_folder = os.path.dirname(video_path)
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    if format == 0:
        img_ext = "png"
    elif format == 1:
        img_ext = "jpg"
    else:
        img_ext = "webp"

    output_path = os.path.join(output_folder, f"frame_%04d.{img_ext}")
    try:
        hwaccel = "-hwaccel videotoolbox " if platform.system() == "Darwin" else ""
        cmd = f"{hwaccel}-i {shlex.quote(video_path)}"
        if fps > 0:
            cmd += f" -vf 'fps=1/{fps}'"
        else:
            cmd += " -vsync 0"
        if total_frames > 0:
            cmd += f" -vframes {total_frames}"
        cmd += f" -y {shlex.quote(output_path)}"

        status_code, log = ffmpeg.run_ffmpeg(cmd, timeout=1000)
        if status_code != 0 and hwaccel:
            fallback_cmd = cmd.replace("-hwaccel videotoolbox ", "", 1)
            fallback_status, fallback_log = ffmpeg.run_ffmpeg(
                fallback_cmd, timeout=1000
            )
            log = f"{log}\nVideoToolbox fallback:\n{fallback_log}"
            status_code = fallback_status

        print(log)
        return status_code, log, output_path
    except Exception as exc:
        print(f"Frame extraction failed: {exc}")
        return -1, str(exc), ""
