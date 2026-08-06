"""
Nexus trade tagging — only positions/orders with a nexus comment count toward risk limits.

MT5 comment field is limited (~31 chars on many brokers); keep slugs short.
"""
from __future__ import annotations

import re
from typing import Any

NEXUS_PREFIX = "nexus"
MT5_COMMENT_MAX = 31


def is_nexus_comment(comment: str | None) -> bool:
    """True when the broker comment was opened by Nexus (contains 'nexus')."""
    if not comment:
        return False
    return NEXUS_PREFIX in str(comment).lower()


def build_nexus_comment(strategy: str, *, tag: str = "") -> str:
    """Build comment like ``nexus:genai`` or ``nexus:automl:scalp`` (max 31 chars)."""
    slug = re.sub(r"[^a-z0-9]", "", str(strategy).lower())[:12] or "trade"
    base = f"{NEXUS_PREFIX}:{slug}"
    if tag:
        short = re.sub(r"[^a-z0-9]", "", str(tag).lower())[:8]
        if short:
            base = f"{base}:{short}"
    return base[:MT5_COMMENT_MAX]


def strategy_from_comment(comment: str | None) -> str:
    """Extract the strategy slug from a ``nexus:slug[:tag]`` comment ('' if none)."""
    parts = str(comment or "").split(":")
    if len(parts) > 1 and parts[0].strip().lower() == NEXUS_PREFIX:
        return parts[1].strip()
    return ""


def normalize_nexus_comment(comment: str | None, *, strategy: str = "trade") -> str:
    """Ensure outgoing orders always carry a nexus-prefixed comment."""
    raw = str(comment or "").strip()
    if is_nexus_comment(raw):
        return raw[:MT5_COMMENT_MAX]
    if raw:
        return build_nexus_comment(strategy, tag=raw)
    return build_nexus_comment(strategy)


def filter_nexus_positions(positions: list[dict[str, Any] | Any]) -> list[dict[str, Any]]:
    """Return only open positions whose comment tags them as Nexus-managed."""
    out: list[dict[str, Any]] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        if is_nexus_comment(p.get("comment")):
            out.append(p)
    return out


def partition_positions(
    positions: list[dict[str, Any] | Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (nexus, external) position lists."""
    nexus: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        if is_nexus_comment(p.get("comment")):
            nexus.append(p)
        else:
            external.append(p)
    return nexus, external
