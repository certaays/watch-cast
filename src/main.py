import argparse
import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from src.checkers import get_checker
from src.config import JobConfig, load_config
from src.notifier import TelegramNotifier
from src.storage import StateStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_job(job: JobConfig, storage: StateStorage, notifier: TelegramNotifier) -> None:
    """Execute a single notification job."""
    logger.info("Running job: %s (%s)", job.name, job.type)
    try:
        checker = get_checker(job.type)
        state = storage.get_state(job.name)
        result = checker.check(job.params, state)

        if result.new_state is not None:
            storage.set_state(job.name, result.new_state)

        if result.should_notify and result.message:
            notifier.send(result.message, chat_id=job.target_chat_id)
        else:
            logger.info("No notification needed for %s", job.name)
    except Exception:
        logger.exception("Job %s failed", job.name)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Custom Telegram Notification Scheduler")
    parser.add_argument("--config", default="jobs.yaml", help="Path to jobs.yaml")
    parser.add_argument("--run-once", action="store_true", help="Run all enabled jobs once and exit")
    args = parser.parse_args()

    config = load_config(args.config)
    storage = StateStorage()
    notifier = TelegramNotifier(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        default_chat_id=os.getenv("DEFAULT_CHAT_ID", ""),
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
    )

    enabled_jobs = [j for j in config.jobs if j.enabled]
    if not enabled_jobs:
        logger.warning("No enabled jobs found in %s", args.config)
        return

    if args.run_once:
        for job in enabled_jobs:
            run_job(job, storage, notifier)
        return

    scheduler = BlockingScheduler()
    for job in enabled_jobs:
        trigger = CronTrigger.from_crontab(job.schedule)
        scheduler.add_job(
            run_job,
            trigger=trigger,
            args=[job, storage, notifier],
            id=job.name,
            name=job.name,
        )
        logger.info("Scheduled %s with cron '%s'", job.name, job.schedule)

    logger.info("Scheduler started. Press Ctrl+C to exit.")
    scheduler.start()


if __name__ == "__main__":
    main()

