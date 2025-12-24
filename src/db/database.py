# db/database.py
from pathlib import Path
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# project root = trading-bot/
BASE_DIR = Path(__file__).resolve().parents[2]

# ✅ Allow overriding DB location (Docker-friendly)
# Example in Docker: DB_PATH=/app/data/trading_bot.db
db_path_env = os.getenv("DB_PATH")

if db_path_env:
    DB_PATH = Path(db_path_env)
else:
    # local default (same as before)
    DB_PATH = BASE_DIR / "trading_bot.db"

# ✅ ensure parent dir exists (important for /app/data)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        # ✅ sqlite busy timeout at driver level (seconds)
        "timeout": float(os.getenv("SQLITE_TIMEOUT", "30")),
    },
)

# ✅ Ensure SQLite pragmas for each new DB connection
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()

    # Foreign keys
    cursor.execute("PRAGMA foreign_keys=ON;")

    # ✅ better concurrency for multi-process (api + worker)
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass

    # ✅ wait instead of instantly failing on lock
    busy_ms = int(float(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000")))
    cursor.execute(f"PRAGMA busy_timeout={busy_ms};")

    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
