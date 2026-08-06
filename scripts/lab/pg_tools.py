"""PostgreSQL helpers for lab backup — supports local pg_* tools or Docker."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class PgConn:
    host: str
    port: int
    user: str
    password: str
    database: str
    docker_container: str | None = None


def parse_db_url(url: str) -> PgConn:
    match = re.match(
        r"postgresql\+asyncpg://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<db>[^?]+)",
        url,
    )
    if not match:
        raise ValueError(f"Unsupported DATABASE_URL format: {url}")
    host = match.group("host")
    port = int(match.group("port") or 5432)
    return PgConn(
        host=host,
        port=port,
        user=match.group("user"),
        password=match.group("password"),
        database=match.group("db"),
        docker_container=_docker_container_for_port(port) if host in ("localhost", "127.0.0.1") else None,
    )


def _docker_container_for_port(port: int) -> str | None:
    if not shutil.which("docker"):
        return None
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    needle = f":{port}->"
    for line in result.stdout.splitlines():
        if needle in line:
            return line.split("\t", 1)[0].strip()
    return None


def _pg_env(conn: PgConn) -> dict[str, str]:
    import os

    env = os.environ.copy()
    env["PGPASSWORD"] = conn.password
    return env


def pg_dump(conn: PgConn, database: str | None = None, *, schema: str | None = None) -> bytes:
    db = database or conn.database
    extra: list[str] = []
    if schema:
        extra.extend(["-n", schema])

    if conn.docker_container:
        cmd = [
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={conn.password}",
            conn.docker_container,
            "pg_dump",
            "-U",
            conn.user,
            "-d",
            db,
            "--no-owner",
            "--no-acl",
            *extra,
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode() or "docker pg_dump failed")
        return result.stdout

    if not shutil.which("pg_dump"):
        raise RuntimeError(
            "pg_dump not found on PATH and no Docker PostgreSQL container detected on port "
            f"{conn.port}. Install PostgreSQL client tools or expose Postgres via Docker."
        )
    result = subprocess.run(
        [
            "pg_dump",
            "-h",
            conn.host,
            "-p",
            str(conn.port),
            "-U",
            conn.user,
            "-d",
            db,
            "--no-owner",
            "--no-acl",
            *extra,
        ],
        capture_output=True,
        env=_pg_env(conn),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode() or "pg_dump failed")
    return result.stdout


def psql(conn: PgConn, database: str, sql: str) -> None:
    if conn.docker_container:
        cmd = [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={conn.password}",
            conn.docker_container,
            "psql",
            "-U",
            conn.user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode() or "docker psql failed")
        return

    if not shutil.which("psql"):
        raise RuntimeError("psql not found on PATH")
    result = subprocess.run(
        [
            "psql",
            "-h",
            conn.host,
            "-p",
            str(conn.port),
            "-U",
            conn.user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        capture_output=True,
        env=_pg_env(conn),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode() or "psql failed")


def psql_restore(conn: PgConn, database: str, dump_sql: bytes) -> None:
    if conn.docker_container:
        cmd = [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={conn.password}",
            conn.docker_container,
            "psql",
            "-U",
            conn.user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
        ]
        result = subprocess.run(cmd, input=dump_sql, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode() or "docker psql restore failed")
        return

    if not shutil.which("psql"):
        raise RuntimeError("psql not found on PATH")
    result = subprocess.run(
        [
            "psql",
            "-h",
            conn.host,
            "-p",
            str(conn.port),
            "-U",
            conn.user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=dump_sql,
        capture_output=True,
        env=_pg_env(conn),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode() or "psql restore failed")


def recreate_database(admin: PgConn, db_name: str) -> None:
    psql(
        admin,
        "postgres",
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid();",
    )
    psql(admin, "postgres", f"DROP DATABASE IF EXISTS {db_name};")
    psql(admin, "postgres", f"CREATE DATABASE {db_name} OWNER {admin.user};")


def clone_public_to_schema(conn: PgConn, target_schema: str) -> bytes:
    """Clone public schema into target_schema within the same database (no superuser needed)."""
    print(f"  Cloning schema public -> {target_schema}")
    dump_sql = pg_dump(conn, schema="public")
    text = dump_sql.decode("utf-8", errors="replace")
    text = text.replace("CREATE SCHEMA public;", f"CREATE SCHEMA {target_schema};")
    text = text.replace("SCHEMA public", f"SCHEMA {target_schema}")
    text = text.replace("public.", f"{target_schema}.")
    # pg_dump emits a public schema stub we replaced; drop target first.
    psql(conn, conn.database, f"DROP SCHEMA IF EXISTS {target_schema} CASCADE;")
    psql_restore(conn, conn.database, text.encode("utf-8"))
    return text.encode("utf-8")
