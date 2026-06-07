"""Database session management."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings
from src.database.models import Base


def create_db_engine(settings: Settings):
    return create_engine(
        settings.database.url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def create_session_factory(settings: Settings) -> sessionmaker:
    engine = create_db_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db(settings: Settings) -> None:
    engine = create_db_engine(settings)
    Base.metadata.create_all(bind=engine)


def get_db_session(session_factory: sessionmaker) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
