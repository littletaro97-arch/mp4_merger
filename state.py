"""state.py - 自动化状态持久化"""

import json
import os
from pathlib import Path
from datetime import datetime

STATE_FILE = "automation_state.json"


MAX_HISTORY = 5


class AutomationState:
    """管理已处理和待处理文件组的状态，以及历史目录"""

    def __init__(self, state_dir=None):
        if state_dir is None:
            state_dir = Path(__file__).parent
        self.state_path = Path(state_dir) / STATE_FILE
        self.data = self._load()

    def _load(self):
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 确保必要字段存在
                data.setdefault("processed", {})
                data.setdefault("pending", {})
                data.setdefault("skipped", {})
                data.setdefault("last_dir", "")
                data.setdefault("history_dirs", [])
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "processed": {},
            "pending": {},
            "skipped": {},
            "last_dir": "",
            "history_dirs": [],
        }

    def save(self):
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ── 已处理状态 ──────────────────────────────────────────

    def is_processed(self, base_path):
        return str(base_path) in self.data.get("processed", {})

    def get_processed(self, base_path):
        return self.data.get("processed", {}).get(str(base_path))

    def mark_processed(
        self,
        base_path,
        extra_path,
        merged_path,
        base_size,
        base_mtime,
        extra_size,
        extra_mtime,
    ):
        self.data.setdefault("processed", {})[str(base_path)] = {
            "base": str(base_path),
            "extra": str(extra_path),
            "merged": str(merged_path),
            "base_size": base_size,
            "base_mtime": base_mtime,
            "extra_size": extra_size,
            "extra_mtime": extra_mtime,
            "timestamp": datetime.now().isoformat(),
        }
        # 移除待处理状态
        self.data.get("pending", {}).pop(str(base_path), None)
        self.save()

    # ── 待处理（文件稳定性检测） ──────────────────────────────

    def is_pending(self, base_path):
        return str(base_path) in self.data.get("pending", {})

    def get_pending(self, base_path):
        return self.data.get("pending", {}).get(str(base_path))

    def set_pending(self, base_path, extra_path, base_size, base_mtime, extra_size, extra_mtime):
        self.data.setdefault("pending", {})[str(base_path)] = {
            "base": str(base_path),
            "extra": str(extra_path),
            "base_size": base_size,
            "base_mtime": base_mtime,
            "extra_size": extra_size,
            "extra_mtime": extra_mtime,
            "timestamp": datetime.now().isoformat(),
        }
        self.save()

    def remove_pending(self, base_path):
        self.data.get("pending", {}).pop(str(base_path), None)
        self.save()

    # ── 跳过记录 ────────────────────────────────────────────

    def mark_skipped(self, base_path, reason):
        self.data.setdefault("skipped", {})[str(base_path)] = {
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        }
        self.save()

    def is_skipped(self, base_path):
        return str(base_path) in self.data.get("skipped", {})

    # ── 历史目录 ────────────────────────────────────────────

    def get_last_dir(self):
        """获取上次使用的目录"""
        return self.data.get("last_dir", "")

    def get_history_dirs(self):
        """获取历史目录列表"""
        return self.data.get("history_dirs", [])

    def set_last_dir(self, directory):
        """保存上次使用的目录，并加入历史（去重，保留最近5条）"""
        self.data["last_dir"] = directory
        # 添加到历史（去重）
        history = self.data.get("history_dirs", [])
        # 移除已存在的同名条目
        history = [d for d in history if d != directory]
        # 插入到最前面
        history.insert(0, directory)
        # 只保留最近 MAX_HISTORY 条
        self.data["history_dirs"] = history[:MAX_HISTORY]
        self.save()

    # ── 清理 ────────────────────────────────────────────────

    def cleanup_stale(self):
        """移除已不存在的文件对应的状态条目"""
        for category in ("processed", "pending", "skipped"):
            entries = self.data.get(category, {})
            stale = []
            for key, entry in entries.items():
                base = entry.get("base")
                extra = entry.get("extra")
                if base and not Path(base).exists():
                    stale.append(key)
                elif extra and not Path(extra).exists():
                    stale.append(key)
            for key in stale:
                del entries[key]
        self.save()