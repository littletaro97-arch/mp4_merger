"""main.py - MP4 音视频自动合并工具 GUI 入口

持续扫描指定目录，自动发现"同名 MP4 + (1) MP4"二文件组合，
判断纯视频 + 纯音频，时长一致后使用 FFmpeg 无损封装。
"""

import queue
import sys
import threading
from pathlib import Path
from datetime import datetime

# ── 确保当前目录在 sys.path 中 ──────────────────────────────
_script_dir = Path(__file__).parent.resolve()
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

from state import AutomationState
from media import find_ffmpeg
from tasks import PairMergeTask

class LogQueueHandler:
    """线程安全的日志队列，由主线程定时消费"""

    def __init__(self, text_widget):
        self.queue = queue.Queue(maxsize=2000)
        self.text_widget = text_widget

    def log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        try:
            self.queue.put_nowait(f"{timestamp} {message}")
        except queue.Full:
            pass

    def process_queue(self):
        self.text_widget.config(state=tk.NORMAL)
        for _ in range(100):
            try:
                msg = self.queue.get_nowait()
                self.text_widget.insert(tk.END, msg + "\n")
                self.text_widget.see(tk.END)
            except queue.Empty:
                break
        lines = int(self.text_widget.index("end-1c").split(".")[0])
        if lines > 1500:
            self.text_widget.delete("1.0", f"{lines - 1500}.0")
        self.text_widget.config(state=tk.DISABLED)
        self.text_widget.after(250, self.process_queue)


class MergerApp:
    """主 GUI 应用"""

    def __init__(self, root):
        self.root = root
        self.root.title("媒体工具箱 · v0.2.0")
        self.root.geometry("960x700")
        self.root.minsize(800, 560)

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
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TButton", padding=(12, 7), font=("Microsoft YaHei UI", 10))
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        shell = ttk.Frame(self.root, padding=18)
        shell.pack(fill=tk.BOTH, expand=True)
        ttk.Label(shell, text="媒体工具箱", font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        ttk.Label(shell, text="本地处理 · 保留源文件 · 无损封装", foreground="#526176").pack(anchor="w", pady=(4, 14))
        notebook = ttk.Notebook(shell)
        notebook.pack(fill=tk.BOTH, expand=True)
        main_frame = ttk.Frame(notebook, padding=14)
        notebook.add(main_frame, text="  自动音视频配对  ")
        about = ttk.Frame(notebook, padding=20)
        notebook.add(about, text="  使用与环境  ")
        ttk.Label(about, text="自动音视频配对", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(about, text="同一目录中的 XX.mp4 与 XX(1).mp4：一份纯视频 + 一份纯音频。\n每 10 秒扫描一次，文件稳定后生成 XX_merged.mp4。\n支持递归子目录；存在 (2) 等额外编号时跳过。\n停止时等待当前文件处理结束，再停止监控。", justify="left").pack(anchor="w", pady=14)
        ttk.Label(about, text="后续功能将以独立任务页加入；现有配对逻辑位于 tasks.py。\n便携版请复制整个 MediaToolbox 文件夹，无需安装 Python 或 FFmpeg。", justify="left").pack(anchor="w", pady=14)
        ttk.Label(main_frame, text="自动音视频配对", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(main_frame, text="选择下载目录，自动将分离的音轨和视频轨封装为一个 MP4。", foreground="#526176").pack(anchor="w", pady=(0, 12))

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
            text="✓ FFmpeg 已就绪" if self.ffmpeg_path and self.ffprobe_path else "✗ FFmpeg 未找到",
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
        self.task = PairMergeTask(self.state, self.log_handler, self.ffmpeg_path, self.ffprobe_path, self.stop_event)
        self.scan_thread = threading.Thread(
            target=self.task.run,
            args=(monitor_dir,),
            daemon=True,
            name="ScanThread",
        )
        self.scan_thread.start()

    def _stop_automation(self):
        if not self.running:
            return
        self.stop_event.set()
        self.stop_btn.config(state=tk.DISABLED)
        self.status_label.config(text="状态：正在停止，等待当前任务完成", foreground="#9a6700")
        self.root.after(100, self._finish_stop)

    def _finish_stop(self):
        if self.scan_thread and self.scan_thread.is_alive():
            self.root.after(100, self._finish_stop)
            return
        self.running = False
        self.start_btn.config(state=tk.NORMAL)
        self.browse_btn.config(state=tk.NORMAL)
        self.dir_combo.config(state=tk.NORMAL)
        self.status_label.config(text="状态：已停止", foreground="black")
        self.log_handler.log("已停止自动化监控。")

    # ── 后台扫描循环 ───────────────────────────────────────

    # ── 窗口关闭处理 ────────────────────────────────────────

    def _on_closing(self):
        self.stop_event.set()
        if self.scan_thread and self.scan_thread.is_alive():
            self.status_label.config(text="状态：等待当前处理结束后关闭")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)
            self.root.after(100, self._on_closing)
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    app = MergerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()