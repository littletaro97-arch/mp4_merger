"""scanner.py - 目录扫描、文件配对与稳定性检测"""

import os
import re
import time
from pathlib import Path


def extract_root_name(filepath):
    """提取文件名的基础名称（去掉尾部 (数字) 后缀）

    例如：
        ABC.mp4              → "ABC"
        ABC(1).mp4           → "ABC"
        ABC(2).mp4           → "ABC"
        课程(第一章).mp4        → "课程(第一章)"
        课程(第一章)(1).mp4     → "课程(第一章)"
    """
    stem = filepath.stem
    # 匹配末尾的 (数字)
    match = re.match(r"^(.*)\((\d+)\)$", stem)
    if match:
        return match.group(1)
    return stem


def scan_directory(directory):
    """递归扫描目录，返回所有 .mp4 文件（不区分大小写）的 Path 列表"""
    mp4_files = []
    try:
        for root, dirs, files in os.walk(directory):
            for f in files:
                if f.lower().endswith(".mp4"):
                    mp4_files.append(Path(root) / f)
    except PermissionError as e:
        print(f"权限不足，跳过部分目录: {e}")
    except OSError as e:
        print(f"扫描目录出错: {e}")
    return mp4_files


def find_file_pairs(mp4_files):
    """从 MP4 文件列表中查找有效的 (base.mp4, base(1).mp4) 配对

    Returns:
        (pairs, skipped):
            pairs: list of (base_path, extra_path, root_name)
            skipped: list of (root_name, reason_string)
    """
    # 按 (目录, root_name) 分组
    groups = {}
    for fp in mp4_files:
        root = extract_root_name(fp)
        key = (os.path.normpath(str(fp.parent)), root)
        groups.setdefault(key, []).append(fp)

    pairs = []
    skipped = []

    for (dir_path, root_name), files in groups.items():
        base_file = None
        extra_file = None
        has_higher_number = False
        higher_number_files = []

        for f in files:
            stem = f.stem
            if stem == root_name:
                base_file = f
            elif stem == root_name + "(1)":
                extra_file = f
            else:
                # 检查是否是 root_name + (N) (N >= 2)
                suffix = stem[len(root_name) :]
                m = re.match(r"^\((\d+)\)$", suffix)
                if m:
                    num = int(m.group(1))
                    if num >= 2:
                        has_higher_number = True
                        higher_number_files.append(f)
                    elif num == 1:
                        # 理论上不会到这里，但为了安全
                        if extra_file is None:
                            extra_file = f
                        else:
                            has_higher_number = True
                            higher_number_files.append(f)
                # 其他情况忽略（名称包含 root_name 但不匹配 (N) 模式的文件）

        if has_higher_number:
            extra_name = higher_number_files[0].name
            skipped.append(
                (
                    root_name,
                    f"跳过：检测到额外编号文件 {extra_name}，当前自动化仅支持严格的「基础文件 + (1)」二文件组合。",
                )
            )
            continue

        if base_file is not None and extra_file is not None:
            pairs.append((base_file, extra_file, root_name))
        # 其他情况（只有 base 没有 (1)，或只有 (1) 没有 base）忽略

    return pairs, skipped


def check_file_stability(base_path, extra_path):
    """检查两个文件的大小和最后修改时间

    Returns:
        dict 包含文件状态信息，或 None（如果文件无法读取）
    """
    try:
        base_stat = base_path.stat()
        extra_stat = extra_path.stat()
        return {
            "base_size": base_stat.st_size,
            "base_mtime": base_stat.st_mtime,
            "extra_size": extra_stat.st_size,
            "extra_mtime": extra_stat.st_mtime,
            "stable": True,
        }
    except OSError:
        return None


def are_files_stable(prev_state, current_state):
    """比较两次文件状态，判断是否稳定不变"""
    if prev_state is None or current_state is None:
        return False
    return (
        prev_state["base_size"] == current_state["base_size"]
        and prev_state["base_mtime"] == current_state["base_mtime"]
        and prev_state["extra_size"] == current_state["extra_size"]
        and prev_state["extra_mtime"] == current_state["extra_mtime"]
    )


def files_still_exist_and_readable(base_path, extra_path):
    """确认两个文件仍然存在、大小 > 0、可读取"""
    try:
        if not base_path.exists() or not extra_path.exists():
            return False, "文件已不存在"
        if base_path.stat().st_size == 0:
            return False, f"文件已为空: {base_path.name}"
        if extra_path.stat().st_size == 0:
            return False, f"文件已为空: {extra_path.name}"
        # 尝试打开文件验证可读性
        with open(base_path, "rb") as f:
            f.read(1)
        with open(extra_path, "rb") as f:
            f.read(1)
        return True, ""
    except OSError as e:
        return False, f"文件无法读取: {e}"