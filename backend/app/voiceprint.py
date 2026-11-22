"""聲紋庫：資料存取與比對（比對邏輯是純 Python，可單元測試）。"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Sequence

from . import db


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


def match(
    speaker_embeddings: dict[str, Sequence[float]],
    voiceprints: Sequence[dict[str, Any]],
    threshold: float,
) -> dict[str, dict[str, Any]]:
    """講者 ↔ 聲紋一對一配對：相似度由高到低貪婪配對，低於門檻不配。

    回傳 {講者 label: {"voiceprint_id", "name", "similarity"}}。
    """
    pairs: list[tuple[float, str, dict[str, Any]]] = []
    for label, emb in speaker_embeddings.items():
        for vp in voiceprints:
            sim = cosine(emb, vp["embedding"])
            if sim >= threshold:
                pairs.append((sim, label, vp))
    pairs.sort(key=lambda p: p[0], reverse=True)
    result: dict[str, dict[str, Any]] = {}
    used_vp: set[int] = set()
    for sim, label, vp in pairs:
        if label in result or vp["id"] in used_vp:
            continue
        result[label] = {"voiceprint_id": vp["id"], "name": vp["name"], "similarity": round(sim, 4)}
        used_vp.add(vp["id"])
    return result


# ---- 資料存取 ----


def _row(r) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "embedding": json.loads(r["embedding"]),
        "embedding_model": r["embedding_model"],
        "source": r["source"],
        "duration": r["duration"],
        "created_at": r["created_at"],
    }


def list_all(embedding_model: str | None = None) -> list[dict[str, Any]]:
    with db.connect() as conn:
        if embedding_model:
            rows = conn.execute(
                "SELECT * FROM voiceprints WHERE embedding_model = ? ORDER BY name, id", (embedding_model,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM voiceprints ORDER BY name, id").fetchall()
    return [_row(r) for r in rows]


def _insert(conn, name: str, embedding: Sequence[float], embedding_model: str, source: str,
            duration: float | None) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO voiceprints (name, embedding, embedding_model, source, duration, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name, json.dumps([float(x) for x in embedding]), embedding_model, source, duration, time.time()),
    )
    row = conn.execute("SELECT * FROM voiceprints WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row(row)


def create(name: str, embedding: Sequence[float], embedding_model: str, source: str,
           duration: float | None, conn=None) -> dict[str, Any]:
    """新增聲紋；傳入 conn 時在呼叫端的交易裡寫入。"""
    if conn is not None:
        return _insert(conn, name, embedding, embedding_model, source, duration)
    with db.connect() as c:
        return _insert(c, name, embedding, embedding_model, source, duration)


def get(vp_id: int, conn=None) -> dict[str, Any] | None:
    if conn is not None:
        r = conn.execute("SELECT * FROM voiceprints WHERE id = ?", (vp_id,)).fetchone()
        return _row(r) if r else None
    with db.connect() as c:
        return get(vp_id, c)


def update_embedding(vp_id: int, name: str, embedding: Sequence[float], embedding_model: str, source: str,
                     duration: float | None, conn=None) -> dict[str, Any] | None:
    """用新的樣本更新既有聲紋（同一個人，不新增一筆）；找不到回傳 None。"""
    def run(c) -> dict[str, Any] | None:
        cur = c.execute(
            "UPDATE voiceprints SET name = ?, embedding = ?, embedding_model = ?, source = ?, duration = ?"
            " WHERE id = ?",
            (name, json.dumps([float(x) for x in embedding]), embedding_model, source, duration, vp_id),
        )
        if cur.rowcount == 0:
            return None
        return get(vp_id, c)

    if conn is not None:
        return run(conn)
    with db.connect() as c:
        return run(c)


def rename(vp_id: int, name: str) -> bool:
    with db.connect() as conn:
        cur = conn.execute("UPDATE voiceprints SET name = ? WHERE id = ?", (name, vp_id))
    return cur.rowcount > 0


def delete(vp_id: int) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM voiceprints WHERE id = ?", (vp_id,))
    return cur.rowcount > 0
