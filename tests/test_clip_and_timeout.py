import sys
import time
import unittest
from unittest import mock

from ffmpeg_mcp import cut_video, ffmpeg


class ClipCommandTests(unittest.TestCase):
    def test_fast_clip_uses_input_seek_duration_and_stream_copy(self):
        start_sec, duration_sec = cut_video._clip_times("00:00:02", None, 5)
        command, encoder = cut_video._build_clip_command(
            "/tmp/input video.MOV",
            "/tmp/output.MOV",
            start_sec,
            duration_sec,
            fast=True,
        )

        self.assertTrue(command.startswith("-ss 2 -i"))
        self.assertIn("-t 5", command)
        self.assertIn("-c copy", command)
        self.assertIsNone(encoder)

    def test_end_is_converted_to_duration(self):
        start_sec, duration_sec = cut_video._clip_times(2, 7, None)
        self.assertEqual(start_sec, 2.0)
        self.assertEqual(duration_sec, 5.0)

    def test_invalid_range_is_rejected(self):
        with self.assertRaises(ValueError):
            cut_video._clip_times(7, 2, None)

    def test_exact_hevc_clip_uses_videotoolbox_on_macos(self):
        stream = mock.Mock(codec_name="hevc")
        fmt_ctx = mock.Mock(video_streams=[stream])
        with mock.patch.object(cut_video.platform, "system", return_value="Darwin"), mock.patch.object(
            cut_video.ffmpeg, "media_format_ctx", return_value=fmt_ctx
        ):
            command, encoder = cut_video._build_clip_command(
                "/tmp/input.MOV",
                "/tmp/output.MOV",
                1.0,
                3.0,
                fast=False,
                hardware_acceleration=True,
            )

        self.assertEqual(encoder, "hevc_videotoolbox")
        self.assertIn("-hwaccel videotoolbox", command)
        self.assertIn("-c:v hevc_videotoolbox", command)
        self.assertIn("-tag:v hvc1", command)


class ProcessTimeoutTests(unittest.TestCase):
    def test_timeout_returns_promptly(self):
        started = time.monotonic()
        code, _, message = ffmpeg.run_command(
            [sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.2
        )
        elapsed = time.monotonic() - started

        self.assertEqual(code, -1)
        self.assertIn("Timeout expired", message)
        self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()
