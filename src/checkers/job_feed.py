import html
import os
from typing import Any, Callable

import httpx

from src.checkers.base import BaseChecker, CheckResult


def _fetch_remotive(keyword: str, limit: int, params: dict[str, Any]) -> list[dict[str, str]]:
    resp = httpx.get(
        "https://remotive.com/api/remote-jobs",
        params={"search": keyword, "limit": limit},
        timeout=15.0,
    )
    resp.raise_for_status()
    return [
        {
            "id": f"remotive:{j['id']}",
            "title": j["title"],
            "company": j["company_name"],
            "url": j["url"],
        }
        for j in resp.json().get("jobs", [])[:limit]
    ]


def _fetch_arbeitnow(keyword: str, limit: int, params: dict[str, Any]) -> list[dict[str, str]]:
    resp = httpx.get("https://www.arbeitnow.com/api/job-board-api", timeout=15.0)
    resp.raise_for_status()
    kw = keyword.lower()
    jobs = [
        j for j in resp.json().get("data", [])
        if not kw or kw in j.get("title", "").lower() or kw in " ".join(j.get("tags", [])).lower()
    ][:limit]
    return [
        {
            "id": f"arbeitnow:{j['slug']}",
            "title": j["title"],
            "company": j["company_name"],
            "url": j["url"],
        }
        for j in jobs
    ]


def _fetch_remoteok(keyword: str, limit: int, params: dict[str, Any]) -> list[dict[str, str]]:
    resp = httpx.get(
        "https://remoteok.com/api",
        headers={"User-Agent": "custom-notification-bot/1.0"},
        timeout=15.0,
    )
    resp.raise_for_status()
    kw = keyword.lower()
    raw = [j for j in resp.json() if isinstance(j, dict) and j.get("id")]
    filtered = [
        j for j in raw
        if not kw or kw in j.get("position", "").lower() or kw in " ".join(j.get("tags", [])).lower()
    ][:limit]
    return [
        {
            "id": f"remoteok:{j['id']}",
            "title": j.get("position", "Untitled"),
            "company": j.get("company", "Unknown"),
            "url": j.get("url", ""),
        }
        for j in filtered
    ]


def _fetch_jobicy(keyword: str, limit: int, params: dict[str, Any]) -> list[dict[str, str]]:
    query: dict[str, Any] = {"count": limit}
    if keyword:
        query["tag"] = keyword
    resp = httpx.get("https://jobicy.com/api/v2/remote-jobs", params=query, timeout=15.0)
    resp.raise_for_status()
    return [
        {
            "id": f"jobicy:{j['id']}",
            "title": j["jobTitle"],
            "company": j["companyName"],
            "url": j["url"],
        }
        for j in resp.json().get("jobs", [])[:limit]
    ]


def _fetch_himalayas(keyword: str, limit: int, params: dict[str, Any]) -> list[dict[str, str]]:
    resp = httpx.get(
        "https://himalayas.app/jobs/api",
        params={"limit": limit},
        timeout=15.0,
    )
    resp.raise_for_status()
    kw = keyword.lower()
    jobs = [
        j for j in resp.json().get("jobs", [])
        if not kw or kw in j.get("title", "").lower()
    ][:limit]
    return [
        {
            "id": f"himalayas:{j.get('guid') or j.get('applicationLink')}",
            "title": j.get("title", "Untitled"),
            "company": j.get("companyName", "Unknown"),
            "url": j.get("applicationLink", ""),
        }
        for j in jobs
    ]


def _fetch_jsearch(keyword: str, limit: int, params: dict[str, Any]) -> list[dict[str, str]]:
    api_key = os.getenv("JSEARCH_API_KEY") or os.getenv("RAPIDAPI_KEY", "")
    if not api_key:
        raise ValueError("JSEARCH_API_KEY is required for jsearch source")
    country = params.get("country", "id")
    date_posted = params.get("date_posted", "today")
    resp = httpx.get(
        "https://api.openwebninja.com/jsearch/search",
        headers={"x-api-key": api_key},
        params={
            "query": keyword,
            "country": country,
            "date_posted": date_posted,
            "num_pages": "1",
        },
        timeout=20.0,
    )
    resp.raise_for_status()
    return [
        {
            "id": f"jsearch:{j['job_id']}",
            "title": j.get("job_title", "Untitled"),
            "company": j.get("employer_name", "Unknown"),
            "url": j.get("job_apply_link", ""),
        }
        for j in resp.json().get("data", [])[:limit]
    ]


def _fetch_adzuna(keyword: str, limit: int, params: dict[str, Any]) -> list[dict[str, str]]:
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        raise ValueError("ADZUNA_APP_ID and ADZUNA_APP_KEY are required for adzuna source")
    country = params.get("country", "sg")
    resp = httpx.get(
        f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
        params={
            "app_id": app_id,
            "app_key": app_key,
            "what": keyword,
            "results_per_page": limit,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    return [
        {
            "id": f"adzuna:{j['id']}",
            "title": j.get("title", "Untitled"),
            "company": j.get("company", {}).get("display_name", "Unknown"),
            "url": j.get("redirect_url", ""),
        }
        for j in resp.json().get("results", [])[:limit]
    ]


SOURCE_FETCHERS: dict[str, Callable[[str, int, dict[str, Any]], list[dict[str, str]]]] = {
    "remotive": _fetch_remotive,
    "arbeitnow": _fetch_arbeitnow,
    "remoteok": _fetch_remoteok,
    "jobicy": _fetch_jobicy,
    "himalayas": _fetch_himalayas,
    "jsearch": _fetch_jsearch,
    "adzuna": _fetch_adzuna,
}


class JobFeedChecker(BaseChecker):
    """Fetches jobs from configurable sources and alerts on new listings."""

    def check(self, params: dict[str, Any], state: dict[str, Any]) -> CheckResult:
        source = params.get("source", "remotive")
        keyword = params.get("keyword", "")
        limit = int(params.get("limit", 5))
        seen_ids: list[str] = state.get("seen_ids", [])

        fetcher = SOURCE_FETCHERS.get(source)
        if fetcher is None:
            raise ValueError(f"Unknown job_feed source: {source!r}")

        jobs = fetcher(keyword, limit, params)
        new_jobs = [job for job in jobs if job["id"] not in seen_ids]
        if not new_jobs:
            return CheckResult(should_notify=False)

        label = html.escape(f"{source.title()} - {keyword or 'All'}")
        lines = [f"<b>New Jobs ({label})</b>\n"]
        for job in new_jobs:
            title = html.escape(job["title"])
            company = html.escape(job["company"])
            url = job["url"]
            lines.append(f"• <a href=\"{url}\">{title}</a> — {company}")

        updated_seen = (seen_ids + [j["id"] for j in new_jobs])[-200:]
        return CheckResult(
            should_notify=True,
            message="\n".join(lines),
            new_state={"seen_ids": updated_seen},
        )
