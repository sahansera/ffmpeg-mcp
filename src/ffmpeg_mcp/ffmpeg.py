import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import threading

import ffmpeg_mcp.typedef as typedef
import ffmpeg_mcp.utils as utils


DEFAULT_FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_MCP_TIMEOUT", "1200"))
DEFAULT_PROBE_TIMEOUT = int(os.getenv("FFMPEG_MCP_PROBE_TIMEOUT", "60"))


def check_os_architecture():
    return platform.system(), platform.machine()


def _terminate_process(proc):
    """Terminate a process and its process group without leaving FFmpeg behind."""
    if proc is None or proc.poll() is not None:
        return

    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def run_command(command, timeout=DEFAULT_FFMPEG_TIMEOUT):
    """Run a command, capture combined stdout/stderr, and enforce a timeout in seconds."""
    logs = []
    append_msg = ""
    return_code = 0
    proc = None
    thread = None

    def read_output(process):
        try:
            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
                logs.append(line.rstrip("\n"))
        except (ValueError, OSError):
            pass

    try:
        args = command if isinstance(command, (list, tuple)) else shlex.split(
            command, posix=(sys.platform != "win32")
        )
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            start_new_session=(os.name != "nt"),
        )
        thread = threading.Thread(target=read_output, args=(proc,), daemon=True)
        thread.start()
        return_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return_code = -1
        append_msg = f"Timeout expired after {timeout} seconds"
        _terminate_process(proc)
    except Exception as exc:
        return_code = -1
        append_msg = f"An error occurred: {exc}"
        _terminate_process(proc)
    finally:
        _terminate_process(proc)
        if thread is not None:
            thread.join(timeout=5)
        if proc is not None and proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass

    if append_msg:
        logs.append(append_msg)
    return return_code, "\n".join(logs), append_msg


def is_file_and_exists(file_path):
    return os.path.isfile(file_path) and os.path.exists(file_path)


def _bundled_command_dir():
    """Return the bundled macOS FFmpeg directory, downloading it if necessary."""
    system, machine = check_os_architecture()
    if system != "Darwin" or machine not in {"x86_64", "arm64"}:
        return None

    current_work_dir = os.path.dirname(__file__)
    bin_dir = os.path.join(current_work_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)

    bundle_name = f"ffmpeg-macos-{machine}"
    bundle_dir = os.path.join(bin_dir, bundle_name)
    url = f"https://gitee.com/littlecodergitxxx/{bundle_name}.git"

    if not is_file_and_exists(os.path.join(bundle_dir, "ffmpeg")):
        original_cwd = os.getcwd()
        try:
            os.chdir(bin_dir)
            run_command(["rm", "-rf", bundle_dir])
            code, _, _ = run_command(["git", "clone", url])
            if code != 0:
                return None
            os.chdir(bundle_dir)
            code, _, _ = run_command(["git", "checkout", "v0.1"])
            if code != 0:
                return None
            utils.unzip_to_current_directory("ffmpeg.zip")
            for binary in ("ffmpeg", "ffprobe", "ffplay"):
                binary_path = os.path.join(bundle_dir, binary)
                if os.path.exists(binary_path):
                    os.chmod(binary_path, 0o755)
        finally:
            os.chdir(original_cwd)

    return bundle_dir


def _command_path(binary):
    """Resolve FFmpeg tools, preferring explicit/system installs over bundled binaries."""
    env_name = {
        "ffmpeg": "FFMPEG_PATH",
        "ffprobe": "FFPROBE_PATH",
        "ffplay": "FFPLAY_PATH",
    }[binary]

    explicit = os.getenv(env_name)
    if explicit and is_file_and_exists(explicit):
        return explicit

    system_path = shutil.which(binary)
    if system_path:
        return system_path

    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = os.path.join(prefix, binary)
        if is_file_and_exists(candidate):
            return candidate

    bundle_dir = _bundled_command_dir()
    if bundle_dir is None:
        return None

    candidate = os.path.join(bundle_dir, binary)
    return candidate if is_file_and_exists(candidate) else None


def command_dir():
    """Backward-compatible helper returning the directory containing ffmpeg."""
    path = _command_path("ffmpeg")
    return os.path.dirname(path) if path else None


def run_ffmpeg(cmd, timeout=DEFAULT_FFMPEG_TIMEOUT):
    executable = _command_path("ffmpeg")
    if executable is None:
        return -1, "FFmpeg executable not found"

    full_cmd = f"{shlex.quote(executable)} {cmd}"
    code, log, append_msg = run_command(full_cmd, timeout)
    return code, "\n".join(part for part in (full_cmd, log, append_msg) if part)


def run_ffprobe(cmd, timeout=DEFAULT_PROBE_TIMEOUT):
    executable = _command_path("ffprobe")
    if executable is None:
        return -1, "", "ffprobe executable not found"

    full_cmd = f"{shlex.quote(executable)} {cmd}"
    code, log, append_msg = run_command(full_cmd, timeout)
    if code != 0:
        error_log = "\n".join(part for part in (full_cmd, log, append_msg) if part)
        return code, full_cmd, error_log
    return code, full_cmd, log


def run_ffplay(cmd, timeout=DEFAULT_PROBE_TIMEOUT):
    executable = _command_path("ffplay")
    if executable is None:
        return -1, "", "ffplay executable not found"

    full_cmd = f"{shlex.quote(executable)} {cmd}"
    code, log, append_msg = run_command(full_cmd, timeout)
    if code != 0:
        error_log = "\n".join(part for part in (full_cmd, log, append_msg) if part)
        return code, full_cmd, error_log
    return code, full_cmd, log


def media_format_ctx(path):
    cmd = f"-show_streams -of json -v error -i {shlex.quote(path)}"
    code, _, log = run_ffprobe(cmd)
    if code == 0:
        return typedef.FormatContext(log)
    return None
