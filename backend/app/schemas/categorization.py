"""Request and response bodies of the categorization feature."""
import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class CategorizationSettingsUpdate(BaseModel):
    """A partial update: fields left out stay as they are.

    `llm_connection_id` is the only blankable field (null = fall back to the
    owner's default connection); a null anywhere else means "unchanged".
    """

    enabled: Optional[bool] = None
    auto_apply_threshold: Optional[int] = Field(default=None, ge=50, le=100)
    monthly_cap_usd: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("1000"), decimal_places=2)
    llm_connection_id: Optional[uuid.UUID] = None
