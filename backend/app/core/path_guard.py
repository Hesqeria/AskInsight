"""路径白名单校验器

防止路径穿越攻击（DB-GPT 踩了 11 次的坑）。

校验规则：
  1. 禁止 .. 和绝对路径
  2. 文件名只允许字母数字中文._-
  3. 临时文件限定在系统 temp 目录
  4. 输出文件限定在指定白名单目录
"""
import os
import re
import tempfile
from pathlib import Path

from app.core.log import logger

# 允许的文件名模式：字母数字中文._-，长度1-200
_FILENAME_PATTERN = re.compile(r'^[\w\u4e00-\u9fff.\-]{1,200}$')

# 允许的文件后缀（白名单）
ALLOWED_EXTENSIONS = {
    '.py', '.csv', '.json', '.txt', '.html', '.md',
    '.png', '.jpg', '.jpeg', '.gif', '.svg',
}

# 允许写入的根目录（绝对路径前缀）
_SAFE_OUTPUT_DIRS = set()


def register_safe_dir(path: str):
    """注册安全输出目录"""
    abs_path = str(Path(path).resolve())
    _SAFE_OUTPUT_DIRS.add(abs_path)
    logger.debug(f"注册安全目录: {abs_path}")


def validate_filename(filename: str) -> tuple[bool, str]:
    """校验文件名安全性

    Returns:
        (is_safe, safe_filename_or_error)
    """
    if not filename:
        return False, "文件名为空"

    # P0: 严格禁止任何路径分隔符和穿越（不自动去除，直接拒绝）
    if '/' in filename or chr(92) in filename or '..' in filename:
        return False, f"文件名包含路径穿越字符: {filename}"

    # 检查文件名格式（字母数字中文._-）
    if not _FILENAME_PATTERN.match(filename):
        return False, f"文件名格式非法: {filename}"

    # 检查后缀白名单
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        return False, f"文件类型不允许: {ext}"

    return True, filename

def validate_path(path: str, must_be_in: str | None = None) -> tuple[bool, str]:
    """校验文件路径安全性

    Args:
        path: 待校验的路径
        must_be_in: 必须在此目录内（绝对路径）

    Returns:
        (is_safe, resolved_path_or_error)
    """
    if not path:
        return False, "路径为空"

    # 解析为绝对路径
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        return False, f"路径解析失败: {path}"

    # 检查路径穿越
    if '..' in path:
        return False, f"路径包含 .. 穿越: {path}"

    # 如果指定了必须在此目录内
    if must_be_in:
        must_abs = str(Path(must_be_in).resolve())
        if not resolved.startswith(must_abs):
            return False, f"路径越界: {resolved} 不在 {must_abs} 内"

    # 检查是否在安全目录内（如果注册了）
    if _SAFE_OUTPUT_DIRS:
        in_safe = any(resolved.startswith(d) for d in _SAFE_OUTPUT_DIRS)
        if not in_safe:
            return False, f"路径不在安全目录内: {resolved}"

    return True, resolved


def safe_temp_path(suffix: str = '.py', prefix: str = 'tmp') -> str:
    """生成安全的临时文件路径

    保证在系统 temp 目录内，使用随机文件名。
    """
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    os.close(fd)
    return path


def sanitize_table_name(name: str) -> tuple[bool, str]:
    """校验表名安全性（防 SQL 注入）

    表名只允许：字母、数字、下划线，长度1-64。
    """
    if not name:
        return False, "表名为空"

    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', name):
        return False, f"表名格式非法: {name}"

    # 检查 SQL 关键字
    sql_keywords = {'select', 'insert', 'update', 'delete', 'drop', 'create',
                    'alter', 'truncate', 'union', 'exec', 'execute', 'script'}
    if name.lower() in sql_keywords:
        return True, f"`{name}`"  # Wrap SQL keyword in backticks instead of rejecting

    return True, name
