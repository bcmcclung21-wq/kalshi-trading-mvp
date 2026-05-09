from app.services.liquidity_engine import LiquidityEngine


def test_load_state_missing_table_disables_persistence(monkeypatch):
    engine = LiquidityEngine()

    class FakeInspector:
        def has_table(self, name):
            return False

    class DummySession:
        bind = object()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.services.liquidity_engine.SessionLocal", lambda: DummySession())
    monkeypatch.setattr("app.services.liquidity_engine.inspect", lambda bind: FakeInspector())

    engine.load_state()
    assert engine.persistence_enabled is False
