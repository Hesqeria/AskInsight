"""CSV/Excel upload service: upload file -> Doris temporary table -> queryable."""
import csv
import io
import re
from datetime import datetime
from app.core.log import logger
from app.core.path_guard import validate_filename

MAX_CSV_SIZE = 10 * 1024 * 1024  # 10MB
MAX_CSV_ROWS = 50000
ALLOWED_COL_TYPES = {"VARCHAR(255)", "BIGINT", "DOUBLE", "DATE"}


def _sanitize_identifier(name: str) -> str:
    """Sanitize a CSV header into a safe SQL identifier.

    Only [a-zA-Z0-9_] allowed; everything else -> '_'.
    Prevents backtick injection, semicolons, SQL keywords in column names.
    """
    safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)[:60]
    if not safe or safe[0].isdigit():
        safe = f"col_{safe}"
    return safe


async def upload_csv_to_temp_table(file_content: bytes, filename: str, session) -> dict:
    """Upload a CSV file -> Doris temporary table.

    Args:
        file_content: file byte content
        filename: original filename
        session: Doris AsyncSession

    Returns:
        {"table_name": "...", "columns": [...], "row_count": N}
    """
    from sqlalchemy import text

    # 0. Validate filename safety
    ok, safe_name = validate_filename(filename)
    if not ok:
        return {"error": f"Unsafe filename: {safe_name}"}

    # 0b. Size limit
    if len(file_content) > MAX_CSV_SIZE:
        return {"error": f"File too large: {len(file_content)} bytes (max {MAX_CSV_SIZE})"}

    # 1. Parse CSV
    try:
        content = file_content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            content = file_content.decode('gbk')
        except UnicodeDecodeError:
            return {"error": "Unsupported file encoding (use UTF-8 or GBK)"}

    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    if not headers:
        return {"error": "CSV has no valid header"}

    rows = list(reader)
    if not rows:
        return {"error": "CSV has no data rows"}

    if len(rows) > MAX_CSV_ROWS:
        logger.warning(f"CSV truncated: {len(rows)} -> {MAX_CSV_ROWS} rows")
        rows = rows[:MAX_CSV_ROWS]

    # 2. Generate temporary table name (sanitized)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_base = _sanitize_identifier(filename.split('.')[0])[:20]
    table_name = f"tmp_{safe_base}_{timestamp}"

    # 3. Sanitize all headers
    safe_headers = [_sanitize_identifier(h) for h in headers]
    # Deduplicate
    seen = set()
    for i, sh in enumerate(safe_headers):
        base = sh
        n = 2
        while sh in seen:
            sh = f"{base}_{n}"
            n += 1
        seen.add(sh)
        safe_headers[i] = sh

    # 4. Infer column types
    col_types = {}
    for i, h in enumerate(headers):
        col_types[safe_headers[i]] = _infer_type([r.get(h, '') for r in rows[:50]])

    # 5. Create table (all identifiers sanitized)
    col_defs = [f"`{sh}` {col_types[sh]}" for sh in safe_headers]
    first_col = safe_headers[0]
    ddl = (f"CREATE TABLE `{table_name}` ({', '.join(col_defs)}) "
           f"DUPLICATE KEY(`{first_col}`) "
           f"DISTRIBUTED BY HASH(`{first_col}`) BUCKETS 1 "
           f"PROPERTIES('replication_num'='1')")

    await session.execute(text(ddl))

    # 6. Batch insert (parameterized)
    batch_size = 50
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        values_list = []
        params = {}
        for j, row in enumerate(batch):
            placeholders = []
            for k, sh in enumerate(safe_headers):
                key = f"v{i}_{j}_{k}"
                val = row.get(headers[k], '')
                ct = col_types[sh]
                if ct in ('INT', 'BIGINT'):
                    try:
                        params[key] = int(float(val)) if val else 0
                    except (ValueError, TypeError):
                        params[key] = 0
                elif ct == 'DOUBLE':
                    try:
                        params[key] = float(val) if val else 0.0
                    except (ValueError, TypeError):
                        params[key] = 0.0
                else:
                    params[key] = str(val)[:200] if val else ''
                placeholders.append(f":{key}")
            values_list.append(f"({', '.join(placeholders)})")

        col_list = ', '.join(f'`{sh}`' for sh in safe_headers)
        insert_sql = f"INSERT INTO `{table_name}` ({col_list}) VALUES {', '.join(values_list)}"
        await session.execute(text(insert_sql), params)

    await session.commit()

    logger.info(f"CSV upload: {filename} -> {table_name} ({len(rows)} rows, {len(headers)} cols)")
    return {
        "table_name": table_name,
        "columns": [{"name": sh, "type": col_types[sh]} for sh in safe_headers],
        "row_count": len(rows),
    }


def _infer_type(values: list) -> str:
    """Infer column data types from sample values."""
    int_count = 0
    float_count = 0
    for v in values:
        v = str(v).strip()
        if not v:
            continue
        try:
            int(v)
            int_count += 1
        except ValueError:
            try:
                float(v)
                float_count += 1
            except ValueError:
                return "VARCHAR(255)"
    total = int_count + float_count
    if total == 0:
        return "VARCHAR(255)"
    if int_count == total:
        return "BIGINT"
    if float_count + int_count == total:
        return "DOUBLE"
    return "VARCHAR(255)"
