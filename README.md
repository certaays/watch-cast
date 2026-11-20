# WatchCast : A Custom Telegram Notification Scheduler

A modular, config-driven Python scheduler and **Live Mission Control** dashboard that monitors external sources (job boards, RSS feeds, product prices) and sends alerts to Telegram users, groups, or public channels.

## Features
- **Live Mission Control Dashboard:** Single unified process runs both the APScheduler engine and FastAPI web UI (`http://localhost:8000`). Adding, editing, pausing, or deleting a job hot-reloads the live cron schedule immediately.
- **Dry-Run Preview & Run Now:** Safely preview rendered alert output (or test as a first run ignoring seen state) without sending Telegram messages or changing saved state, or trigger any job immediately on demand.
- **Run & Alert History + Health Badges:** SQLite logs every check's timestamp, trigger, duration, rendered alert payload, and error details so broken jobs stand out in the dashboard.
- **Price-History Sparklines:** `price_drop` jobs record checked prices over time and render an inline SVG trend chart with target-price line directly inside each job card.
- **Config-driven & Multi-destination:** Define schedules and targets in `jobs.yaml` using cron expressions and route each job to a personal DM, group, or public `@channel`.
- **Extensible:** Add new checkers by subclassing `BaseChecker` in `src/checkers/`.

## Quick Start

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your TELEGRAM_BOT_TOKEN and DEFAULT_CHAT_ID
   ```

3. Test once in dry-run mode:
   ```bash
   python -m src.main --run-once
   ```

4. Run the unified scheduler & Live Mission Control dashboard (`http://localhost:8000`):
   ```bash
   python -m src.main
   ```

   Optional flags:
   - `--no-web` — run only the headless scheduler without the web server
   - `--host` / `--port` — bind host and port for Mission Control (default `0.0.0.0:8000`, or `WATCHCAST_HOST` / `WATCHCAST_PORT`)
   - `--config` / `--db` — custom paths to `jobs.yaml` and `data/state.db`

## Docker (VPS Deployment)
```bash
docker build -t custom-notifier .
docker run -d --name notifier -p 8000:8000 --env-file .env -v $(pwd)/data:/app/data custom-notifier
```
