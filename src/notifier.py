import logging

import httpx

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends formatted notifications to a Telegram user, group, or channel."""

    def __init__(
        self,
        bot_token: str,
        default_chat_id: str,
        dry_run: bool = False,
    ) -> None:
        self.bot_token = bot_token
        self.default_chat_id = default_chat_id
        self.dry_run = dry_run
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, message: str, chat_id: str | None = None) -> None:
        """Send a Markdown-formatted message to the target Telegram chat."""
        target = chat_id or self.default_chat_id
        if self.dry_run or not self.bot_token:
            logger.info("[DRY RUN] Would send to %s:\n%s", target, message)
            return

        transport = httpx.HTTPTransport(local_address="0.0.0.0")
        with httpx.Client(transport=transport, timeout=15.0) as client:
            response = client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": target,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        response.raise_for_status()
        logger.info("Sent notification to %s", target)

