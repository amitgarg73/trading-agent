from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_KEY

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def insert(table: str, data: dict) -> dict:
    return get_client().table(table).insert(data).execute().data[0]


def upsert(table: str, data: dict) -> dict:
    return get_client().table(table).upsert(data).execute().data[0]


def update(table: str, match: dict, data: dict) -> list:
    q = get_client().table(table)
    for col, val in match.items():
        q = q.eq(col, val)
    return q.update(data).execute().data


def select(table: str, filters: dict | None = None, order: str | None = None, limit: int | None = None) -> list:
    q = get_client().table(table).select("*")
    if filters:
        for col, val in filters.items():
            q = q.eq(col, val)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    return q.execute().data
