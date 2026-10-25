import time
from uuid import uuid4

from sqlalchemy import literal


def now():
    return time.time_ns() // 1_000_000


def identifier():
    return str(uuid4())


async def insert_once(db, table, values, keys):
    dialect = db.bind.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError('OI persistence requires PostgreSQL or SQLite')
    result = await db.execute(
        insert(table).values(**values).on_conflict_do_nothing(index_elements=keys).returning(literal(1))
    )
    # psycopg async can report -1 even after a successful INSERT. The returned
    # row is authoritative for both PostgreSQL and SQLite, including conflicts.
    return result.scalar_one_or_none() is not None
