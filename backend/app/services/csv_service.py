"""CSV/Excel upload service: upload file -> Doris temporary table -> queryable (Vanna#20)."""
import csv
import io
from datetime import datetime
from app.core.log import logger
from app.core.path_guard import validate_filename


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

    # 1. Parse CSV
    content = file_content.decode('utf-8-sig')  # Handle BOM
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    if not headers:
        return {"error": "CSV has no valid header"}

    rows = list(reader)
    if not rows:
        return {"error": "CSV has no data rows"}

    # 2. Generate temporary table name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = filename.replace('.', '_').replace(' ', '_')[:20]
    table_name = f"tmp_{safe_name}_{timestamp}"

    # 3. Infer column types
    col_types = {}
    for h in headers:
        col_types[h] = _infer_type([r.get(h, '') for r in rows[:50]])

    # 4. Create table
    col_defs = []
    for h in headers:
        safe_h = h.replace(' ', '_').replace('.', '_')[:60]
        col_defs.append(f"`{safe_h}` {col_types[h]}")
    ddl = (f"CREATE TABLE {table_name} ({', '.join(col_defs)}) "
           f"DUPLICATE KEY(`{headers[0].replace(' ', '_')[:60]}`) "
           f"DISTRIBUTED BY HASH(`{headers[0].replace(' ', '_')[:60]}`) BUCKETS 1 "
           f"PROPERTIES('replication_num'='1')")

    await session.execute(text(ddl))

    # 5. Batch insert
    safe_headers = [h.replace(' ', '_').replace('.', '_')[:60] for h in headers]
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
                # Type conversion
                if col_types[headers[k]] in ('INT', 'BIGINT'):
                    try:
                        params[key] = int(float(val)) if val else 0
                    except:
                        params[key] = 0
                elif col_types[headers[k]] == 'DOUBLE':
                    try:
                        params[key] = float(val) if val else 0.0
                    except:
                        params[key] = 0.0
                else:
                    params[key] = val[:200] if val else ''
                placeholders.append(f":{key}")
            values_list.append(f"({', '.join(placeholders)})")

        insert_sql = f"INSERT INTO {table_name} ({', '.join(f'`{sh}`' for sh in safe_headers)}) VALUES {', '.join(values_list)}"
        await session.execute(text(insert_sql), params)

    await session.commit()

    logger.info(f"CSV upload: {filename} -> {table_name} ({len(rows)} rows, {len(headers)} cols)")
    return {
        "table_name": table_name,
        "columns": [{"name": h, "type": col_types[h]} for h in headers],
        "row_count": len(rows),
    }


def _infer_type(values: list) -> str:
    """Infer column data types."""
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
