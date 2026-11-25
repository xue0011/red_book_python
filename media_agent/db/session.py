"""数据库会话层（可插拔）——给工作流里 ``get_session()`` / ``PositioningRecord`` 这类写法兜底。

设计原则：
1. **工作流不该关心数据库**：``with get_session() as session: session.add(...); session.commit()``
   这两种写法在"有没有数据库"的情况下都必须成立；
2. **连不上就退化为内存会话**：``DATABASE_URL`` 为空、没装 psycopg、库挂了 —— 只记日志，
   不抛异常、不打断工作流（页面照常出结果，只是没入库）；
3. 表结构跟着代码走：第一次连接时 ``CREATE TABLE IF NOT EXISTS``，不需要手工跑 SQL。

表：
- ``positioning_records``：一次账号定位（用户画像 / 竞品分析 / 定位方案）
- ``hot_topic_records``：一次热点监控（平台 / 赛道 / 原始条数 / 筛选 / 选题建议）
- ``replicate_records``：一次内容复刻（链接 / 本地视频路径 / 译文 / 拆解 / 仿写 / 标题）
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any, Iterator

from config import settings

log = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS positioning_records (
    id                BIGSERIAL PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_name         TEXT,
    user_input        TEXT,
    profile_analysis  TEXT,
    competitors       TEXT,
    positioning_plan  TEXT
);

CREATE TABLE IF NOT EXISTS hot_topic_records (
    id             BIGSERIAL PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    platform       TEXT,
    account_field  TEXT,
    raw_count      INTEGER DEFAULT 0,
    filtered       TEXT,
    suggestions    TEXT
);

CREATE TABLE IF NOT EXISTS replicate_records (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    url           TEXT,
    local_video   TEXT,
    transcript    TEXT,
    breakdown     TEXT,
    rewritten     TEXT,
    titles        TEXT
);
"""


@dataclass
class PositioningRecord:
    """账号定位结果（对应页面的三张卡：画像 / 竞品 / 方案）。"""

    user_name: str = ""
    user_input: str = ""
    profile_analysis: str = ""
    competitors: str = ""
    positioning_plan: str = ""
    id: int | None = None
    table = "positioning_records"


@dataclass
class HotTopicRecord:
    """热点监控结果。"""

    platform: str = ""
    account_field: str = ""
    raw_count: int = 0
    filtered: str = ""
    suggestions: str = ""
    id: int | None = None
    table = "hot_topic_records"


@dataclass
class ReplicateRecord:
    """内容复刻结果（一条视频一行：链接 / 本地视频 / 译文 / 拆解 / 仿写 / 标题）。"""

    url: str = ""
    local_video: str = ""
    transcript: str = ""
    breakdown: str = ""
    rewritten: str = ""
    titles: str = ""
    id: int | None = None
    table = "replicate_records"


class _Session:
    """薄薄一层：``add`` 记待写对象，``commit`` 时才真正落库。"""

    def __init__(self, conn: Any | None) -> None:
        self.conn = conn
        self._pending: list[Any] = []

    def add(self, record: Any) -> None:
        self._pending.append(record)

    def commit(self) -> None:
        if self.conn is None or not self._pending:
            self._pending.clear()
            return
        for record in self._pending:
            self._insert(record)
        self.conn.commit()
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()
        if self.conn is not None:
            try:
                self.conn.rollback()
            except Exception:  # noqa: BLE001
                pass

    #  内部
    def _insert(self, record: Any) -> None:
        table = getattr(record, "table", "")
        names = [f.name for f in fields(record) if f.name != "id"]
        values = [getattr(record, f.name) for f in fields(record) if f.name != "id"]
        placeholders = ", ".join(["%s"] * len(names))
        sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders}) RETURNING id"
        with self.conn.cursor() as cur:
            cur.execute(sql, values)
            row = cur.fetchone()
            if row:
                record.id = row[0]


class _NullSession(_Session):
    """没有数据库时的假会话：调用都成功，什么也不写。"""

    def __init__(self) -> None:
        super().__init__(None)


def _connect():
    """建立连接并确保表存在；任何失败返回 ``None``。"""
    dsn = (settings.DATABASE_URL or "").strip()
    if not dsn:
        return None
    try:
        import psycopg

        conn = psycopg.connect(dsn, connect_timeout=5, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        return conn
    except Exception as exc:  # noqa: BLE001 - 没装驱动/连不上/权限不足
        log.info("数据库不可用，本次结果只留在内存与页面：%s", exc)
        return None


@contextmanager
def get_session() -> Iterator[_Session]:
    """``with get_session() as session: session.add(obj); session.commit()``。"""
    conn = _connect()
    session = _Session(conn) if conn is not None else _NullSession()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        if session.conn is not None:
            try:
                session.conn.close()
            except Exception:  # noqa: BLE001
                pass


def _rows(sql: str, params: tuple = (), limit: int = 1) -> list[dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()][:limit]
    except Exception as exc:  # noqa: BLE001
        log.info("查询失败：%s", exc)
        return []
    finally:
        conn.close()


def latest_positioning() -> dict[str, Any] | None:
    """最近一次账号定位（给热点监控页做上下文）。"""
    rows = _rows("SELECT * FROM positioning_records ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def latest_hot_topic() -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM hot_topic_records ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def latest_replicate() -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM replicate_records ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def save_replicate(
    url: str = "",
    local_video: str = "",
    transcript: str = "",
    breakdown: str = "",
    rewritten: str = "",
    titles: list[str] | str = (),
) -> int | None:
    """内容复刻结果入库；没有数据库时返回 ``None``（页面照常显示）。"""
    if isinstance(titles, (list, tuple)):
        titles_text = "\n".join(str(t) for t in titles if str(t).strip())
    else:
        titles_text = str(titles or "")
    record = ReplicateRecord(
        url=url or "",
        local_video=local_video or "",
        transcript=transcript or "",
        breakdown=breakdown or "",
        rewritten=rewritten or "",
        titles=titles_text,
    )
    return save_record(record)


def health() -> str:
    """给页面显示用：postgres / 未配置 / 连不上。"""
    dsn = (settings.DATABASE_URL or "").strip()
    if not dsn:
        return "未配置 DATABASE_URL"
    conn = _connect()
    if conn is None:
        return f"连不上（{dsn.split('@')[-1]}）"
    conn.close()
    return "postgres 已连接"


def save_record(record: Any) -> int | None:
    """一行写完就提交，返回自增 id（没有数据库则 None）。"""
    with get_session() as session:
        session.add(record)
        session.commit()
    return record.id
