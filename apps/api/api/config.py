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


def get_approval_redeemer_database_url() -> str:
    url = os.environ.get("APPROVAL_REDEEMER_DATABASE_URL")
    if not url:
        raise RuntimeError("APPROVAL_REDEEMER_DATABASE_URL is not set")
    return url


def get_approval_redeemer_connection() -> Generator[psycopg.Connection, None, None]:
    """FastAPI dependency for the approval endpoints (api/main.py): one
    connection per request, as approval_redeemer -- NOT a BYPASSRLS role,
    same grants and tenant_isolation exposure as app_role on
    approval_requests/invoices/match_exceptions/jobs/audit_log, plus one
    narrow additional permissive SELECT policy on approval_requests alone
    (see db/migrations/0019_approval_redeemer_role.sql and db/README.md's
    "Security model" section) -- closed when the request finishes. Never
    the same role as get_connection's app_role -- redeeming a token looks
    approval_requests up by an opaque token before any tenant_id is known,
    which app_role's ordinary RLS policies structurally cannot do.
    """
    conn = psycopg.connect(get_approval_redeemer_database_url())
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
