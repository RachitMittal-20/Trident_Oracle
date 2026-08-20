"""Environment-driven configuration for the API."""

import os
from collections.abc import Generator

import psycopg
from storage.base import Storage
from storage.supabase_storage import SupabaseStorage

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def get_connection() -> Generator[psycopg.Connection, None, None]:
    """FastAPI dependency: one connection per request, as app_role, closed
    when the request finishes."""
    conn = psycopg.connect(get_database_url())
    try:
        yield conn
    finally:
        conn.close()


def get_storage() -> Storage:
    base_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not base_url or not service_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must both be set")
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "invoices")
    return SupabaseStorage(base_url, service_key, bucket)
