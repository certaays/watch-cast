import html
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.checkers.base import BaseChecker, CheckResult


def _parse_price(raw_text: str) -> float:
    cleaned = re.sub(r"[^\d.,]", "", raw_text).replace(",", "")
    if not cleaned:
        raise ValueError(f"Could not parse price from text: {raw_text!r}")
    return float(cleaned)


class PriceDropChecker(BaseChecker):
    """Scrapes a product page for a price and alerts when it drops below target."""

    def check(self, params: dict[str, Any], state: dict[str, Any]) -> CheckResult:
        url = params["url"]
        selector = params["css_selector"]
        target_price = float(params["target_price"])
        last_price = state.get("last_price")

        response = httpx.get(url, timeout=15.0, follow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        element = soup.select_one(selector)
        if element is None:
            raise ValueError(f"CSS selector {selector!r} matched no element")

        current_price = _parse_price(element.get_text(strip=True))
        should_alert = current_price <= target_price and (
            last_price is None or current_price < float(last_price)
        )

        if not should_alert:
            return CheckResult(
                should_notify=False,
                new_state={"last_price": current_price},
            )

        message = (
            f"<b>Price Drop Alert!</b>\n"
            f"Current price: <b>{current_price:.2f}</b> "
            f"(target: {target_price:.2f})\n"
            f"<a href=\"{html.escape(url)}\">View Product</a>"
        )
        return CheckResult(
            should_notify=True,
            message=message,
            new_state={"last_price": current_price},
        )

