from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.checkers import CHECKER_REGISTRY, get_checker
from src.config import AppConfig, JobConfig, load_config, save_config
from src.notifier import TelegramNotifier
from src.storage import StateStorage

logger = logging.getLogger(__name__)


@dataclass
class JobRunResult:
    """Outcome of executing or previewing a single WatchCast job."""

    job_name: str
    status: str
    should_notify: bool
    message: str = ""
    error: str | None = None
    duration_ms: int = 0
    trigger: str = "manual"
    dry_run: bool = False
    ran_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WatchCastEngine:
    """Unified job config, APScheduler hot-reload, and execution engine."""

    def __init__(
        self,
        config_path: str | Path = "jobs.yaml",
        storage: StateStorage | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.storage = storage or StateStorage()
        self.notifier = notifier or TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            default_chat_id=os.getenv("DEFAULT_CHAT_ID", ""),
            dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
        )
        self.scheduler = BackgroundScheduler()
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        return bool(self.scheduler.running)

    def _load_app_config(self) -> AppConfig:
        if not self.config_path.exists():
            return AppConfig()
        return load_config(self.config_path)

    def start(self) -> None:
        """Start the background scheduler and load jobs from config."""
        with self._lock:
            if not self.scheduler.running:
                self.scheduler.start()
            self.reload_jobs()

    def shutdown(self) -> None:
        """Shut down the background scheduler cleanly."""
        with self._lock:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)

    def validate_job(self, job: JobConfig) -> None:
        """Validate job name, checker type, and cron expression."""
        if not job.name.strip():
            raise ValueError("Job name cannot be empty")
        if job.type not in CHECKER_REGISTRY:
            raise ValueError(f"Unknown checker type: {job.type!r}")
        try:
            CronTrigger.from_crontab(job.schedule)
        except Exception as exc:
            raise ValueError(f"Invalid cron schedule {job.schedule!r}: {exc}") from exc

    def reload_jobs(self) -> int:
        """Reload all enabled jobs from jobs.yaml into the live scheduler."""
        with self._lock:
            config = self._load_app_config()
            if self.scheduler.running:
                self.scheduler.remove_all_jobs()

            scheduled = 0
            for job in config.jobs:
                if not job.enabled:
                    continue
                try:
                    trigger = CronTrigger.from_crontab(job.schedule)
                except Exception:
                    logger.exception("Invalid cron schedule for job %s: %r", job.name, job.schedule)
                    continue

                if self.scheduler.running:
                    self.scheduler.add_job(
                        self._run_scheduled_job,
                        trigger=trigger,
                        args=[job.name],
                        id=job.name,
                        name=job.name,
                        replace_existing=True,
                        max_instances=1,
                        coalesce=True,
                        misfire_grace_time=60,
                    )
                    logger.info("Scheduled %s with cron '%s'", job.name, job.schedule)
                scheduled += 1
            return scheduled

    def get_next_run_times(self) -> dict[str, str | None]:
        """Return ISO-8601 next scheduled run times keyed by job name."""
        with self._lock:
            if not self.scheduler.running:
                return {}
            return {
                job.id: (job.next_run_time.isoformat() if job.next_run_time else None)
                for job in self.scheduler.get_jobs()
            }

    def load_jobs(self) -> list[JobConfig]:
        """Return all configured jobs in file order."""
        with self._lock:
            return list(self._load_app_config().jobs)

    def get_job(self, index: int) -> JobConfig:
        """Return a single configured job by index."""
        with self._lock:
            jobs = self._load_app_config().jobs
            if index < 0 or index >= len(jobs):
                raise IndexError(f"Job index {index} out of range")
            return jobs[index]

    def add_job(self, job: JobConfig) -> JobConfig:
        """Validate, persist, and hot-reload a newly added job."""
        with self._lock:
            self.validate_job(job)
            config = self._load_app_config()
            if any(existing.name == job.name for existing in config.jobs):
                raise ValueError(f"A job named {job.name!r} already exists")
            config.jobs.append(job)
            save_config(config, self.config_path)
            if self.is_running:
                self.reload_jobs()
            return job

    def update_job(self, index: int, job: JobConfig) -> JobConfig:
        """Validate, update, migrate storage if renamed, and hot-reload a job."""
        with self._lock:
            config = self._load_app_config()
            if index < 0 or index >= len(config.jobs):
                raise IndexError(f"Job index {index} out of range")
            self.validate_job(job)
            if any(
                i != index and existing.name == job.name
                for i, existing in enumerate(config.jobs)
            ):
                raise ValueError(f"A job named {job.name!r} already exists")

            existing = config.jobs[index]
            updated = JobConfig(
                name=job.name,
                type=job.type,
                schedule=job.schedule,
                enabled=existing.enabled,
                target_chat_id=job.target_chat_id or None,
                params=job.params,
            )
            config.jobs[index] = updated
            save_config(config, self.config_path)
            if existing.name != updated.name:
                self.storage.rename_job(existing.name, updated.name)
            if self.is_running:
                self.reload_jobs()
            return updated

    def toggle_job(self, index: int) -> JobConfig:
        """Toggle a job's enabled flag, persist, and hot-reload the scheduler."""
        with self._lock:
            config = self._load_app_config()
            if index < 0 or index >= len(config.jobs):
                raise IndexError(f"Job index {index} out of range")
            config.jobs[index].enabled = not config.jobs[index].enabled
            save_config(config, self.config_path)
            if self.is_running:
                self.reload_jobs()
            return config.jobs[index]

    def delete_job(self, index: int) -> JobConfig:
        """Remove a job from config, clean up its state/prices, and hot-reload."""
        with self._lock:
            config = self._load_app_config()
            if index < 0 or index >= len(config.jobs):
                raise IndexError(f"Job index {index} out of range")
            removed = config.jobs.pop(index)
            save_config(config, self.config_path)
            self.storage.delete_job_data(removed.name)
            if self.is_running:
                self.reload_jobs()
            return removed

    def execute_job(self, job: JobConfig, *, trigger: str = "manual") -> JobRunResult:
        """Run a job, send notifications first, persist state/history, and log the run."""
        start = time.perf_counter()
        ran_at = datetime.now(timezone.utc).isoformat()
        try:
            checker = get_checker(job.type)
            state = self.storage.get_state(job.name)
            result = checker.check(job.params, state)
            should_notify = bool(result.should_notify and result.message)

            if should_notify:
                self.notifier.send(result.message, chat_id=job.target_chat_id)

            if result.new_state is not None:
                self.storage.set_state(job.name, result.new_state)

            if (
                job.type == "price_drop"
                and result.new_state is not None
                and "last_price" in result.new_state
            ):
                self.storage.record_price(
                    job.name,
                    float(result.new_state["last_price"]),
                    recorded_at=ran_at,
                )

            duration_ms = int((time.perf_counter() - start) * 1000)
            status = "notified" if should_notify else "ok"
            metadata: dict[str, Any] = {}
            if result.new_state and "last_price" in result.new_state:
                metadata["current_price"] = result.new_state["last_price"]

            self.storage.record_run(
                job.name,
                status,
                trigger=trigger,
                message=result.message if should_notify else "",
                error=None,
                duration_ms=duration_ms,
                ran_at=ran_at,
            )
            return JobRunResult(
                job_name=job.name,
                status=status,
                should_notify=should_notify,
                message=result.message if should_notify else "",
                error=None,
                duration_ms=duration_ms,
                trigger=trigger,
                dry_run=self.notifier.is_effective_dry_run,
                ran_at=ran_at,
                metadata=metadata,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception("Job %s failed", job.name)
            self.storage.record_run(
                job.name,
                "error",
                trigger=trigger,
                error=str(exc),
                duration_ms=duration_ms,
                ran_at=ran_at,
            )
            return JobRunResult(
                job_name=job.name,
                status="error",
                should_notify=False,
                message="",
                error=str(exc),
                duration_ms=duration_ms,
                trigger=trigger,
                dry_run=self.notifier.is_effective_dry_run,
                ran_at=ran_at,
            )

    def preview_job(self, job: JobConfig, *, fresh: bool = False) -> JobRunResult:
        """Run a checker in read-only dry-run mode without sending or saving state."""
        start = time.perf_counter()
        ran_at = datetime.now(timezone.utc).isoformat()
        try:
            checker = get_checker(job.type)
            state = {} if fresh else self.storage.get_state(job.name)
            result = checker.check(job.params, state)
            should_notify = bool(result.should_notify and result.message)
            duration_ms = int((time.perf_counter() - start) * 1000)
            metadata: dict[str, Any] = {}
            if result.new_state and "last_price" in result.new_state:
                metadata["current_price"] = result.new_state["last_price"]

            return JobRunResult(
                job_name=job.name,
                status="notified" if should_notify else "ok",
                should_notify=should_notify,
                message=result.message if should_notify else "",
                error=None,
                duration_ms=duration_ms,
                trigger="preview",
                dry_run=True,
                ran_at=ran_at,
                metadata=metadata,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return JobRunResult(
                job_name=job.name,
                status="error",
                should_notify=False,
                message="",
                error=str(exc),
                duration_ms=duration_ms,
                trigger="preview",
                dry_run=True,
                ran_at=ran_at,
            )

    def _run_scheduled_job(self, job_name: str) -> None:
        with self._lock:
            jobs = self._load_app_config().jobs
            job = next((j for j in jobs if j.name == job_name and j.enabled), None)
        if job is not None:
            self.execute_job(job, trigger="scheduled")

