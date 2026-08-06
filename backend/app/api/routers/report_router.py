"""Report API: generate HTML report + Superset Dashboard."""
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.api.dependencies import get_query_service
from app.core.auth import verify_token
from app.core.log import logger
from app.services.query_service import QueryService
from app.services.report_service import generate_html_report

report_router = APIRouter()


class ReportRequest(BaseModel):
    title: str = "Data Analysis Report"
    queries: list[str] = []


@report_router.post("/api/report/html")
async def generate_report(
    req: ReportRequest,
    bg: BackgroundTasks,
    user: dict = Depends(verify_token),
    service: QueryService = Depends(get_query_service),
):
    """Generate an HTML report (calls the NL2SQL system for data + AI analysis + ECharts charts).

    Request body:
    {
        "title": "Sales Analysis Report",
        "queries": ["Total sales by region", "TOP5 customers", "Category share"]
    }
    """
    sections = []
    for q in req.queries:
        # Call the NL2SQL system
        result_data = None
        async for chunk in service.query(q, username=user.get("sub", "anonymous")):
            if "result" in chunk:
                import json
                try:
                    parsed = json.loads(chunk.replace("data: ", "").strip())
                    if isinstance(parsed.get("result"), list):
                        result_data = parsed["result"]
                except:
                    pass
        sections.append({
            "question": q,
            "data": result_data or [],
            "analysis": "",  # Can be wired to LLM analysis
        })

    # Generate HTML
    html_path = generate_html_report(req.title, sections)

    return JSONResponse({
        "status": "ok",
        "path": html_path,
        "sections": len(sections),
        "time": datetime.now().isoformat(),
    })


@report_router.get("/api/report/download")
async def download_report(path: str, user: dict = Depends(verify_token)):
    """Download the HTML report file."""
    import os
    if not os.path.exists(path):
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(path, media_type="text/html",
                        filename=os.path.basename(path))


@report_router.post("/api/report/push-feishu")
async def push_report_to_feishu(
    req: ReportRequest,
    bg: BackgroundTasks,
    user: dict = Depends(verify_token),
    service: QueryService = Depends(get_query_service),
):
    """Generate a report and push it to Feishu."""
    bg.add_task(_generate_and_push, req, user.get("sub", "anonymous"), service)
    return {"status": "started", "message": "Report is being generated and will be pushed to Feishu when done"}


async def _generate_and_push(req: ReportRequest, username: str, service: QueryService):
    """Generate report in background and push to Feishu."""
    try:
        sections = []
        for q in req.queries:
            result_data = None
            async for chunk in service.query(q, username=username):
                if "result" in chunk:
                    import json
                    try:
                        parsed = json.loads(chunk.replace("data: ", "").strip())
                        if isinstance(parsed.get("result"), list):
                            result_data = parsed["result"]
                    except:
                        pass
            sections.append({"question": q, "data": result_data or [], "analysis": ""})

        html_path = generate_html_report(req.title, sections)

        # Push to Feishu (send a message with the file link)
        import sys
        sys.path.insert(0, r"D:\large_model\mcp\feishu-github-agent")
        from feishu_client import send_text
        msg = f"Report generated: {req.title}\nPath: {html_path}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        send_text(msg)
        logger.info(f"Report pushed to Feishu: {html_path}")
    except Exception as e:
        logger.error(f"Failed to push report: {e}")


class ExportSchema(BaseModel):
    data: list[dict]
    format: str = "excel"
    title: str = "Data Export"


@report_router.post("/api/export")
async def export_data(body: ExportSchema, user: dict = Depends(verify_token)):
    """Export query results (Excel/CSV/JSON)."""
    from fastapi.responses import Response
    from app.services.export_service import export_excel, export_csv_bytes, export_json_bytes

    fmt = body.format.lower()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "excel":
        content = export_excel(body.data, title=body.title)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=export_{ts}.xlsx"},
        )
    elif fmt == "csv":
        content = export_csv_bytes(body.data)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=export_{ts}.csv"},
        )
    elif fmt == "json":
        content = export_json_bytes(body.data)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=export_{ts}.json"},
        )
    else:
        return {"error": f"Unsupported format: {fmt}, available: excel/csv/json"}
