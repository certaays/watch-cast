from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.config import JobConfig
from src.engine import WatchCastEngine
from src.notifier import TelegramNotifier
from src.storage import StateStorage

DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")


class JobPayload(BaseModel):
    name: str
    type: str
    schedule: str
    target_chat_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


def create_app(
    config_path: str | Path = "jobs.yaml",
    db_path: str | Path = "data/state.db",
    notifier: TelegramNotifier | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Create the FastAPI Mission Control app backed by a live WatchCastEngine."""
    load_dotenv()

    storage = StateStorage(db_path=db_path)
    active_notifier = notifier or TelegramNotifier(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        default_chat_id=os.getenv("DEFAULT_CHAT_ID", ""),
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
    )
    engine = WatchCastEngine(
        config_path=config_path,
        storage=storage,
        notifier=active_notifier,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_scheduler:
            engine.start()
        try:
            yield
        finally:
            engine.shutdown()

    app = FastAPI(title="WatchCast Mission Control", lifespan=lifespan)
    app.state.engine = engine

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD_PATH.read_text(encoding="utf-8")

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        jobs = engine.load_jobs()
        enabled_count = sum(1 for j in jobs if j.enabled)
        return {
            "scheduler_running": engine.is_running,
            "dry_run": engine.notifier.is_effective_dry_run,
            "total_jobs": len(jobs),
            "enabled_jobs": enabled_count,
            "paused_jobs": len(jobs) - enabled_count,
            "stats": engine.storage.get_summary_stats(),
            "server_time": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        jobs = engine.load_jobs()
        next_runs = engine.get_next_run_times()
        latest_runs = engine.storage.get_latest_runs_by_job()
        price_histories = engine.storage.get_all_price_histories(limit_per_job=30)
        states = engine.storage.get_all_states()

        enriched: list[dict[str, Any]] = []
        for idx, job in enumerate(jobs):
            state = states.get(job.name, {})
            if isinstance(state.get("seen_ids"), list):
                seen_count = len(state["seen_ids"])
            elif "last_price" in state:
                seen_count = len(price_histories.get(job.name, [])) or 1
            else:
                seen_count = 0

            data = job.model_dump()
            data["index"] = idx
            data["next_run_at"] = next_runs.get(job.name) if job.enabled else None
            data["last_run"] = latest_runs.get(job.name)
            data["price_history"] = price_histories.get(job.name, [])
            data["seen_count"] = seen_count
            enriched.append(data)
        return enriched

    @app.post("/api/jobs")
    def add_job(payload: JobPayload) -> dict[str, Any]:
        new_job = JobConfig(
            name=payload.name,
            type=payload.type,
            schedule=payload.schedule,
            enabled=True,
            target_chat_id=payload.target_chat_id or None,
            params=payload.params,
        )
        try:
            created = engine.add_job(new_job)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        next_runs = engine.get_next_run_times()
        return {"status": "ok", "next_run_at": next_runs.get(created.name)}

    @app.post("/api/jobs/reload")
    def reload_jobs() -> dict[str, Any]:
        count = engine.reload_jobs()
        return {"status": "reloaded", "scheduled_jobs": count}

    @app.put("/api/jobs/{index}")
    def update_job(index: int, payload: JobPayload) -> dict[str, Any]:
        candidate = JobConfig(
            name=payload.name,
            type=payload.type,
            schedule=payload.schedule,
            enabled=True,
            target_chat_id=payload.target_chat_id or None,
            params=payload.params,
        )
        try:
            updated = engine.update_job(index, candidate)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        next_runs = engine.get_next_run_times()
        return {"status": "updated", "next_run_at": next_runs.get(updated.name)}

    @app.post("/api/jobs/{index}/toggle")
    def toggle_job(index: int) -> dict[str, Any]:
        try:
            job = engine.toggle_job(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        next_runs = engine.get_next_run_times()
        return {
            "enabled": job.enabled,
            "next_run_at": next_runs.get(job.name) if job.enabled else None,
        }

    @app.delete("/api/jobs/{index}")
    def delete_job(index: int) -> dict[str, str]:
        try:
            engine.delete_job(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return {"status": "deleted"}

    @app.post("/api/jobs/{index}/run")
    def run_job_now(index: int) -> dict[str, Any]:
        try:
            job = engine.get_job(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return engine.execute_job(job, trigger="manual").to_dict()

    @app.post("/api/jobs/{index}/preview")
    def preview_job(index: int, fresh: bool = Query(default=False)) -> dict[str, Any]:
        try:
            job = engine.get_job(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return engine.preview_job(job, fresh=fresh).to_dict()

    @app.get("/api/runs")
    def list_runs(
        limit: int = Query(default=50, ge=1, le=200),
        job_name: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        clean_status = None if not status or status == "all" else status
        return engine.storage.get_runs(
            limit=limit,
            job_name=job_name or None,
            status=clean_status,
        )

    @app.get("/api/jobs/{index}/prices")
    def get_job_prices(
        index: int,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            job = engine.get_job(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return {
            "job_name": job.name,
            "target_price": job.params.get("target_price"),
            "history": engine.storage.get_price_history(job.name, limit=limit),
        }

    return app


app = create_app()
