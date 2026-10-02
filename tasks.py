"""自动配对任务服务：不依赖 GUI，未来功能可实现相同的 run 接口。"""
from pathlib import Path
from scanner import (
    scan_directory,
    find_file_pairs,
    check_file_stability,
    are_files_stable,
    files_still_exist_and_readable,
)
from media import (
    find_ffmpeg,
    get_ffprobe_info,
    analyze_streams,
    get_duration,
    check_start_time_difference,
    merge_audio_video,
    validate_output,
)


SCAN_INTERVAL = 10

class PairMergeTask:
    def __init__(self, state, logger, ffmpeg_path, ffprobe_path, stop_event):
        self.state = state
        self.log_handler = logger
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.stop_event = stop_event
        self._last_skipped = set()

    def run(self, directory):
        self._scan_loop(directory)

    def _scan_loop(self, monitor_dir):
        """后台线程：每 10 秒扫描一次目录"""
        while not self.stop_event.is_set():
            try:
                self._run_one_scan(monitor_dir)
            except Exception as e:
                self.log_handler.log(f"扫描过程异常: {e}")
            # 等待 10 秒（可被 stop_event 中断）
            if self.stop_event.wait(SCAN_INTERVAL):
                return

    def _run_one_scan(self, monitor_dir):
        """执行一次扫描流程"""
        # 1. 扫描所有 MP4 文件
        mp4_files = scan_directory(monitor_dir)
        if not mp4_files:
            return

        # 2. 查找有效配对
        pairs, skipped = find_file_pairs(mp4_files)

        # 3. 记录跳过信息
        notices = set(skipped)
        for root_name, reason in notices - self._last_skipped:
            self.log_handler.log(reason)
        self._last_skipped = notices

        # 4. 处理每个配对
        for base_path, extra_path, root_name in pairs:
            if self.stop_event.is_set():
                return
            self._process_pair(base_path, extra_path, root_name)

    def _process_pair(self, base_path, extra_path, root_name):
        """处理一个文件配对"""
        try:
            self._do_process_pair(base_path, extra_path, root_name)
        except Exception as e:
            self.log_handler.log(f"处理 {root_name} 时发生异常: {e}")
            import traceback
            self.log_handler.log(traceback.format_exc()[-300:])

    def _do_process_pair(self, base_path, extra_path, root_name):
        """实际处理逻辑（可被 _process_pair 包装异常捕获）"""
        base_key = str(base_path)
        log = self.log_handler.log

        # 生成输出文件路径
        output_name = f"{root_name}_merged.mp4"
        output_path = base_path.parent / output_name

        # ── 检查是否已处理 ──────────────────────────────────
        processed = self.state.get_processed(base_path)
        if processed:
            # 检查源文件是否发生变化
            current = check_file_stability(base_path, extra_path)
            if current is not None:
                if (current["base_size"] == processed["base_size"] and
                    current["base_mtime"] == processed["base_mtime"] and
                    current["extra_size"] == processed["extra_size"] and
                    current["extra_mtime"] == processed["extra_mtime"]):
                    # 源文件未变化，跳过
                    return

            # 源文件发生了变化，但合并文件已存在
            if output_path.exists():
                log(f"检测到源文件发生变化，但目标文件 {output_name} 已经存在，请人工处理。")
                # 更新状态记录
                current = check_file_stability(base_path, extra_path)
                if current:
                    self.state.mark_processed(
                        base_path, extra_path, output_path,
                        current["base_size"], current["base_mtime"],
                        current["extra_size"], current["extra_mtime"],
                    )
                return

        # ── 检查是否已跳过 ──────────────────────────────────
        if self.state.is_skipped(base_path):
            return

        # ── 检查是否是已处理的跳过（如 (2) 文件存在） ────────
        # 这个已经在 find_file_pairs 中处理了，但以防万一

        # ── 文件稳定性检测 ──────────────────────────────────
        pending = self.state.get_pending(base_path)
        current_state = check_file_stability(base_path, extra_path)

        if current_state is None:
            # 文件无法读取，可能正在写入
            return

        if pending is None:
            # 第一次发现：记录待处理状态
            log(f"发现候选组合：{root_name}")
            log(f"等待文件写入完成...")
            self.state.set_pending(
                base_path, extra_path,
                current_state["base_size"], current_state["base_mtime"],
                current_state["extra_size"], current_state["extra_mtime"],
            )
            return

        # 已有待处理记录：检查是否稳定
        if not are_files_stable(pending, current_state):
            # 文件仍在变化，更新待处理状态
            self.state.set_pending(
                base_path, extra_path,
                current_state["base_size"], current_state["base_mtime"],
                current_state["extra_size"], current_state["extra_mtime"],
            )
            return

        # 文件稳定了！继续处理
        self.state.remove_pending(base_path)
        log(f"文件状态稳定")

        # ── 确认文件可读 ────────────────────────────────────
        ok, err_msg = files_still_exist_and_readable(base_path, extra_path)
        if not ok:
            log(f"跳过：{err_msg}")
            return

        # ── 分析媒体流 ──────────────────────────────────────
        log(f"正在分析媒体流...")

        info_a = get_ffprobe_info(self.ffprobe_path, base_path)
        info_b = get_ffprobe_info(self.ffprobe_path, extra_path)

        if info_a is None or info_b is None:
            log(f"跳过：无法用 ffprobe 读取文件，可能仍在写入")
            return

        streams_a, fmt_a = analyze_streams(info_a)
        streams_b, fmt_b = analyze_streams(info_b)

        if streams_a is None or streams_b is None:
            log(f"跳过：无法分析媒体流信息")
            return

        # ── 判断哪个是纯视频、哪个是纯音频 ──────────────────
        # 文件 A: base_path
        # 文件 B: extra_path (1)
        a_is_video = streams_a["has_video"] and not streams_a["has_audio"]
        a_is_audio = streams_a["has_audio"] and not streams_a["has_video"]
        b_is_video = streams_b["has_video"] and not streams_b["has_audio"]
        b_is_audio = streams_b["has_audio"] and not streams_b["has_video"]

        if a_is_video and b_is_audio:
            video_path = base_path
            audio_path = extra_path
            video_codec = streams_a.get("video_codec", "?")
            audio_codec = streams_b.get("audio_codec", "?")
            log(f"原文件：纯视频，{video_codec}")
            log(f"(1)文件：纯音频，{audio_codec}")
        elif a_is_audio and b_is_video:
            video_path = extra_path
            audio_path = base_path
            video_codec = streams_b.get("video_codec", "?")
            audio_codec = streams_a.get("audio_codec", "?")
            log(f"原文件：纯音频，{audio_codec}")
            log(f"(1)文件：纯视频，{video_codec}")
        else:
            # 不符合纯视频 + 纯音频的组合
            reasons = []
            if streams_a["has_video"] and streams_b["has_video"]:
                reasons.append("两个 MP4 均包含视频流")
            if streams_a["has_audio"] and streams_b["has_audio"]:
                reasons.append("两个 MP4 均包含音频流")
            if streams_a["has_video"] and streams_a["has_audio"]:
                reasons.append("原文件已同时包含视频和音频")
            if streams_b["has_video"] and streams_b["has_audio"]:
                reasons.append("(1)文件已同时包含视频和音频")
            if not streams_a["has_video"] and not streams_a["has_audio"]:
                reasons.append("原文件无法识别媒体流")
            if not streams_b["has_video"] and not streams_b["has_audio"]:
                reasons.append("(1)文件无法识别媒体流")
            reason = "；".join(reasons) if reasons else "未检测到「一份纯视频 + 一份纯音频」的有效组合"
            log(f"跳过：{reason}")
            self.state.mark_skipped(base_path, reason)
            return

        # ── 时长检查 ────────────────────────────────────────
        video_duration = get_duration(info_a if a_is_video else info_b)
        audio_duration = get_duration(info_b if b_is_audio else info_a)

        if video_duration is None or audio_duration is None:
            log(f"跳过：无法获取媒体时长，可能仍在写入")
            return

        log(f"视频时长：{video_duration:.2f}s")
        log(f"音频时长：{audio_duration:.2f}s")

        duration_diff = abs(video_duration - audio_duration)
        if duration_diff > 0.5:
            log(
                f"跳过：音视频时长不一致。视频 {video_duration:.2f}s，音频 {audio_duration:.2f}s，"
                f"差值 {duration_diff:.2f}s"
            )
            self.state.mark_skipped(
                base_path,
                f"时长不一致：视频 {video_duration:.2f}s，音频 {audio_duration:.2f}s，差值 {duration_diff:.2f}s",
            )
            return

        log(f"时长差：{duration_diff:.2f}s，通过")

        # ── start_time 检查 ─────────────────────────────────
        ok, msg = check_start_time_difference(
            self.ffprobe_path, video_path, audio_path
        )
        log(msg)
        if not ok:
            self.state.mark_skipped(base_path, msg)
            return

        # ── 检查输出文件是否已存在 ──────────────────────────
        if output_path.exists():
            log(f"跳过：目标文件 {output_name} 已经存在。如需重新合并，请手动删除后重试。")
            self.state.mark_skipped(base_path, f"目标文件 {output_name} 已存在")
            return

        # ── 执行 FFmpeg 合并 ────────────────────────────────
        log(f"开始合并音视频...")
        success, result = merge_audio_video(
            self.ffmpeg_path, self.ffprobe_path, video_path, audio_path, output_path
        )

        if not success:
            log(f"合并失败：{result}")
            return

        tmp_path = Path(result)
        log(f"FFmpeg 合并完成，正在验证输出...")

        # ── 验证输出 ────────────────────────────────────────
        valid, vmsg = validate_output(
            self.ffprobe_path, tmp_path, video_duration, audio_duration
        )

        if not valid:
            log(f"验证失败：{vmsg}")
            # 删除临时文件
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return

        log(vmsg)

        # ── 重命名：.tmp.mp4 → .mp4 ─────────────────────────
        try:
            tmp_path.rename(output_path)
        except OSError as e:
            log(f"重命名失败：{e}，临时文件保留在 {tmp_path.name}")
            return

        # ── 记录成功状态 ────────────────────────────────────
        current = check_file_stability(base_path, extra_path)
        if current:
            self.state.mark_processed(
                base_path, extra_path, output_path,
                current["base_size"], current["base_mtime"],
                current["extra_size"], current["extra_mtime"],
            )

        log(f"合并成功：{output_name}")
        log(f"源文件已保留：{base_path.name}、{extra_path.name}")

