from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_url = make_url(settings.database_url)
_is_sqlite = _url.get_backend_name() == "sqlite"

if _is_sqlite and _url.database:
    Path(_url.database).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    _add_missing_columns()
    with engine.begin() as conn:
        # user agent asal-asalan (mis. "string" dari contoh Swagger) dibersihkan
        conn.execute(
            text(
                "UPDATE browser_sessions SET user_agent = NULL "
                "WHERE user_agent IS NOT NULL AND user_agent NOT LIKE 'Mozilla/%'"
            )
        )


def _add_missing_columns() -> None:
    """Migrasi ringan: tambahkan kolom nullable baru ke tabel yang sudah ada."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing or not col.nullable:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}"))
