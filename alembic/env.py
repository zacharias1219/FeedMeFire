from alembic import context

from app.config import settings
from app.db import make_engine
from app.models import Base

target_metadata = Base.metadata


def run() -> None:
    engine = make_engine(settings.database_url)
    if context.is_offline_mode():
        context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run()
