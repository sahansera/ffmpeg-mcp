# server.py
import os
import sys
from typing import List

cur_path = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, cur_path + "/..")

from mcp.server.fastmcp import FastMCP

import ffmpeg_mcp.cut_video as cut_video


mcp = FastMCP("ffmpeg-mcp")


@mcp.tool()
def find_video_path(root_path, video_name):
    """Recursively find a local video by file name, with or without extension."""
    video_exts = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".ts"}
    target_stem, target_ext = os.path.splitext(video_name)
    if target_ext.lower() not in video_exts:
        target_stem = f"{target_stem}{target_ext}"
        target_ext = ""

    for root, _, files in os.walk(root_path):
        for file in files:
            stem, ext = os.path.splitext(file)
            if stem.lower() == target_stem.lower():
                if not target_ext or ext.lower() in video_exts:
                    return os.path.join(root, file)
    return ""


@mcp.tool()
def clip_video(
    video_path,
    start=None,
    end=None,
    duration=None,
    output_path=None,
    time_out=1200,
    fast=True,
    hardware_acceleration=True,
):
    """Trim a video file on the local machine using FFmpeg.

    This tool runs FFmpeg locally; the model is not transcoding the video inside
    its inference sandbox. Large 4K/HEVC files are supported.

    Parameters:
      video_path: Local input video path.
      start: Start time in seconds, MM:SS, or HH:MM:SS. Defaults to the beginning.
      end: End time in the same formats. Use either end or duration.
      duration: Length of output segment. Use either duration or end.
      output_path: Optional output path.
      time_out: FFmpeg process timeout in seconds. Defaults to 1200 (20 minutes).
      fast: When true (recommended), use stream copy (-c copy). This is very fast
        for 4K HEVC but start time may align to a nearby keyframe. Set false when
        frame-accurate cuts are required.
      hardware_acceleration: In exact mode on macOS, attempt VideoToolbox hardware
        acceleration and automatically fall back to software encoding if needed.

    Prefer fast=True for ordinary clipping before assuming a video is too large
    or computationally expensive to process.
    """
    return cut_video.clip_video_ffmpeg(
        video_path,
        start=start,
        end=end,
        duration=duration,
        output_path=output_path,
        time_out=time_out,
        fast=fast,
        hardware_acceleration=hardware_acceleration,
    )


@mcp.tool()
def concat_videos(input_files: List[str], output_path: str = None, fast: bool = True):
    """Concatenate local videos. Prefer fast=True when stream properties match."""
    return cut_video.concat_videos(input_files, output_path, fast)


@mcp.tool()
def get_video_info(video_path: str):
    """Inspect a local video with ffprobe, including codec, dimensions and frame rate."""
    return cut_video.get_video_info(video_path)


@mcp.tool()
def play_video(video_path: str, speed=1, loop=1):
    """Play a local media file with ffplay."""
    return cut_video.video_play(video_path, speed=speed, loop=loop)


@mcp.tool()
def overlay_video(
    background_video,
    overlay_video,
    output_path: str = None,
    position: int = 1,
    dx=0,
    dy=0,
):
    """Overlay one video on another and mix their audio tracks."""
    return cut_video.overlay_video(
        background_video, overlay_video, output_path, position, dx, dy
    )


@mcp.tool()
def scale_video(video_path, width, height=-2, output_path: str = None):
    """Scale a video. Use -2 for one dimension to preserve aspect ratio."""
    return cut_video.scale_video(video_path, width, height, output_path)


@mcp.tool()
def extract_frames_from_video(
    video_path, fps=0, output_folder=None, format=0, total_frames=0
):
    """Extract frames from a local video.

    fps is interpreted as the interval in seconds between extracted frames. Set
    fps=0 to extract every frame. format: 0=PNG, 1=JPG, 2=WEBP.
    """
    return cut_video.extract_frames_from_video(
        video_path, fps, output_folder, format, total_frames
    )


def main():
    print("Server running")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
