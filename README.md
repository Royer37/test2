"""
FastAPI backend — serves both the REST/SSE API and the Next.js static frontend
"""
import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from scraper import MilanunciosScraper, export_csv, export_excel, export_json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Milanuncios Scraper")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
sessions: dict[str, dict] = {}


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = 5
    delay_min: float = 3.0
    delay_max: float = 7.0
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    location_filter: Optional[str] = None


class ExportRequest(BaseModel):
    session_id: str
    format: str


@app.post("/api/scrape/start")
async def start_scrape(req: ScrapeRequest):
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "status": "running",
        "events": asyncio.Queue(),
        "results": [],
        "scraper": None,
        "task": None,
    }

    async def run():
        session = sessions[session_id]

        def on_progress(event):
            try:
                session["events"].put_nowait(event)
            except Exception:
                pass

        scraper = MilanunciosScraper(
            delay_min=req.delay_min,
            delay_max=req.delay_max,
            on_progress=on_progress,
        )
        session["scraper"] = scraper

        try:
            results = await scraper.scrape(
                base_url=req.url,
                max_pages=req.max_pages,
                min_price=req.min_price,
                max_price=req.max_price,
                location_filter=req.location_filter,
            )
            session["results"] = results
            session["status"] = "done"
            session["events"].put_nowait({"event": "complete", "total": len(results)})
        except Exception as e:
            logger.error(f"Scrape error: {e}")
            session["status"] = "error"
            session["events"].put_nowait({"event": "error", "message": str(e)})

    task = asyncio.create_task(run())
    sessions[session_id]["task"] = task
    return {"session_id": session_id}


@app.post("/api/scrape/stop/{session_id}")
async def stop_scrape(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    scraper: MilanunciosScraper = session.get("scraper")
    if scraper:
        scraper.stop()
        session["status"] = "stopped"
        session["events"].put_nowait({"event": "stopped"})
    return {"status": "stop_requested"}


@app.get("/api/scrape/events/{session_id}")
async def scrape_events(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        queue: asyncio.Queue = session["events"]
        yield f"data: {json.dumps({'event': 'connected', 'session_id': session_id})}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("event") in ("complete", "error", "stopped"):
                    break
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
                if session.get("status") in ("done", "error", "stopped"):
                    break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/scrape/results/{session_id}")
async def get_results(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "status": session["status"],
        "results": session["results"],
        "count": len(session["results"]),
    }


@app.post("/api/export")
async def export_data(req: ExportRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    data = session["results"]
    if not data:
        raise HTTPException(status_code=400, detail="No data to export")

    exports_dir = Path("exports")
    exports_dir.mkdir(exist_ok=True)

    if req.format == "csv":
        path = export_csv(data, str(exports_dir / f"export_{req.session_id}.csv"))
        media_type = "text/csv"
        filename = "milanuncios_export.csv"
    elif req.format == "excel":
        path = export_excel(data, str(exports_dir / f"export_{req.session_id}.xlsx"))
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "milanuncios_export.xlsx"
    elif req.format == "json":
        path = export_json(data, str(exports_dir / f"export_{req.session_id}.json"))
        media_type = "application/json"
        filename = "milanuncios_export.json"
    else:
        raise HTTPException(status_code=400, detail="Invalid format")

    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ── Serve Next.js static export ──────────────────────────────────────────────
FRONTEND_OUT = Path(__file__).parent / "frontend_out"

if FRONTEND_OUT.exists():
    # Serve Next.js static assets
    app.mount("/static", StaticFiles(directory=str(FRONTEND_OUT / "_next" / "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # Try exact file match first
        target = FRONTEND_OUT / full_path
        if target.is_file():
            return FileResponse(str(target))
        # Try with .html extension
        html_target = FRONTEND_OUT / f"{full_path}.html"
        if html_target.is_file():
            return FileResponse(str(html_target))
        # Fallback to index
        index = FRONTEND_OUT / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=404, detail="Not found")
else:
    @app.get("/")
    async def root():
        return {"message": "API running. Frontend not built yet."}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port)
