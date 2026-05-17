from __future__ import annotations
from typing import Optional
from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_KEY

_client: Optional[Client] = None


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
    q = get_client().table(table).update(data)
    for col, val in match.items():
        q = q.eq(col, val)
    return q.execute().data


def select(table: str, filters: Optional[dict] = None, order: Optional[str] = None, limit: Optional[int] = None) -> list:
    q = get_client().table(table).select("*")
    if filters:
        for col, val in filters.items():
            q = q.eq(col, val)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    return q.execute().data
