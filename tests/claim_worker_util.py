"""Hilfsfunktion fuer tests/test_processing_claims.py: laeuft in einem EIGENEN Prozess (multiprocessing, spawn)."""

from __future__ import annotations


def claim_in_process(db_url: str, message_id: str, start_event, results) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.chat import processing_claims

    engine = create_engine(db_url, connect_args={"timeout": 30})
    session = sessionmaker(bind=engine)()
    start_event.wait(30)
    try:
        results.put(processing_claims.claim(session, message_id))
    except Exception as exc:  # noqa: BLE001 - Fehler zaehlen als "nicht erhalten" (fail-closed)
        results.put(f"error:{type(exc).__name__}")
    finally:
        session.close()
        engine.dispose()
