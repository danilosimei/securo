"""Per-workspace categorization settings, created lazily with the defaults.

Like `invoice_service.get_settings`: the row appears on first read rather
than at workspace creation. Nothing here commits: the caller owns the
transaction.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models.connection import LlmConnection
from app.models.categorization import CategorizationSettings
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.categorization import CategorizationSettingsUpdate

#: The only kind of LLM connection the categorizer can use.
CONNECTION_KIND = "anthropic"


async def workspace_owner(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID | None:
    """Whose key and consent unattended runs use.

    The earliest `owner` member; failing that, the external manager, then
    the creator (a workspace whose owner left still has a responsible).
    """
    owner = await session.scalar(
        select(WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")
        .order_by(WorkspaceMember.joined_at, WorkspaceMember.id)
        .limit(1)
    )
    if owner is not None:
        return owner
    row = (
        await session.execute(
            select(Workspace.managed_by_user_id, Workspace.created_by_user_id).where(
                Workspace.id == workspace_id
            )
        )
    ).first()
    if row is None:
        return None
    return row.managed_by_user_id or row.created_by_user_id


async def get_settings(session: AsyncSession, workspace_id: uuid.UUID) -> CategorizationSettings:
    """This workspace's settings, creating the default row on first read."""
    query = select(CategorizationSettings).where(
        CategorizationSettings.workspace_id == workspace_id
    )
    settings = (await session.execute(query)).scalar_one_or_none()
    if settings is not None:
        return settings

    settings = CategorizationSettings(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        owner_user_id=await workspace_owner(session, workspace_id),
        provider_category_map={},
    )
    try:
        # A savepoint, so losing the race on the unique workspace leaves the
        # caller's transaction usable.
        async with session.begin_nested():
            session.add(settings)
            await session.flush()
        return settings
    except IntegrityError:
        return (await session.execute(query)).scalar_one()


async def find_ai_connection(
    session: AsyncSession, settings: CategorizationSettings
) -> LlmConnection | None:
    """The Anthropic connection runs would spend, or None (no AI).

    The one the settings name, when it is still an Anthropic connection of
    the owner; otherwise the owner's default Anthropic connection.
    """
    if settings.owner_user_id is None:
        return None
    if settings.llm_connection_id is not None:
        connection = await session.get(LlmConnection, settings.llm_connection_id)
        if (
            connection is not None
            and connection.user_id == settings.owner_user_id
            and connection.kind == CONNECTION_KIND
        ):
            return connection
    return await session.scalar(
        select(LlmConnection)
        .where(
            LlmConnection.user_id == settings.owner_user_id,
            LlmConnection.kind == CONNECTION_KIND,
            LlmConnection.is_default.is_(True),
        )
        .order_by(LlmConnection.created_at, LlmConnection.id)
        .limit(1)
    )


async def update_settings(
    session: AsyncSession, workspace_id: uuid.UUID, patch: CategorizationSettingsUpdate
) -> CategorizationSettings:
    """Apply the fields `patch` carries; refuses a connection the owner does not hold.

    Raises ValueError when `llm_connection_id` is not an Anthropic connection
    of the workspace owner: runs spend that person's key, so pointing the
    settings at anyone else's would spend a stranger's money.
    """
    settings = await get_settings(session, workspace_id)
    data = patch.model_dump(exclude_unset=True)

    if "llm_connection_id" in data:
        connection_id = data.pop("llm_connection_id")
        if connection_id is not None:
            connection = await session.get(LlmConnection, connection_id)
            if (
                connection is None
                or connection.user_id != settings.owner_user_id
                or connection.kind != CONNECTION_KIND
            ):
                raise ValueError("LLM connection is not an Anthropic connection of the workspace owner")
        settings.llm_connection_id = connection_id

    for key, value in data.items():
        if value is not None:
            setattr(settings, key, value)

    await session.flush()
    return settings
