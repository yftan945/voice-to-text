"""字典資料存取（SQLite）。"""
from __future__ import annotations

import time
from typing import Any

from . import db
from . import dictionary as dict_mod


def _row(r) -> dict[str, Any]:
    parsed = dict_mod.parse(r["content"])
    return {
        "id": r["id"],
        "name": r["name"],
        "content": r["content"],
        "term_count": len(parsed.terms),
        "rule_count": len(parsed.rules),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def list_all() -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM dictionaries ORDER BY name, id").fetchall()
    return [_row(r) for r in rows]


def get(dict_id: int) -> dict[str, Any] | None:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM dictionaries WHERE id = ?", (dict_id,)).fetchone()
    return _row(r) if r else None


def create(name: str, content: str) -> dict[str, Any]:
    now = time.time()
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO dictionaries (name, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, content, now, now),
        )
        new_id = cur.lastrowid
    return get(int(new_id))  # type: ignore[return-value]


def update(dict_id: int, name: str | None = None, content: str | None = None) -> dict[str, Any] | None:
    with db.transaction() as conn:
        r = conn.execute("SELECT * FROM dictionaries WHERE id = ?", (dict_id,)).fetchone()
        if r is None:
            return None
        conn.execute(
            "UPDATE dictionaries SET name = ?, content = ?, updated_at = ? WHERE id = ?",
            (name if name is not None else r["name"], content if content is not None else r["content"],
             time.time(), dict_id),
        )
    return get(dict_id)


def add_rule(dict_id: int, wrong: str, correct: str) -> dict[str, Any] | None:
    """在同一個交易內讀取、加規則、寫回，避免同時編輯時互蓋。"""
    with db.transaction() as conn:
        r = conn.execute("SELECT content FROM dictionaries WHERE id = ?", (dict_id,)).fetchone()
        if r is None:
            return None
        content = dict_mod.add_rule(r["content"], wrong, correct)
        conn.execute(
            "UPDATE dictionaries SET content = ?, updated_at = ? WHERE id = ?", (content, time.time(), dict_id)
        )
    return get(dict_id)


def delete(dict_id: int) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM dictionaries WHERE id = ?", (dict_id,))
    return cur.rowcount > 0


def load_merged(ids: list[int]) -> dict_mod.Dictionary:
    """依使用者選擇的順序合併多份字典；已刪除的字典略過。"""
    parsed = []
    with db.connect() as conn:
        for i in ids:
            r = conn.execute("SELECT content FROM dictionaries WHERE id = ?", (i,)).fetchone()
            if r is not None:
                parsed.append(dict_mod.parse(r["content"]))
    return dict_mod.merge_all(parsed)
