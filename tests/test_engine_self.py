from eki import engine, queue, train


def _due(monkeypatch):
    monkeypatch.setattr(engine, "_last_self", [0.0])
    monkeypatch.setattr(engine, "refresh_quota", lambda: None)


def test_the_engine_ticks_the_queue_and_the_train_and_an_empty_home_stays_empty(home, conn, monkeypatch):
    _due(monkeypatch)
    called = []
    real_queue, real_train = queue.tick, train.tick
    monkeypatch.setattr(queue, "tick", lambda c: called.append("queue") or real_queue(c))
    monkeypatch.setattr(train, "tick", lambda c, **kw: called.append("train") or real_train(c, **kw))
    engine.tick(conn)
    assert called == ["queue", "train"]
    assert not (home / "self" / "repo").exists()


def test_a_failing_queue_tick_does_not_stop_the_train_or_the_engine(home, conn, monkeypatch):
    _due(monkeypatch)
    called = []

    def boom(c):
        raise RuntimeError("queue broke")
    monkeypatch.setattr(queue, "tick", boom)
    monkeypatch.setattr(train, "tick", lambda c, **kw: called.append("train") or ["train: said"])
    engine.tick(conn)
    assert called == ["train"]
