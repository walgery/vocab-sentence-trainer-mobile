"""复习会话：打乱句子、记录进度与掌握度。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .generator import SentenceItem


@dataclass
class ReviewState:
    items: list[SentenceItem]
    order: list[int] = field(default_factory=list)
    cursor: int = 0
    known: set[int] = field(default_factory=set)
    unknown: set[int] = field(default_factory=set)
    show_translation: bool = False

    def __post_init__(self) -> None:
        if not self.order:
            self.order = list(range(len(self.items)))

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def progress(self) -> float:
        """本轮掌握进度：当前轮次卡片中已标记「认识」的比例。"""
        if not self.order:
            return 0.0
        known_in_round = sum(1 for i in self.order if i in self.known)
        return known_in_round / len(self.order)

    def current(self) -> SentenceItem | None:
        if not self.order or self.cursor >= len(self.order):
            return None
        return self.items[self.order[self.cursor]]

    def current_index(self) -> int | None:
        if not self.order or self.cursor >= len(self.order):
            return None
        return self.order[self.cursor]

    def shuffle(self) -> None:
        self.order = list(range(len(self.items)))
        random.shuffle(self.order)
        self.cursor = 0
        self.show_translation = False

    def mark_known(self) -> None:
        idx = self.current_index()
        if idx is None:
            return
        self.known.add(idx)
        self.unknown.discard(idx)
        self._advance()

    def mark_unknown(self) -> None:
        idx = self.current_index()
        if idx is None:
            return
        self.unknown.add(idx)
        self.known.discard(idx)
        self._advance()

    def _advance(self) -> None:
        self.show_translation = False
        self.cursor += 1

    def skip(self) -> None:
        """跳过当前卡片（不记录掌握情况）；最后一张卡跳过后本轮结束。"""
        self.show_translation = False
        if self.cursor < len(self.order):
            self.cursor += 1

    def back(self) -> None:
        self.show_translation = False
        if self.cursor > 0:
            self.cursor -= 1

    def reveal(self) -> None:
        self.show_translation = True

    def restart_unknown_only(self) -> None:
        if not self.unknown:
            self.shuffle()
            return
        self.order = list(self.unknown)
        random.shuffle(self.order)
        self.cursor = 0
        self.unknown = set()
        self.show_translation = False
