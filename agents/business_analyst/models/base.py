from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """BA's own declarative base — deliberately NOT models.base.Base. Alembic's env.py points
    target_metadata at this Base only, so autogenerate can never see (and can never emit a
    spurious CREATE TABLE for) any non-ba_* table, even if one is added later without a
    matching Alembic revision. Root's other tables keep being created via create_all(),
    untouched by this Alembic setup."""
