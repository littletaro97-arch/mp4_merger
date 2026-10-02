"""media.py - FFmpeg/ffprobe 媒体文件分析、合并与验证"""

import json
import os
import subprocess
import sys
from pathlib import Path


def find_ffmpeg():
    """查找 ffmpeg.exe 和 ffprobe.exe

    优先级：
    1. 程序目录下的 ffmpeg/ 子目录
    2. Windows PATH
    """
    program_dir = Path(__file__).parent

    # 1. 检查程序目录下的 ffmpeg 子目录
    ffmpeg_dir = program_dir / "ffmpeg"
    ffmpeg = ffmpeg_dir / "ffmpeg.exe"
    ffprobe = ffmpeg_dir / "ffprobe.exe"
    if ffmpeg.exists() and ffprobe.exists():
        return str(ffmpeg), str(ffprobe)

    # 2. 检查 PATH
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for path_dir in path_dirs:
        try:
            p = Path(path_dir)
            ffmpeg_path = p / "ffmpeg.exe"
            ffprobe_path = p / "ffprobe.exe"
            if ffmpeg_path.exists() and ffprobe_path.exists():
                return str(ffmpeg_path), str(ffprobe_path)
        except OSError:
            continue

    return None, None


def get_ffprobe_info(ffprobe_path, file_path):
    """使用 ffprobe 获取媒体文件信息，返回解析后的 JSON 字典"""
    cmd = [
        str(ffprobe_path),
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(file_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        return None


def analyze_streams(info):
    """分析 ffprobe 输出，返回流摘要和 format 信息

    Returns:
        (streams_dict, format_dict) 或 (None, None)
    """
    if not info or "streams" not in info:
        return None, None

    has_video = False
    has_audio = False
    video_codec = None
    audio_codec = None
    video_index = None
    audio_index = None

    for i, stream in enumerate(info["streams"]):
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            has_video = True
            video_codec = stream.get("codec_name")
            if video_index is None:
                video_index = i
        elif codec_type == "audio":
            has_audio = True
            audio_codec = stream.get("codec_name")
            if audio_index is None:
                audio_index = i

    return {
        "has_video": has_video,
        "has_audio": has_audio,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "video_index": video_index,
        "audio_index": audio_index,
    }, info.get("format", {})


def get_duration(info):
    """从 ffprobe 解析的 format 中获取时长（秒）"""
    if not info:
        return None
    fmt = info.get("format", {})
    if "duration" in fmt:
        try:
            return float(fmt["duration"])
        except (ValueError, TypeError):
            pass
    # 回退：从 streams 中查找
    streams = info.get("streams", [])
    for stream in streams:
        dur = stream.get("duration")
        if dur:
            try:
                return float(dur)
            except (ValueError, TypeError):
                pass
    return None


def get_start_time(info):
    """从 ffprobe 解析的 format 中获取 start_time（秒）"""
    if not info:
        return None
    fmt = info.get("format", {})
    if "start_time" in fmt:
        try:
            return float(fmt["start_time"])
        except (ValueError, TypeError):
            pass
    return None


def check_start_time_difference(ffprobe_path, video_path, audio_path, max_diff=0.5):
    """检查两个文件的 start_time 差异是否在允许范围内"""
    info_v = get_ffprobe_info(ffprobe_path, video_path)
    info_a = get_ffprobe_info(ffprobe_path, audio_path)
    if not info_v or not info_a:
        return True, "无法读取文件 start_time，跳过检查"
    st_v = get_start_time(info_v)
    st_a = get_start_time(info_a)
    if st_v is None or st_a is None:
        return True, "无法获取 start_time，跳过检查"
    diff = abs(st_v - st_a)
    if diff > max_diff:
        return (
            False,
            f"跳过：音视频起始时间存在明显差异（差值 {diff:.3f}s），需要人工检查。",
        )
    return True, f"起始时间差异 {diff:.3f}s，通过"


def merge_audio_video(ffmpeg_path, ffprobe_path, video_path, audio_path, output_path):
    """将视频文件的视频流和音频文件的音频流无损合并

    Returns:
        (success, message_or_tmp_path)
    """
    tmp_output = output_path.with_name(output_path.stem + ".tmp" + output_path.suffix)

    # 注意：先判断 audio 编码是否可以直接封装进 MP4
    info_a = get_ffprobe_info(ffprobe_path, audio_path)
    if info_a:
        streams, _ = analyze_streams(info_a)
        if streams and streams["audio_codec"]:
            # MP4 容器支持的音频编码检查
            supported_codecs = {"aac", "mp3", "mp4a", "alac", "opus", "flac", "ac3", "eac3"}
            if streams["audio_codec"].lower() not in supported_codecs:
                return (
                    False,
                    f"音频编码 {streams['audio_codec']} 无法直接封装进 MP4（stream copy 不支持），"
                    f"跳过合并。",
                )

    cmd = [
        str(ffmpeg_path),
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-y",
        str(tmp_output),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    except subprocess.TimeoutExpired:
        # 清理临时文件
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        return False, "FFmpeg 执行超时（300秒）"
    except OSError as e:
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        return False, f"FFmpeg 执行失败: {e}"

    if result.returncode != 0:
        # 清理临时文件
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        return False, f"FFmpeg 返回错误码 {result.returncode}:\n{result.stderr[:500]}"

    return True, str(tmp_output)


def validate_output(ffprobe_path, output_path, video_duration=None, audio_duration=None):
    """验证合并后的输出文件

    Returns:
        (success, message)
    """
    if not output_path.exists():
        return False, "输出文件不存在"

    file_size = output_path.stat().st_size
    if file_size == 0:
        return False, "输出文件大小为 0"

    info = get_ffprobe_info(ffprobe_path, output_path)
    if not info:
        return False, "无法用 ffprobe 读取输出文件"

    streams, fmt = analyze_streams(info)
    if not streams:
        return False, "无法分析输出文件的媒体流"

    if not streams["has_video"]:
        return False, "输出文件缺少视频流"

    if not streams["has_audio"]:
        return False, "输出文件缺少音频流"

    duration = get_duration(info)
    if duration is None or duration <= 0:
        return False, "输出文件时长无效"

    # 检查时长是否合理（不应该接近两倍时长）
    if video_duration is not None and audio_duration is not None:
        expected = max(video_duration, audio_duration)
        if duration > expected * 1.5:
            return (
                False,
                f"输出时长异常（{duration:.2f}s），疑似错误拼接，"
                f"预期约 {expected:.2f}s",
            )
        if duration < expected * 0.5:
            return (
                False,
                f"输出时长过短（{duration:.2f}s），预期约 {expected:.2f}s",
            )

    return True, f"输出检查：视频流正常（{streams.get('video_codec', '?')}），音频流正常（{streams.get('audio_codec', '?')}），时长 {duration:.2f}s"