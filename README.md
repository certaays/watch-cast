# WatchCast : A Custom Telegram Notification Scheduler

A modular, config-driven Python scheduler that monitors external sources (job boards, product prices, etc.) and sends alerts to Telegram users, groups, or public channels.

## Features
- **Config-driven:** Define schedules and targets in `jobs.yaml` using cron expressions.
- **Multi-destination:** Route each job to a personal DM, group, or public `@channel`.
- **Stateful deduplication:** SQLite tracks seen items and previous prices to prevent spam.
- **Extensible:** Add new checkers by subclassing `BaseChecker` in `src/checkers/`.
- **Dry-run mode:** Test locally without sending real Telegram messages.

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

4. Run the scheduler:
   ```bash
   python -m src.main
   ```

## Docker (VPS Deployment)
```bash
docker build -t custom-notifier .
docker run -d --name notifier --env-file .env -v $(pwd)/data:/app/data custom-notifier
```

