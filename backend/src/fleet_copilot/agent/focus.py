from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from fleet_copilot.agent import texts
from fleet_copilot.agent.format import noun
from fleet_copilot.agent.llm import Reference
from fleet_copilot.contracts import EntityList
from fleet_copilot.repository import Device

_MAX_OPTIONS = 6


@dataclass(frozen=True)
class Resolved:
    """The tenant devices a follow-up refers to, in the order they were shown."""

    device_ids: tuple[str, ...]
    list_id: str | None


@dataclass(frozen=True)
class NeedsClarification:
    """Code-written question: the reference cannot be resolved without asking."""

    question: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class NotFoundRef:
    """The named device is not one of this tenant's devices (foreign and nonexistent look the same)."""


Resolution = Resolved | NeedsClarification | NotFoundRef


def focus_summary(focus: Sequence[EntityList]) -> list[str]:
    """One line per list shown last turn, for the planner: titles and sizes, never device ids."""
    return [
        f'{number}. "{entity_list.title}" ({len(entity_list.items)} {noun(len(entity_list.items), "device")})'
        for number, entity_list in enumerate(focus, start=1)
    ]


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 1]


def _matches(entity_list: EntityList, hint_words: list[str]) -> bool:
    title_words = set(_words(entity_list.title))
    return bool(hint_words) and all(word in title_words for word in hint_words)


def resolve_reference(
    reference: Reference, focus: Sequence[EntityList], tenant_devices: Sequence[Device]
) -> Resolution:
    """Resolve "the first one", "those", "the last one" or a named device against the last answer.

    Only this tenant's devices can come out; ids from an old list that are no longer the tenant's are dropped.
    """
    in_tenant = {d.device_id for d in tenant_devices}

    if reference.kind == "named":
        wanted = (reference.device_ref or "").strip()
        for device in tenant_devices:
            if device.device_id == wanted or device.hostname.lower() == wanted.lower():
                return Resolved((device.device_id,), None)
        return NotFoundRef()

    if not focus:
        return NeedsClarification(texts.NEED_REFERENCE, ())

    candidates = list(focus)
    if reference.list_hint:
        hinted = [el for el in focus if _matches(el, _words(reference.list_hint))]
        if not hinted:
            return NeedsClarification(
                texts.WHICH_LIST, tuple(el.title for el in focus)[:_MAX_OPTIONS]
            )
        candidates = hinted

    if len(candidates) > 1:
        if reference.kind == "all":  # "those" after several lists means everything that was shown
            ids = list(dict.fromkeys(item.device_id for el in candidates for item in el.items))
            kept = tuple(i for i in ids if i in in_tenant)
            return Resolved(kept, None) if kept else NotFoundRef()
        return NeedsClarification(
            texts.WHICH_LIST, tuple(el.title for el in candidates)[:_MAX_OPTIONS]
        )

    chosen = candidates[0]
    items = chosen.items
    if reference.kind == "ordinal":
        index = reference.index or 1
        if index > len(items):
            return NeedsClarification(
                texts.entry_out_of_range(len(items)), tuple(i.label for i in items)[:_MAX_OPTIONS]
            )
        picked = [items[index - 1]]
    elif reference.kind == "last":
        picked = [items[-1]]
    else:
        picked = list(items)
    kept = tuple(i.device_id for i in picked if i.device_id in in_tenant)
    return Resolved(kept, chosen.list_id) if kept else NotFoundRef()
