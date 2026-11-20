from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest

from src.checkers.price_drop import PriceDropChecker
from src.config import AppConfig, JobConfig, save_config
from src.engine import WatchCastEngine
from src.notifier import TelegramNotifier
from src.storage import StateStorage
from src.web import create_app


def _make_notifier(dry_run: bool = False) -> TelegramNotifier:
    notifier = TelegramNotifier(bot_token="test-token", default_chat_id="@test", dry_run=dry_run)
    notifier.send = MagicMock()
    return notifier


def test_storage_runs_stats_prices_rename_and_delete(tmp_path):
    storage = StateStorage(db_path=tmp_path / "state.db")

    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(hours=30)).isoformat()
    recent_ts_1 = (now - timedelta(minutes=10)).isoformat()
    recent_ts_2 = (now - timedelta(minutes=5)).isoformat()

    storage.set_state("Watch Product", {"last_price": 120.0})
    storage.record_run("Watch Product", "notified", trigger="scheduled", message="old alert", ran_at=old_ts)
    storage.record_run("Watch Product", "ok", trigger="scheduled", duration_ms=12, ran_at=recent_ts_1)
    storage.record_run("Watch Product", "error", trigger="manual", error="timeout", duration_ms=50, ran_at=recent_ts_2)
    storage.record_run("Job Feed", "notified", trigger="scheduled", message="new job", duration_ms=20, ran_at=recent_ts_2)

    all_runs = storage.get_runs(limit=10)
    assert len(all_runs) == 4
    assert all_runs[0]["ran_at"] == recent_ts_2

    assert len(storage.get_runs(job_name="Watch Product")) == 3
    assert len(storage.get_runs(status="error")) == 1

    latest = storage.get_latest_runs_by_job()
    assert latest["Watch Product"]["status"] == "error"
    assert latest["Job Feed"]["status"] == "notified"

    stats = storage.get_summary_stats()
    assert stats == {"total_runs": 4, "notified_24h": 1, "errors_24h": 1}

    storage.record_price("Watch Product", 150.0, recorded_at=recent_ts_1)
    storage.record_price("Watch Product", 120.0, recorded_at=recent_ts_2)
    prices = storage.get_price_history("Watch Product")
    assert [p["price"] for p in prices] == [150.0, 120.0]
    assert "Watch Product" in storage.get_all_price_histories()

    storage.rename_job("Watch Product", "Watch Product V2")
    assert storage.get_state("Watch Product") == {}
    assert storage.get_state("Watch Product V2") == {"last_price": 120.0}
    assert len(storage.get_runs(job_name="Watch Product V2")) == 3
    assert [p["price"] for p in storage.get_price_history("Watch Product V2")] == [150.0, 120.0]

    storage.delete_job_data("Watch Product V2")
    assert storage.get_state("Watch Product V2") == {}
    assert storage.get_price_history("Watch Product V2") == []
    assert len(storage.get_runs(job_name="Watch Product V2")) == 3


def test_price_drop_raises_on_missing_selector_and_empty_price(monkeypatch):
    fake_response = MagicMock()
    fake_response.text = "<html><body><span class='other'>Free</span></body></html>"
    monkeypatch.setattr("src.checkers.price_drop.httpx.get", lambda *a, **kw: fake_response)

    checker = PriceDropChecker()
    with pytest.raises(ValueError, match="matched no element"):
        checker.check({"url": "https://example.com", "css_selector": ".price", "target_price": 50}, {})

    with pytest.raises(ValueError, match="Could not parse price"):
        checker.check({"url": "https://example.com", "css_selector": ".other", "target_price": 50}, {})


def test_engine_execute_notifier_failure_does_not_overwrite_state(tmp_path, monkeypatch):
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "jobs": [{"id": 99, "title": "Python Lead", "company_name": "Acme", "url": "https://example.com/99"}]
    }
    monkeypatch.setattr("src.checkers.job_feed.httpx.get", lambda *a, **kw: fake_response)

    storage = StateStorage(db_path=tmp_path / "state.db")
    notifier = _make_notifier()
    notifier.send.side_effect = RuntimeError("Telegram API down")

    engine = WatchCastEngine(config_path=tmp_path / "jobs.yaml", storage=storage, notifier=notifier)
    job = JobConfig(name="PyJobs", type="job_feed", schedule="0 * * * *", params={"source": "remotive", "keyword": "python", "limit": 5})

    outcome = engine.execute_job(job, trigger="manual")
    assert outcome.status == "error"
    assert "Telegram API down" in (outcome.error or "")
    assert storage.get_state("PyJobs") == {}

    runs = storage.get_runs(job_name="PyJobs")
    assert len(runs) == 1
    assert runs[0]["status"] == "error"


def test_engine_records_price_above_target_and_preview_is_readonly(tmp_path, monkeypatch):
    fake_response = MagicMock()
    fake_response.text = '<html><body><span class="price">$140.00</span></body></html>'
    monkeypatch.setattr("src.checkers.price_drop.httpx.get", lambda *a, **kw: fake_response)

    storage = StateStorage(db_path=tmp_path / "state.db")
    notifier = _make_notifier()
    engine = WatchCastEngine(config_path=tmp_path / "jobs.yaml", storage=storage, notifier=notifier)

    price_job = JobConfig(
        name="Headphones",
        type="price_drop",
        schedule="*/30 * * * *",
        params={"url": "https://example.com/p", "css_selector": ".price", "target_price": 100.0},
    )

    run_res = engine.execute_job(price_job, trigger="manual")
    assert run_res.status == "ok"
    assert run_res.should_notify is False
    assert notifier.send.call_count == 0
    assert storage.get_state("Headphones") == {"last_price": 140.0}
    assert [p["price"] for p in storage.get_price_history("Headphones")] == [140.0]

    # Preview on job_feed should never notify, save state, or record runs
    feed_resp = MagicMock()
    feed_resp.json.return_value = {
        "jobs": [{"id": 1, "title": "Python Dev", "company_name": "Beta", "url": "https://example.com/1"}]
    }
    monkeypatch.setattr("src.checkers.job_feed.httpx.get", lambda *a, **kw: feed_resp)

    storage.set_state("Feed", {"seen_ids": ["remotive:1"]})
    feed_job = JobConfig(name="Feed", type="job_feed", schedule="0 * * * *", params={"source": "remotive", "keyword": "python", "limit": 5})

    preview_seen = engine.preview_job(feed_job, fresh=False)
    assert preview_seen.status == "ok"
    assert preview_seen.should_notify is False
    assert preview_seen.dry_run is True

    preview_fresh = engine.preview_job(feed_job, fresh=True)
    assert preview_fresh.status == "notified"
    assert preview_fresh.should_notify is True
    assert "Python Dev" in preview_fresh.message
    assert preview_fresh.dry_run is True

    assert notifier.send.call_count == 0
    assert storage.get_state("Feed") == {"seen_ids": ["remotive:1"]}
    assert storage.get_runs(job_name="Feed") == []


def test_mission_control_api_end_to_end(tmp_path, monkeypatch):
    config_file = tmp_path / "jobs.yaml"
    save_config(AppConfig(jobs=[]), config_file)
    db_file = tmp_path / "state.db"
    notifier = _make_notifier()

    app = create_app(config_path=config_file, db_path=db_file, notifier=notifier, start_scheduler=True)

    with TestClient(app) as client:
        html_res = client.get("/")
        assert html_res.status_code == 200
        assert "WatchCast" in html_res.text
        assert "Mission Control" in html_res.text

        status_res = client.get("/api/status")
        assert status_res.status_code == 200
        assert status_res.json()["scheduler_running"] is True
        assert status_res.json()["total_jobs"] == 0

        # Reject invalid cron, unknown type
        bad_cron = client.post(
            "/api/jobs",
            json={"name": "Bad", "type": "job_feed", "schedule": "not a cron", "params": {}},
        )
        assert bad_cron.status_code == 400

        bad_type = client.post(
            "/api/jobs",
            json={"name": "BadType", "type": "unknown", "schedule": "*/30 * * * *", "params": {}},
        )
        assert bad_type.status_code == 400

        # Add valid price_drop job -> hot-reloads scheduler
        add_res = client.post(
            "/api/jobs",
            json={
                "name": "Sony XM5",
                "type": "price_drop",
                "schedule": "*/30 * * * *",
                "params": {"url": "https://example.com/xm5", "css_selector": ".price", "target_price": 300.0},
            },
        )
        assert add_res.status_code == 200
        assert add_res.json()["next_run_at"] is not None

        # Duplicate name rejected
        dup_res = client.post(
            "/api/jobs",
            json={
                "name": "Sony XM5",
                "type": "price_drop",
                "schedule": "*/30 * * * *",
                "params": {"url": "https://example.com/xm5", "css_selector": ".price", "target_price": 300.0},
            },
        )
        assert dup_res.status_code == 400

        # Mock price page at 280.00 -> preview without mutating, then run now
        price_resp = MagicMock()
        price_resp.text = '<html><body><span class="price">$280.00</span></body></html>'
        monkeypatch.setattr("src.checkers.price_drop.httpx.get", lambda *a, **kw: price_resp)

        prev_res = client.post("/api/jobs/0/preview")
        assert prev_res.status_code == 200
        assert prev_res.json()["should_notify"] is True
        assert prev_res.json()["dry_run"] is True
        assert notifier.send.call_count == 0
        assert client.get("/api/runs").json() == []

        run_res = client.post("/api/jobs/0/run")
        assert run_res.status_code == 200
        assert run_res.json()["status"] == "notified"
        assert notifier.send.call_count == 1

        # Second run with lower price builds price history
        price_resp.text = '<html><body><span class="price">$265.00</span></body></html>'
        run_res_2 = client.post("/api/jobs/0/run")
        assert run_res_2.status_code == 200
        assert run_res_2.json()["status"] == "notified"

        jobs_list = client.get("/api/jobs").json()
        assert len(jobs_list) == 1
        assert jobs_list[0]["last_run"]["status"] == "notified"
        assert [p["price"] for p in jobs_list[0]["price_history"]] == [280.0, 265.0]

        prices_res = client.get("/api/jobs/0/prices").json()
        assert [p["price"] for p in prices_res["history"]] == [280.0, 265.0]

        runs_res = client.get("/api/runs?status=notified").json()
        assert len(runs_res) == 2

        # Rename via PUT preserves state and price history
        put_res = client.put(
            "/api/jobs/0",
            json={
                "name": "Sony WH-1000XM5",
                "type": "price_drop",
                "schedule": "*/15 * * * *",
                "params": {"url": "https://example.com/xm5", "css_selector": ".price", "target_price": 300.0},
            },
        )
        assert put_res.status_code == 200
        assert put_res.json()["next_run_at"] is not None
        assert len(client.get("/api/jobs/0/prices").json()["history"]) == 2

        # Toggle pauses and unschedules, then re-enables and reschedules
        tog_off = client.post("/api/jobs/0/toggle").json()
        assert tog_off["enabled"] is False
        assert tog_off["next_run_at"] is None

        tog_on = client.post("/api/jobs/0/toggle").json()
        assert tog_on["enabled"] is True
        assert tog_on["next_run_at"] is not None

        reload_res = client.post("/api/jobs/reload").json()
        assert reload_res == {"status": "reloaded", "scheduled_jobs": 1}

        # Delete removes job and cleans up state/price history
        del_res = client.delete("/api/jobs/0")
        assert del_res.status_code == 200
        assert client.get("/api/jobs").json() == []
        assert client.get("/api/status").json()["total_jobs"] == 0

