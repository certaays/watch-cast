import hashlib
import html
from typing import Any, Callable

import httpx

from src.checkers.base import BaseChecker, CheckResult


def _fetch_useless_fact(params: dict[str, Any]) -> dict[str, str]:
    language = params.get("language", "en")
    resp = httpx.get(
        "https://uselessfacts.jsph.pl/api/v2/facts/random",
        params={"language": language},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("text") or "").strip()
    if not text:
        raise ValueError("Empty fact returned from uselessfacts API")
    fact_id = data.get("id") or hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"uselessfacts:{fact_id}",
        "text": text,
        "source": data.get("source") or "Useless Facts",
        "url": data.get("permalink") or data.get("source_url") or "",
    }


def _fetch_cat_fact(params: dict[str, Any]) -> dict[str, str]:
    resp = httpx.get("https://catfact.ninja/fact", timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("fact") or "").strip()
    if not text:
        raise ValueError("Empty fact returned from catfact API")
    fact_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"catfacts:{fact_hash}",
        "text": text,
        "source": "Cat Facts",
        "url": "https://catfact.ninja/",
    }


FACT_SOURCES: dict[str, Callable[[dict[str, Any]], dict[str, str]]] = {
    "uselessfacts": _fetch_useless_fact,
    "catfacts": _fetch_cat_fact,
}


class CoolFactChecker(BaseChecker):
    """Fetches a random interesting fact and avoids repeating recently sent ones."""

    def check(self, params: dict[str, Any], state: dict[str, Any]) -> CheckResult:
        source = params.get("source", "uselessfacts")
        fetcher = FACT_SOURCES.get(source)
        if fetcher is None:
            raise ValueError(f"Unknown cool_fact source: {source!r}")

        seen_ids: list[str] = list(state.get("seen_ids", []))
        seen_set = set(seen_ids)

        fact: dict[str, str] | None = None
        for _ in range(3):
            candidate = fetcher(params)
            if candidate["id"] not in seen_set:
                fact = candidate
                break

        if fact is None:
            return CheckResult(should_notify=False)

        lines = [
            "<b>Cool Fact</b>\n",
            html.escape(fact["text"]),
        ]
        source_label = html.escape(fact["source"])
        if fact["url"]:
            lines.append(f"\n<i>Source: <a href=\"{html.escape(fact['url'])}\">{source_label}</a></i>")
        else:
            lines.append(f"\n<i>Source: {source_label}</i>")

        updated_seen = (seen_ids + [fact["id"]])[-200:]
        return CheckResult(
            should_notify=True,
            message="\n".join(lines),
            new_state={"seen_ids": updated_seen},
        )

