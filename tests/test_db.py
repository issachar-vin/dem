from pathlib import Path

from sqlalchemy import text

from conductor.db import create_engine


async def test_sqlite_pragmas_applied(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
    try:
        async with engine.connect() as conn:
            journal = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
            busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
        assert journal == "wal"
        assert fk == 1
        assert busy == 5000
    finally:
        await engine.dispose()
