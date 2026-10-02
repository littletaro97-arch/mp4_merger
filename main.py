"""main.py - MP4 音视频自动合并工具 GUI 入口

持续扫描指定目录，自动发现"同名 MP4 + (1) MP4"二文件组合，
判断纯视频 + 纯音频，时长一致后使用 FFmpeg 无损封装。
"""

import queue
import sys
import threading
import time
from pathlib import Path
from datetime import datetime

# ── 确保当前目录在 sys.path 中 ──────────────────────────────
_script_dir = Path(__file__).parent.resolve()
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

from state import AutomationState
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

# ── 常量 ────────────────────────────────────────────────────
SCAN_INTERVAL = 10  # 秒
STABILITY_WAIT = SCAN_INTERVAL  # 两次扫描之间即为稳定检测间隔


class LogQueueHandler:
    """线程安全的日志队列，由主线程定时消费"""

    def __init__(self, text_widget):
        self.queue = queue.Queue()
        self.text_widget = text_widget

    def log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        self.queue.put(f"{timestamp} {message}")

    def process_queue(self):
        while not self.queue.empty():
            try:
                msg = self.queue.get_nowait()
                self.text_widget.insert(tk.END, msg + "\n")
                self.text_widget.see(tk.END)
            except queue.Empty:
                break
        # 每 100ms 检查一次
        self.text_widget.after(100, self.process_queue)


class MergerApp:
    """主 GUI 应用"""

    def __init__(self, root):
        self.root = root
        self.root.title("MP4 音视频自动合并工具")
        self.root.geometry("780x620")
        self.root.minsize(600, 450)

        # 状态
        self.ffmpeg_path = None
        self.ffprobe_path = None
        self.monitor_dir = tk.StringVar()
        self.running = False
        self.scan_thread = None
        self.stop_event = threading.Event()

        # 初始化状态管理
        self.state = AutomationState()

        # 加载历史目录
        self.last_dir = self.state.get_last_dir()
        self.history_dirs = self.state.get_history_dirs()

        # 检测 FFmpeg
        self.ffmpeg_path, self.ffprobe_path = find_ffmpeg()

        # 创建界面
        self._build_ui()

        # 启动日志队列处理
        self.log_handler = LogQueueHandler(self.log_text)
        self.log_text.after(100, self.log_handler.process_queue)

        # 窗口关闭时停止扫描
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # 默认加载上次目录
        if self.last_dir and Path(self.last_dir).is_dir():
            self.monitor_dir.set(self.last_dir)
            self.dir_combo.set(self.last_dir)

        # 初始日志
        self._log_startup_info()

    def _log_startup_info(self):
        self.log_handler.log(f"程序启动")
        if self.ffmpeg_path and self.ffprobe_path:
            self.log_handler.log(f"FFmpeg: {self.ffmpeg_path}")
            self.log_handler.log(f"ffprobe: {self.ffprobe_path}")
        else:
            self.log_handler.log("未找到 FFmpeg/ffprobe，请安装或将 ffmpeg.exe/ffprobe.exe 放入程序目录下的 ffmpeg/ 文件夹。")

    # ── 界面构建 ────────────────────────────────────────────

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── 目录选择 ──
        dir_frame = ttk.LabelFrame(main_frame, text="监控目录", padding="8")
        dir_frame.pack(fill=tk.X, pady=(0, 8))

        dir_row = ttk.Frame(dir_frame)
        dir_row.pack(fill=tk.X)

        self.dir_combo = ttk.Combobox(
            dir_row, textvariable=self.monitor_dir,
            font=("Consolas", 10),
            values=self.history_dirs,
        )
        self.dir_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        # 点击下拉框时刷新历史目录列表
        self.dir_combo.bind("<FocusIn>", self._refresh_history_dropdown)
        self.dir_combo.bind("<Button-1>", self._refresh_history_dropdown)

        self.browse_btn = ttk.Button(
            dir_row, text="选择目录", command=self._browse_directory
        )
        self.browse_btn.pack(side=tk.RIGHT)

        # ── 状态与控制 ──
        ctrl_frame = ttk.LabelFrame(main_frame, text="控制", padding="8")
        ctrl_frame.pack(fill=tk.X, pady=(0, 8))

        ctrl_row = ttk.Frame(ctrl_frame)
        ctrl_row.pack(fill=tk.X)

        self.status_label = ttk.Label(
            ctrl_row, text="状态：就绪", font=("Microsoft YaHei", 10)
        )
        self.status_label.pack(side=tk.LEFT, padx=(0, 16))

        self.ffmpeg_status = ttk.Label(
            ctrl_row,
            text="✓ FFmpeg 已就绪" if self.ffmpeg_path else "✗ FFmpeg 未找到",
            font=("Microsoft YaHei", 9),
            foreground="green" if self.ffmpeg_path else "red",
        )
        self.ffmpeg_status.pack(side=tk.LEFT, padx=(0, 16))

        self.start_btn = ttk.Button(
            ctrl_row, text="开始自动化", command=self._start_automation
        )
        self.start_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.stop_btn = ttk.Button(
            ctrl_row,
            text="停止自动化",
            command=self._stop_automation,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # ── 日志区域 ──
        log_frame = ttk.LabelFrame(main_frame, text="日志", padding="8")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            state=tk.NORMAL,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 配置日志标签颜色
        self.log_text.tag_config("info", foreground="#d4d4d4")
        self.log_text.tag_config("success", foreground="#4ec9b0")
        self.log_text.tag_config("warn", foreground="#ce9178")
        self.log_text.tag_config("error", foreground="#f44747")

    def _browse_directory(self):
        directory = filedialog.askdirectory(title="选择要监控的目录")
        if directory:
            self.monitor_dir.set(directory)
            self._add_to_history(directory)
            self.log_handler.log(f"已选择监控目录：{directory}")

    def _add_to_history(self, directory):
        """将目录加入历史记录（最多5条）"""
        self.state.set_last_dir(directory)
        self.history_dirs = self.state.get_history_dirs()
        self.dir_combo["values"] = self.history_dirs

    def _refresh_history_dropdown(self, event=None):
        """刷新下拉框的历史目录列表"""
        self.history_dirs = self.state.get_history_dirs()
        self.dir_combo["values"] = self.history_dirs

    # ── 启动/停止 ───────────────────────────────────────────

    def _start_automation(self):
        if self.running:
            return

        # 检查 FFmpeg
        if not self.ffmpeg_path or not self.ffprobe_path:
            self.log_handler.log("错误：未找到 FFmpeg/ffprobe，无法启动自动化。")
            self.log_handler.log("请将 ffmpeg.exe 和 ffprobe.exe 放入程序目录下的 ffmpeg/ 文件夹，或加入 PATH。")
            return

        # 检查目录
        monitor_dir = self.monitor_dir.get().strip()
        if not monitor_dir:
            self.log_handler.log("错误：请先选择监控目录。")
            return
        if not Path(monitor_dir).is_dir():
            self.log_handler.log(f"错误：目录不存在或无法访问：{monitor_dir}")
            return

        # 保存到历史记录
        self._add_to_history(monitor_dir)

        self.running = True
        self.stop_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.browse_btn.config(state=tk.DISABLED)
        self.dir_combo.config(state=tk.DISABLED)
        self.status_label.config(text="状态：运行中（每 10 秒扫描一次）", foreground="green")

        self.log_handler.log(f"━" * 50)
        self.log_handler.log(f"开始监控：{monitor_dir}")

        # 启动后台扫描线程
        self.scan_thread = threading.Thread(
            target=self._scan_loop,
            args=(monitor_dir,),
            daemon=True,
            name="ScanThread",
        )
        self.scan_thread.start()

    def _stop_automation(self):
        if not self.running:
            return
        self.running = False
        self.stop_event.set()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.browse_btn.config(state=tk.NORMAL)
        self.dir_combo.config(state=tk.NORMAL)
        self.status_label.config(text="状态：已停止", foreground="black")
        self.log_handler.log("已停止自动化监控。")

    # ── 后台扫描循环 ───────────────────────────────────────

    def _scan_loop(self, monitor_dir):
        """后台线程：每 10 秒扫描一次目录"""
        while not self.stop_event.is_set():
            try:
                self._run_one_scan(monitor_dir)
            except Exception as e:
                self.log_handler.log(f"扫描过程异常: {e}")
            # 等待 10 秒（可被 stop_event 中断）
            for _ in range(SCAN_INTERVAL):
                if self.stop_event.is_set():
                    return
                time.sleep(1)

    def _run_one_scan(self, monitor_dir):
        """执行一次扫描流程"""
        # 1. 扫描所有 MP4 文件
        mp4_files = scan_directory(monitor_dir)
        if not mp4_files:
            return

        # 2. 查找有效配对
        pairs, skipped = find_file_pairs(mp4_files)

        # 3. 记录跳过信息
        for root_name, reason in skipped:
            self.log_handler.log(reason)

        # 4. 处理每个配对
        for base_path, extra_path, root_name in pairs:
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

    # ── 窗口关闭处理 ────────────────────────────────────────

    def _on_closing(self):
        if self.running:
            self.log_handler.log("正在停止扫描...")
            self.stop_event.set()
            self.running = False
        self.root.destroy()


def main():
    root = tk.Tk()
    app = MergerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()