"""字典 API：多份字典的新增、編輯、刪除，以及「錯字 → 正字」一鍵加入。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .. import dictionary_store
from ..i18n import ApiError

router = APIRouter(prefix="/api/dictionaries")

MAX_CONTENT = 200_000


class DictBody(BaseModel):
    name: str
    content: str = ""


class DictPatch(BaseModel):
    name: str | None = None
    content: str | None = None


class RuleBody(BaseModel):
    wrong: str
    correct: str


def _check(name: str | None, content: str | None) -> None:
    if name is not None and not name.strip():
        raise ApiError(400, "dictionary.name_required")
    if content is not None and len(content) > MAX_CONTENT:
        raise ApiError(400, "dictionary.content_too_long")


@router.get("")
def list_dicts() -> list[dict[str, Any]]:
    return dictionary_store.list_all()


@router.post("")
def create_dict(body: DictBody) -> dict[str, Any]:
    _check(body.name, body.content)
    return dictionary_store.create(body.name.strip(), body.content)


@router.get("/{dict_id}")
def get_dict(dict_id: int) -> dict[str, Any]:
    d = dictionary_store.get(dict_id)
    if d is None:
        raise ApiError(404, "dictionary.not_found")
    return d


@router.patch("/{dict_id}")
def update_dict(dict_id: int, body: DictPatch) -> dict[str, Any]:
    _check(body.name, body.content)
    d = dictionary_store.update(dict_id, name=body.name.strip() if body.name else None, content=body.content)
    if d is None:
        raise ApiError(404, "dictionary.not_found")
    return d


@router.post("/{dict_id}/rules")
def add_rule(dict_id: int, body: RuleBody) -> dict[str, Any]:
    try:
        d = dictionary_store.add_rule(dict_id, body.wrong, body.correct)
    except ValueError as e:  # dictionary.add_rule 只會因為錯字／正字沒填或相同丟 ValueError
        raise ApiError(400, "dictionary.rule.invalid") from e
    if d is None:
        raise ApiError(404, "dictionary.not_found")
    return d


@router.delete("/{dict_id}")
def delete_dict(dict_id: int) -> dict[str, Any]:
    if not dictionary_store.delete(dict_id):
        raise ApiError(404, "dictionary.not_found")
    return {"deleted": dict_id}
