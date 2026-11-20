from unittest.mock import MagicMock

from src.checkers.cool_fact import CoolFactChecker
from src.checkers.job_feed import JobFeedChecker
from src.checkers.price_drop import PriceDropChecker
from src.storage import StateStorage


def test_storage_roundtrip(tmp_path):
    db_file = tmp_path / "test.db"
    storage = StateStorage(db_path=db_file)

    assert storage.get_state("missing") == {}

    storage.set_state("job1", {"last_price": 49.99})
    assert storage.get_state("job1") == {"last_price": 49.99}


def test_job_feed_deduplicates_seen_ids(monkeypatch):
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "jobs": [
            {"id": 1, "title": "Python Dev", "company_name": "Acme", "url": "https://example.com/1"},
            {"id": 2, "title": "Backend Eng", "company_name": "Beta", "url": "https://example.com/2"},
        ]
    }
    monkeypatch.setattr("src.checkers.job_feed.httpx.get", lambda *a, **kw: fake_response)

    checker = JobFeedChecker()
    result = checker.check({"keyword": "python", "limit": 5}, {"seen_ids": ["remotive:1"]})

    assert result.should_notify is True
    assert "Backend Eng" in result.message
    assert "Python Dev" not in result.message
    assert result.new_state == {"seen_ids": ["remotive:1", "remotive:2"]}


def test_price_drop_alerts_when_below_target(monkeypatch):
    fake_response = MagicMock()
    fake_response.text = '<html><body><span class="price">$85.50</span></body></html>'
    monkeypatch.setattr("src.checkers.price_drop.httpx.get", lambda *a, **kw: fake_response)

    checker = PriceDropChecker()
    params = {"url": "https://example.com/p", "css_selector": ".price", "target_price": 100.0}

    # First time seeing price below target -> notify
    res1 = checker.check(params, {})
    assert res1.should_notify is True
    assert res1.new_state == {"last_price": 85.5}

    # Same price on next check -> do not notify again
    res2 = checker.check(params, res1.new_state)
    assert res2.should_notify is False


def test_cool_fact_fetches_and_deduplicates(monkeypatch):
    responses = [
        {
            "id": "fact-1",
            "text": "Octopuses have three hearts.",
            "source": "djtech.net",
            "permalink": "https://uselessfacts.jsph.pl/api/v2/facts/fact-1",
        },
        {
            "id": "fact-2",
            "text": "Honey never spoils.",
            "source": "djtech.net",
            "permalink": "https://uselessfacts.jsph.pl/api/v2/facts/fact-2",
        },
    ]
    call_idx = {"i": 0}

    def fake_get(*args, **kwargs):
        resp = MagicMock()
        resp.json.return_value = responses[call_idx["i"]]
        call_idx["i"] += 1
        return resp

    monkeypatch.setattr("src.checkers.cool_fact.httpx.get", fake_get)

    checker = CoolFactChecker()
    result = checker.check({"source": "uselessfacts", "language": "en"}, {"seen_ids": ["uselessfacts:fact-1"]})

    assert result.should_notify is True
    assert "Honey never spoils." in result.message
    assert result.new_state == {"seen_ids": ["uselessfacts:fact-1", "uselessfacts:fact-2"]}


