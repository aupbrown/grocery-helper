from app.storage import init_db, log_event, get_stats


def test_log_and_stats(tmp_path):
    db = tmp_path / "events.db"
    init_db(db)
    log_event(db, "plan_generated")
    log_event(db, "plan_generated")
    log_event(db, "email_captured", email="a@b.com")
    stats = get_stats(db)
    assert stats["plans_generated"] == 2
    assert stats["emails_captured"] == 1
    assert stats["conversion"] == 0.5


def test_get_stats_empty(tmp_path):
    db = tmp_path / "events.db"
    init_db(db)
    stats = get_stats(db)
    assert stats["plans_generated"] == 0
    assert stats["conversion"] == 0.0
