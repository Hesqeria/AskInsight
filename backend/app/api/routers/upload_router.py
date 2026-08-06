"""File upload API: CSV/Excel -> temporary table."""
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.services.csv_service import upload_csv_to_temp_table
from app.core.log import logger
from app.core.path_guard import validate_filename, sanitize_table_name

upload_router = APIRouter()


@upload_router.post("/api/upload/csv")
async def upload_csv(
    file: UploadFile = File(...),
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Upload a CSV file -> create a Doris temporary table (Vanna#20).

    After upload, the table can be queried via the NL2SQL system.
    """
    safe_name, err = validate_filename(file.filename)
    if not safe_name:
        return JSONResponse({"error": err}, status_code=400)
    if not file.filename.endswith(('.csv', '.CSV')):
        return JSONResponse({"error": "Only CSV files are supported"}, status_code=400)

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB limit
        return JSONResponse({"error": "File exceeds the 10MB limit"}, status_code=413)

    result = await upload_csv_to_temp_table(content, file.filename, session)
    logger.info(f"CSV upload: {file.filename} -> {result.get('table_name', 'ERROR')}")
    return result


@upload_router.post("/api/upload/query")
async def query_temp_table(
    body: dict,
    user: dict = Depends(verify_token),
):
    """Query an uploaded temporary table."""
    # Forward to /api/query
    # Simplification: directly use the NL2SQL API to query the temporary table
    table_name = body.get("table_name", "")
    ok, result = sanitize_table_name(table_name)
    if not ok:
        return JSONResponse({"error": result}, status_code=400)
    table_name = result
    # Reuse the NL2SQL API here
    return JSONResponse({"message": "Use /api/query to query the temporary table", "table_name": table_name})
