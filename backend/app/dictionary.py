"""字典：解析、合併、替換。

語法（同 Umless）：
- 每行一個詞，例如 `Kubernetes`：只當辨識提示詞
- `正字 = 誤聽1, 誤聽2`：提示詞＋辨識後把誤聽替換成正字
- 空行與 `#` 開頭的行忽略
- 等號可用半形 `=` 或全形 `＝`；誤聽之間可用 `,`、`，`、`、` 分隔
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

_EQ_RE = re.compile(r"[=＝]")
_SPLIT_RE = re.compile(r"[,，、]")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]")


@dataclass
class Dictionary:
    terms: list[str] = field(default_factory=list)  # 提示詞（含所有正字）
    rules: list[tuple[str, str]] = field(default_factory=list)  # (誤聽, 正字)

    def merge(self, other: "Dictionary") -> "Dictionary":
        """合併兩份字典；同一個誤聽有衝突時以先出現者為準。"""
        terms = list(self.terms)
        for t in other.terms:
            if t not in terms:
                terms.append(t)
        seen = {w.casefold() for w, _ in self.rules}
        rules = list(self.rules)
        for w, c in other.rules:
            if w.casefold() not in seen:
                rules.append((w, c))
                seen.add(w.casefold())
        return Dictionary(terms=terms, rules=rules)


def parse(content: str) -> Dictionary:
    d = Dictionary()
    seen_wrong: set[str] = set()
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = _EQ_RE.split(line, maxsplit=1)
        correct = parts[0].strip()
        if not correct:
            continue
        if correct not in d.terms:
            d.terms.append(correct)
        if len(parts) == 2:
            for wrong in _SPLIT_RE.split(parts[1]):
                wrong = wrong.strip()
                if not wrong or wrong == correct or wrong.casefold() in seen_wrong:
                    continue
                seen_wrong.add(wrong.casefold())
                d.rules.append((wrong, correct))
    return d


def merge_all(dicts: Iterable[Dictionary]) -> Dictionary:
    out = Dictionary()
    for d in dicts:
        out = out.merge(d)
    return out


def _pattern_for(word: str) -> str:
    """英數開頭／結尾的誤聽要求不能緊貼其他英數字，避免把 `cat` 換進 `category`。"""
    p = re.escape(word)
    if _ASCII_WORD.match(word[0]):
        p = r"(?<![A-Za-z0-9])" + p
    if _ASCII_WORD.match(word[-1]):
        p = p + r"(?![A-Za-z0-9])"
    return p


class Replacer:
    """一次掃描完成所有替換（最長的誤聽優先），替換後的正字不會再被其他規則改到。"""

    def __init__(self, rules: list[tuple[str, str]],
                 normalize: Callable[[str], str | Iterable[str]] | None = None):
        mapping: dict[str, str] = {}
        for wrong, correct in rules:
            variants = {wrong}
            if normalize is not None:
                # 也比對轉換成繁體後的誤聽寫法（文字會先經過 OpenCC 再替換）；
                # normalize 可以回傳多種寫法（段落可能用不同的轉換方式）
                out = normalize(wrong)
                variants.update([out] if isinstance(out, str) else out)
            for v in variants:
                key = v.casefold()
                if v and key not in mapping:
                    mapping[key] = correct
        self._mapping = mapping
        if mapping:
            words = sorted(mapping.keys(), key=len, reverse=True)
            self._re: re.Pattern[str] | None = re.compile(
                "|".join(_pattern_for(w) for w in words), re.IGNORECASE
            )
        else:
            self._re = None

    def __call__(self, text: str) -> str:
        if self._re is None or not text:
            return text
        return self._re.sub(lambda m: self._mapping.get(m.group(0).casefold(), m.group(0)), text)


def apply(text: str, d: Dictionary) -> str:
    return Replacer(d.rules)(text)


def hint_terms(d: Dictionary, max_chars: int = 200) -> list[str]:
    """給 ASR 的提示詞；限制總長度，避免 prompt 太長擠掉上下文。"""
    out: list[str] = []
    total = 0
    for t in d.terms:
        if total + len(t) + 1 > max_chars:
            break
        out.append(t)
        total += len(t) + 1
    return out


def add_rule(content: str, wrong: str, correct: str) -> str:
    """把「錯字 → 正字」加進字典內容，盡量併到既有的同一個正字那一行。"""
    wrong = wrong.strip()
    correct = correct.strip()
    if not wrong or not correct or wrong == correct:
        raise ValueError("錯字與正字都要填，且不能相同")
    lines = content.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = _EQ_RE.split(line, maxsplit=1)
        if parts[0].strip() != correct:
            continue
        existing = [w.strip() for w in _SPLIT_RE.split(parts[1])] if len(parts) == 2 else []
        existing = [w for w in existing if w]
        if wrong in existing:
            return content
        existing.append(wrong)
        lines[i] = f"{correct} = {', '.join(existing)}"
        return "\n".join(lines) + ("\n" if content.endswith("\n") else "")
    new_line = f"{correct} = {wrong}"
    if content and not content.endswith("\n"):
        content += "\n"
    return content + new_line + "\n"
