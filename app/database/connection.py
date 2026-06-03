import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import OperationalError
from app.core.config import settings

logger = logging.getLogger(__name__)

db_url = settings.DATABASE_URL
engine = None
fallback_to_sqlite = False

if db_url.startswith("postgresql"):
    try:
        # Pre-flight check to see if we can connect to PostgreSQL
        temp_engine = create_engine(db_url, connect_args={"connect_timeout": 1})
        with temp_engine.connect() as conn:
            pass
        engine = create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        print("[DATABASE] Connected to PostgreSQL successfully.")
    except (OperationalError, Exception) as e:
        if "localhost" in db_url or "127.0.0.1" in db_url:
            fallback_to_sqlite = True
            print(f"[DATABASE WARNING] PostgreSQL connection failed. Falling back to local SQLite.")
        else:
            # In production, try to use PostgreSQL anyway with a short timeout
            engine = create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
            print("[DATABASE WARNING] Production PostgreSQL connection failed, but proceeding.")

if not db_url or db_url.startswith("sqlite") or fallback_to_sqlite:
    sqlite_url = "sqlite:///./banking_db.db"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    print(f"[DATABASE] Initialized SQLite engine: {sqlite_url}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

