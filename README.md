# FFmpeg-MCP

An MCP server that uses the local FFmpeg command line for video search, inspection, clipping, concatenation, playback, overlays, scaling, and frame extraction.

This repository is a fork of [`video-creator/ffmpeg-mcp`](https://github.com/video-creator/ffmpeg-mcp) with additional work focused on reliable use from **LM Studio**, especially for long-running and 4K/HEVC operations.

## LM Studio improvements in this fork

- FFmpeg subprocess timeouts are configurable and timed-out processes are terminated cleanly.
- The server prefers an explicitly configured or system-installed FFmpeg before falling back to the bundled build.
- Ordinary clipping defaults to FFmpeg stream copy (`-c copy`) so 4K/HEVC clips do not need to be decoded and re-encoded.
- Seeking is performed before the input where appropriate.
- Frame-accurate clipping can use macOS VideoToolbox hardware acceleration, with a software fallback.
- Frame extraction attempts VideoToolbox decoding on macOS and falls back automatically.
- Tool descriptions explicitly tell local models that FFmpeg runs on the host machine rather than inside the model inference sandbox.

## Supported tools

- `find_video_path` — recursively find a local video by name.
- `get_video_info` — inspect duration, FPS, codec, width, height, and stream information with ffprobe.
- `clip_video` — trim a video using either fast stream copy or frame-accurate transcoding.
- `concat_videos` — concatenate compatible files quickly or normalize them when required.
- `play_video` — play local media with ffplay.
- `overlay_video` — overlay one video on another.
- `scale_video` — resize a video.
- `extract_frames_from_video` — extract selected or all frames.

## Installation

```bash
git clone https://github.com/sahansera/ffmpeg-mcp.git
cd ffmpeg-mcp
uv sync
```

FFmpeg-MCP will prefer FFmpeg available from your `PATH`. On Apple Silicon it also checks `/opt/homebrew/bin`; on Intel macOS it checks `/usr/local/bin`. If no system installation can be found, the upstream bundled macOS FFmpeg fallback is retained.

You can explicitly select binaries with environment variables:

```bash
export FFMPEG_PATH=/opt/homebrew/bin/ffmpeg
export FFPROBE_PATH=/opt/homebrew/bin/ffprobe
export FFPLAY_PATH=/opt/homebrew/bin/ffplay
```

The server-side default FFmpeg timeout can also be changed:

```bash
export FFMPEG_MCP_TIMEOUT=1200
```

The value is in **seconds**.

## LM Studio configuration

LM Studio's `mcp.json` `timeout` value is in **milliseconds**. A 20-minute tool-call timeout is therefore `1200000`.

```json
{
  "mcpServers": {
    "ffmpeg-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/YOUR_USER/Projects/ffmpeg-mcp",
        "run",
        "ffmpeg-mcp"
      ],
      "timeout": 1200000
    }
  }
}
```

Replace the directory with the actual local path to this repository. After changing the configuration, save `mcp.json` and restart the MCP server in LM Studio.

### Clipping behavior

`clip_video` now exposes these additional controls:

- `fast=true` (default): uses `-c copy`. This is recommended for normal clipping and is especially useful for 4K HEVC files. Because stream copy does not re-encode, a start position can be limited by source keyframe placement.
- `fast=false`: performs a frame-accurate transcode. On macOS, `hardware_acceleration=true` attempts VideoToolbox first and falls back to software if required.
- `time_out=1200`: FFmpeg process timeout in seconds. This is separate from LM Studio's MCP timeout.

For example, a model should normally call a simple five-second clip with `fast=true` rather than assuming a 4K/HEVC source is too expensive to process.

## Cline configuration

The upstream project was originally documented for Cline. A typical Cline configuration remains:

```json
{
  "mcpServers": {
    "ffmpeg-mcp": {
      "autoApprove": [],
      "disabled": false,
      "timeout": 60,
      "command": "uv",
      "args": [
        "--directory",
        "/Users/YOUR_USER/Projects/ffmpeg-mcp",
        "run",
        "ffmpeg-mcp"
      ],
      "transportType": "stdio"
    }
  }
}
```

Client timeout units and semantics are client-specific; the LM Studio example above intentionally uses milliseconds.

## Development

Run the tests with:

```bash
uv run python -m unittest discover -s tests -v
```

## Supported platforms

The upstream bundled-binary fallback currently supports macOS ARM64 and x86_64. System FFmpeg discovery makes the execution layer less dependent on the bundled binary, but this fork is currently tested primarily with macOS in mind.

## License and attribution

MIT licensed. This fork retains the original license and copyright notice from `video-creator/ffmpeg-mcp`.
