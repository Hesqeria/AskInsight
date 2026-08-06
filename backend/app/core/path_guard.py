"""Path whitelist validator.

Prevents path traversal attacks (a pitfall DB-GPT hit 11 times).

Validation rules:
  1. Forbid .. and absolute paths
  2. Filenames allow only alphanumeric/Chinese/._-
  3. Temporary files must stay in the system temp directory
  4. Output files must stay in specified whitelist directories
"""
import os
import re
import tempfile
from pathlib import Path

from app.core.log import logger

# Allowed filename pattern: alphanumeric/Chinese/._-, length 1-200
_FILENAME_PATTERN = re.compile(r'^[\w\u4e00-\u9fff.\-]{1,200}$')

# Allowed file extensions (whitelist)
ALLOWED_EXTENSIONS = {
    '.py', '.csv', '.json', '.txt', '.html', '.md',
    '.png', '.jpg', '.jpeg', '.gif', '.svg',
}

# Allowed root directories for writing (absolute path prefixes)
_SAFE_OUTPUT_DIRS = set()


def register_safe_dir(path: str):
    """Register a safe output directory."""
    abs_path = str(Path(path).resolve())
    _SAFE_OUTPUT_DIRS.add(abs_path)
    logger.debug(f"Registered safe directory: {abs_path}")


def validate_filename(filename: str) -> tuple[bool, str]:
    """Validate filename safety.

    Returns:
        (is_safe, safe_filename_or_error)
    """
    if not filename:
        return False, "Filename is empty"

    # P0: Strictly forbid any path separator or traversal (reject directly instead of stripping)
    if '/' in filename or chr(92) in filename or '..' in filename:
        return False, f"Filename contains path traversal characters: {filename}"

    # Check filename format (alphanumeric/Chinese/._-)
    if not _FILENAME_PATTERN.match(filename):
        return False, f"Illegal filename format: {filename}"

    # Check extension whitelist
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        return False, f"File type not allowed: {ext}"

    return True, filename

def validate_path(path: str, must_be_in: str | None = None) -> tuple[bool, str]:
    """Validate file path safety.

    Args:
        path: path to validate
        must_be_in: must be inside this directory (absolute path)

    Returns:
        (is_safe, resolved_path_or_error)
    """
    if not path:
        return False, "Path is empty"

    # Resolve to absolute path
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        return False, f"Path resolution failed: {path}"

    # Check path traversal
    if '..' in path:
        return False, f"Path contains .. traversal: {path}"

    # If a required directory was specified
    if must_be_in:
        must_abs = str(Path(must_be_in).resolve())
        if not resolved.startswith(must_abs):
            return False, f"Path out of bounds: {resolved} not inside {must_abs}"

    # Check whether inside a safe directory (if any registered)
    if _SAFE_OUTPUT_DIRS:
        in_safe = any(resolved.startswith(d) for d in _SAFE_OUTPUT_DIRS)
        if not in_safe:
            return False, f"Path is not inside a safe directory: {resolved}"

    return True, resolved


def safe_temp_path(suffix: str = '.py', prefix: str = 'tmp') -> str:
    """Generate a safe temporary file path.

    Guaranteed to be inside the system temp directory, using a random filename.
    """
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    os.close(fd)
    return path


def sanitize_table_name(name: str) -> tuple[bool, str]:
    """Validate table name safety (prevent SQL injection).

    Table names allow only letters, digits, and underscores, length 1-64.
    """
    if not name:
        return False, "Table name is empty"

    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', name):
        return False, f"Illegal table name format: {name}"

    # Check SQL keywords
    sql_keywords = {'select', 'insert', 'update', 'delete', 'drop', 'create',
                    'alter', 'truncate', 'union', 'exec', 'execute', 'script'}
    if name.lower() in sql_keywords:
        return False, f"Table name is a SQL keyword: {name}"

    return True, name
