import argparse
import logging
import os
import signal
import threading

from dotenv import load_dotenv
import uvicorn

from src.config import JobConfig
from src.engine import JobRunResult, WatchCastEngine
from src.notifier import TelegramNotifier
from src.storage import StateStorage
from src.web import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_job(job: JobConfig, storage: StateStorage, notifier: TelegramNotifier) -> JobRunResult:
    """Execute a single notification job using the shared WatchCastEngine."""
    engine = WatchCastEngine(storage=storage, notifier=notifier)
    return engine.execute_job(job, trigger="cli")


def _run_headless_scheduler(engine: WatchCastEngine) -> None:
    stop_event = threading.Event()

    def _handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    engine.start()
    logger.info("Headless scheduler started. Press Ctrl+C to exit.")
    try:
        while not stop_event.wait(timeout=1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
        logger.info("Scheduler stopped.")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="WatchCast Notification Scheduler & Mission Control")
    parser.add_argument("--config", default="jobs.yaml", help="Path to jobs.yaml")
    parser.add_argument("--db", default="data/state.db", help="Path to SQLite state database")
    parser.add_argument("--run-once", action="store_true", help="Run all enabled jobs once and exit")
    parser.add_argument("--no-web", action="store_true", help="Run scheduler only without the web dashboard")
    parser.add_argument("--host", default=os.getenv("WATCHCAST_HOST", "127.0.0.1"), help="Web dashboard bind host")
    parser.add_argument("--port", type=int, default=int(os.getenv("WATCHCAST_PORT", "8000")), help="Web dashboard port")
    args = parser.parse_args()

    if args.run_once:
        engine = WatchCastEngine(config_path=args.config, storage=StateStorage(db_path=args.db))
        enabled_jobs = [j for j in engine.load_jobs() if j.enabled]
        if not enabled_jobs:
            logger.warning("No enabled jobs found in %s", args.config)
            return
        for job in enabled_jobs:
            logger.info("Running job: %s (%s)", job.name, job.type)
            engine.execute_job(job, trigger="cli")
        return

    if args.no_web:
        engine = WatchCastEngine(config_path=args.config, storage=StateStorage(db_path=args.db))
        _run_headless_scheduler(engine)
        return

    app = create_app(config_path=args.config, db_path=args.db, start_scheduler=True)
    logger.info("Starting WatchCast Mission Control on http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
