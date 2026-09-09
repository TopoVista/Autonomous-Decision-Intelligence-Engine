from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import URL

from app.models.connection import DBConnection
from app.models.database import get_sessionmaker
from app.models.query_embedding import QueryEmbedding
from app.models.schema_cache import SchemaCache
from app.models.session import QuerySession
from app.services.encryption_service import EncryptionService
from app.tools.sql_executor import SQLExecutor


class ConnectionService:
    def __init__(self, db=None, encryption: EncryptionService | None = None) -> None:
        self.db = db
        self.encryption = encryption or EncryptionService()
        self.sessionmaker = get_sessionmaker()

    def build_connection_string(self, conn: DBConnection, password: str | None = None) -> str:
        if conn.db_type.lower() in {"postgres", "postgresql"}:
            self._validate_external_host(conn.host)
            query = None
            ssl_mode = (conn.ssl_mode or "").strip().lower()
            if ssl_mode == "prefer":
                # Managed Postgres providers such as Neon reject plaintext
                # probes, so "prefer" is effectively "require" here.
                ssl_mode = "require"
            if ssl_mode and ssl_mode != "disable":
                # asyncpg accepts the parameter name `ssl`, while `sslmode`
                # passed through SQLAlchemy URL parsing can become an
                # unsupported keyword argument at connect time.
                query = {"ssl": ssl_mode}
            url = URL.create(
                "postgresql+asyncpg",
                username=conn.username,
                password=password or self.encryption.decrypt(conn.password_encrypted),
                host=conn.host,
                port=conn.port,
                database=conn.database_name,
                query=query,
            )
            return url.render_as_string(hide_password=False)
        if conn.db_type.lower() == "sqlite":
            return f"sqlite+aiosqlite:///{conn.database_name}"
        raise ValueError(f"Unsupported database type: {conn.db_type}")

    @staticmethod
    def _validate_external_host(host: str) -> None:
        """Reject obvious local/private targets before opening a user-supplied DB URL."""
        clean_host = host.strip().strip("[]")
        if clean_host.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("Database host must be a publicly reachable PostgreSQL host.")
        try:
            address = ipaddress.ip_address(clean_host)
        except ValueError:
            return
        if not address.is_global:
            raise ValueError("Database host must not use a loopback or private IP address.")

    @staticmethod
    def describe_connection_error(error: str) -> str:
        lowered = (error or "").lower()
        if "password authentication failed" in lowered or "invalidpassworderror" in lowered:
            return (
                "Database authentication failed. Verify the username and password for this database. "
                "For Neon, use the database password from the connection details and keep SSL mode set to require."
            )
        if "name or service not known" in lowered or "getaddrinfo failed" in lowered:
            return "Database host could not be resolved. Check the host name and try again."
        if "connection refused" in lowered:
            return "Database connection was refused. Check the host, port, and whether the database is accepting connections."
        if "does not exist" in lowered and "database" in lowered:
            return "Database name was not found. Verify the database name in the connection settings."
        if "timeout" in lowered or "timed out" in lowered:
            return "Database connection timed out. Check network access, firewall rules, and SSL settings."
        if "ssl" in lowered:
            return "Database SSL negotiation failed. For Neon or managed Postgres, use SSL mode 'require'."
        return "Unable to connect to the database. Check the connection details, network access, and SSL setting."

    async def validate_connection_payload(self, conn_data) -> None:
        temp_conn = DBConnection(
            user_id=UUID("00000000-0000-0000-0000-000000000000"),
            name=conn_data.name,
            db_type=conn_data.db_type,
            host=conn_data.host,
            port=conn_data.port,
            database_name=conn_data.database_name,
            username=conn_data.username,
            password_encrypted="",
            ssl_mode=conn_data.ssl_mode,
            is_active=True,
        )
        connection_string = self.build_connection_string(temp_conn, password=conn_data.password)
        result = await SQLExecutor().execute(connection_string, "SELECT 1 AS ok")
        if not result["success"]:
            raise ValueError(self.describe_connection_error(result["error"]))

    async def create_connection(self, user_id, conn_data) -> DBConnection:
        await self.validate_connection_payload(conn_data)
        encrypted = self.encryption.encrypt(conn_data.password)
        user_uuid = UUID(str(user_id))

        if self.db is not None:
            conn = DBConnection(
                user_id=user_uuid,
                name=conn_data.name,
                db_type=conn_data.db_type,
                host=conn_data.host,
                port=conn_data.port,
                database_name=conn_data.database_name,
                username=conn_data.username,
                password_encrypted=encrypted,
                ssl_mode=conn_data.ssl_mode,
                is_active=True,
            )
            self.db.add(conn)
            await self.db.flush()
            await self.db.refresh(conn)
            return conn

        async with self.sessionmaker() as session:
            conn = DBConnection(
                user_id=user_uuid,
                name=conn_data.name,
                db_type=conn_data.db_type,
                host=conn_data.host,
                port=conn_data.port,
                database_name=conn_data.database_name,
                username=conn_data.username,
                password_encrypted=encrypted,
                ssl_mode=conn_data.ssl_mode,
                is_active=True,
            )
            session.add(conn)
            await session.commit()
            await session.refresh(conn)
            return conn

    async def list_connections(self, user_id) -> list[DBConnection]:
        if self.db is not None:
            if user_id is None:
                result = await self.db.execute(select(DBConnection).order_by(DBConnection.created_at.desc()))
            else:
                result = await self.db.execute(
                    select(DBConnection).where(DBConnection.user_id == user_id).order_by(DBConnection.created_at.desc())
                )
            return list(result.scalars().all())

        async with self.sessionmaker() as session:
            if user_id is None:
                result = await session.execute(select(DBConnection).order_by(DBConnection.created_at.desc()))
            else:
                result = await session.execute(
                    select(DBConnection).where(DBConnection.user_id == user_id).order_by(DBConnection.created_at.desc())
                )
            return list(result.scalars().all())

    async def get_connection(self, connection_id, user_id) -> DBConnection | None:
        connection_uuid = UUID(str(connection_id))
        if self.db is not None:
            if user_id is None:
                result = await self.db.execute(select(DBConnection).where(DBConnection.id == connection_uuid))
            else:
                user_uuid = UUID(str(user_id))
                result = await self.db.execute(
                    select(DBConnection).where(DBConnection.id == connection_uuid, DBConnection.user_id == user_uuid)
                )
            return result.scalar_one_or_none()

        async with self.sessionmaker() as session:
            if user_id is None:
                result = await session.execute(select(DBConnection).where(DBConnection.id == connection_uuid))
            else:
                user_uuid = UUID(str(user_id))
                result = await session.execute(
                    select(DBConnection).where(DBConnection.id == connection_uuid, DBConnection.user_id == user_uuid)
                )
            return result.scalar_one_or_none()

    async def get_decrypted_connection_string(self, connection_id, user_id) -> str | None:
        conn = await self.get_connection(connection_id, user_id)
        if conn is None:
            return None
        return self.build_connection_string(conn)

    async def delete_connection(self, connection_id, user_id) -> bool:
        conn = await self.get_connection(connection_id, user_id)
        if conn is None:
            return False

        connection_uuid = UUID(str(connection_id))
        if self.db is not None:
            embeddings = await self.db.execute(select(QueryEmbedding).where(QueryEmbedding.connection_id == connection_uuid))
            for embedding in embeddings.scalars().all():
                await self.db.delete(embedding)

            sessions = await self.db.execute(select(QuerySession).where(QuerySession.connection_id == connection_uuid))
            for query_session in sessions.scalars().all():
                await self.db.delete(query_session)

            cached = await self.db.execute(select(SchemaCache).where(SchemaCache.connection_id == connection_uuid))
            schema_cache = cached.scalar_one_or_none()
            if schema_cache is not None:
                await self.db.delete(schema_cache)

            await self.db.delete(conn)
            await self.db.flush()
            return True

        async with self.sessionmaker() as session:
            result = await session.execute(select(DBConnection).where(DBConnection.id == connection_uuid))
            db_conn = result.scalar_one_or_none()
            if db_conn is None:
                return False

            embeddings = await session.execute(select(QueryEmbedding).where(QueryEmbedding.connection_id == connection_uuid))
            for embedding in embeddings.scalars().all():
                await session.delete(embedding)

            sessions = await session.execute(select(QuerySession).where(QuerySession.connection_id == connection_uuid))
            for query_session in sessions.scalars().all():
                await session.delete(query_session)

            cached = await session.execute(select(SchemaCache).where(SchemaCache.connection_id == connection_uuid))
            schema_cache = cached.scalar_one_or_none()
            if schema_cache is not None:
                await session.delete(schema_cache)

            await session.delete(db_conn)
            await session.commit()
            return True

    async def test_connection(self, connection_id, user_id) -> dict[str, Any]:
        conn = await self.get_connection(connection_id, user_id)
        if conn is None:
            return {"success": False, "message": "Connection not found"}
        connection_string = self.build_connection_string(conn)

        result = await SQLExecutor().execute(connection_string, "SELECT 1 AS ok LIMIT 1")
        if self.db is not None:
            db_conn = await self.db.get(DBConnection, UUID(str(connection_id)))
            if db_conn is not None:
                db_conn.last_tested_at = datetime.now(timezone.utc)
                await self.db.flush()
        else:
            async with self.sessionmaker() as session:
                db_conn = await session.get(DBConnection, UUID(str(connection_id)))
                if db_conn is not None:
                    db_conn.last_tested_at = datetime.now(timezone.utc)
                    await session.commit()
        return {
            "success": result["success"],
            "message": "Connection successful" if result["success"] else self.describe_connection_error(result["error"]),
        }
