"""Find or create a category (and its group) by name, on the spot.

The person (or the model's proposal they accept) names a group and a
category. Names are compared the way a person reads them: no accents, no
case, spaces squeezed. A visible group of the same name is reused (a hidden
one is not: it would hide the category made in it), and so is an assignable
category of the same name in that group. Whatever is created gets an icon
and a color guessed from its name, editable later in Categorias.

Nothing here commits: the caller owns the transaction.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.schemas.category import CategoryCreate
from app.schemas.category_group import CategoryGroupCreate
from app.services import category_group_service, category_service
from app.services.categorization import memory

#: Name stems (folded) and the icon they suggest, first match wins. A stem
#: counts at the start of a word ("PET SHOP", not "CARPETE"). Every icon
#: exists in `frontend/src/lib/category-icons.ts`.
STYLE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("CARRO", "AUTO", "VEICUL"), "car"),
    (("COMBUST", "GASOL", "POSTO"), "fuel"),
    (("TRANSPORT", "UBER", "TAXI"), "bus"),
    (("VIAG", "HOTEL", "PASSAG"), "plane"),
    (("CASA", "MORAD", "ALUGU"), "house"),
    (("JARDIN", "PLANTA"), "tree-pine"),
    (("MERCAD", "SUPERMERC"), "shopping-cart"),
    (("RESTAUR", "ALIMENT", "COMIDA", "DELIVER"), "utensils-crossed"),
    (("CAFE", "PADAR"), "coffee"),
    (("FARMAC", "REMED"), "pill"),
    (("SAUDE", "MEDIC", "CONSULT"), "stethoscope"),
    (("ACADEM", "ESPORT"), "dumbbell"),
    (("PRESENT",), "gift"),
    (("ROUPA", "VESTU"), "shirt"),
    (("PET", "CACHOR", "VETERIN"), "dog"),
    (("FILHO", "BEBE", "ESCOLA"), "baby"),
    (("EDUCA", "CURSO", "FACULD"), "graduation-cap"),
    (("TRABALH", "EMPRES", "NEGOC"), "briefcase"),
    (("ASSINAT", "STREAM"), "tv"),
    (("INTERNET", "CELULAR"), "wifi"),
    (("LUZ", "ENERG"), "lightbulb"),
    (("AGUA",), "droplets"),
    (("JOGO", "LAZER"), "gamepad-2"),
    (("MANUTEN", "REFORM"), "wrench"),
)
#: Word starts that look like a stem but mean something else.
FALSE_STEMS: tuple[str, ...] = ("AGUARD", "PETRO", "AUTONOM", "CASAMENT")
#: Colors a new group (and its categories) can get, picked by its name.
PALETTE: tuple[str, ...] = (
    "#6366f1", "#22c55e", "#f59e0b", "#ef4444", "#06b6d4", "#a855f7", "#ec4899", "#14b8a6",
)
#: The color a group or category gets when nobody chose one.
DEFAULT_COLOR = "#6B7280"
#: Icons for a name that suggests none: the frontend's "Outros" icon (it
#: has no folder icon, and an unknown name falls back to a generic one).
DEFAULT_CATEGORY_ICON = "circle-help"
DEFAULT_GROUP_ICON = "circle-help"


def fold(text: str) -> str:
    """Uppercase, accents removed: how the rule engine compares text."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def norm_name(text: str) -> str:
    """A name as compared for "the same one": folded, spaces squeezed."""
    return " ".join(fold(text).split())


class InvalidName(ValueError):
    """A group or category name that is blank."""


_WORD_RE = re.compile(r"[A-Z0-9]+")


def _words(name: str) -> list[str]:
    return [w for w in _WORD_RE.findall(fold(name)) if not w.startswith(FALSE_STEMS)]


def _icon_for(*names: str) -> str | None:
    for name in names:
        words = _words(name)
        for stems, icon in STYLE_KEYWORDS:
            if any(word.startswith(stems) for word in words):
                return icon
    return None


def _palette_color(group_name: str) -> str:
    digest = hashlib.sha1(fold(group_name).encode("utf-8")).hexdigest()
    return PALETTE[int(digest, 16) % len(PALETTE)]


def suggest_group_style(group_name: str) -> tuple[str, str]:
    """(icon, color) for a new group: the icon from its name, else the
    "Outros" one; the color picked by its name."""
    return _icon_for(group_name) or DEFAULT_GROUP_ICON, _palette_color(group_name)


def suggest_style(name: str, group_name: str, group_color: str | None) -> tuple[str, str]:
    """(icon, color) for a new category.

    The icon comes from the category's name, then its group's; the color is
    the group's when it has one of its own, else one picked by the group's
    name (the same group always gets the same one).
    """
    icon = _icon_for(name, group_name) or DEFAULT_CATEGORY_ICON
    if group_color and group_color.strip().lower() != DEFAULT_COLOR.lower():
        return icon, group_color
    return icon, _palette_color(group_name)


@dataclass(frozen=True)
class Created:
    category_id: uuid.UUID
    group_id: uuid.UUID
    created_group: bool
    created_category: bool


async def _find_or_create_group(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, name: str
) -> tuple[CategoryGroup, bool]:
    wanted = norm_name(name)
    groups = (
        await session.execute(
            select(CategoryGroup).where(CategoryGroup.workspace_id == workspace_id)
        )
    ).scalars().all()
    for group in groups:
        # A hidden group would hide the category created in it.
        if not group.is_hidden and norm_name(group.name) == wanted:
            return group, False
    clean = " ".join(name.split())
    icon, color = suggest_group_style(clean)
    group = await category_group_service.create_group(
        session,
        workspace_id,
        user_id,
        CategoryGroupCreate(name=clean, icon=icon, color=color),
        commit=False,
    )
    return group, True


async def find_or_create_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    group: str,
    name: str,
) -> Created:
    """The category `name` in the group `group`, created when missing.

    When anything is created the tree changed, so the cached AI decisions
    (made without it) are dropped for the whole workspace.
    """
    if not group.strip() or not name.strip():
        raise InvalidName("Grupo e nome da categoria são obrigatórios")
    category_group, created_group = await _find_or_create_group(
        session, workspace_id, user_id, group
    )
    wanted = norm_name(name)
    existing = (
        await session.execute(
            select(Category).where(
                Category.workspace_id == workspace_id,
                Category.group_id == category_group.id,
            )
        )
    ).scalars().all()
    assignable = await category_service.get_assignable_category_ids(session, workspace_id)
    category = next(
        (c for c in existing if c.id in assignable and norm_name(c.name) == wanted), None
    )
    created_category = category is None
    if category is None:
        clean = " ".join(name.split())
        icon, color = suggest_style(clean, category_group.name, category_group.color)
        category = await category_service.create_category(
            session,
            workspace_id,
            user_id,
            CategoryCreate(name=clean, icon=icon, color=color, group_id=category_group.id),
            commit=False,
        )
    if created_group or created_category:
        await memory.forget_ai_decisions(session, workspace_id)
    return Created(
        category_id=category.id,
        group_id=category_group.id,
        created_group=created_group,
        created_category=created_category,
    )
