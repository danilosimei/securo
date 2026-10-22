import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

ReportKind = Literal["normal", "income", "refund"]
DESCRIPTION_MAX = 300


def _clean_description(value: Optional[str]) -> Optional[str]:
    """Stripped text, or None when blank (a blank description is no description)."""
    if value is None:
        return None
    value = value.strip()
    if len(value) > DESCRIPTION_MAX:
        raise ValueError(f"description must have at most {DESCRIPTION_MAX} characters")
    return value or None


class CategoryBase(BaseModel):
    name: str
    icon: str = "circle-help"
    color: str = "#6B7280"


class CategoryCreate(CategoryBase):
    group_id: Optional[uuid.UUID] = None
    treat_as_transfer: bool = False
    is_ignored: bool = False
    report_kind: ReportKind = "normal"
    description: Optional[str] = None

    _strip_description = field_validator("description")(_clean_description)


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    group_id: Optional[uuid.UUID] = None
    treat_as_transfer: Optional[bool] = None
    is_ignored: Optional[bool] = None
    is_hidden: Optional[bool] = None
    report_kind: Optional[ReportKind] = None
    description: Optional[str] = None

    _strip_description = field_validator("description")(_clean_description)

    @field_validator("report_kind")
    @classmethod
    def _report_kind_not_null(cls, value: Optional[str]) -> Optional[str]:
        # Omitting the field leaves it alone; sending null is not a kind.
        if value is None:
            raise ValueError("report_kind cannot be null")
        return value


class CategoryRead(CategoryBase):
    id: uuid.UUID
    user_id: uuid.UUID
    group_id: Optional[uuid.UUID] = None
    is_system: bool
    is_hidden: bool = False
    treat_as_transfer: bool = False
    is_ignored: bool = False
    report_kind: ReportKind = "normal"
    description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class RuleSummary(BaseModel):
    """Just enough of a rule to name it in the hide-category dialog."""

    id: uuid.UUID
    name: str


class CategoryRuleUsage(BaseModel):
    """Active rules that assign a category."""

    rules: list[RuleSummary] = []


class CategoryUsageRead(BaseModel):
    """What still points at a category, for the delete dialog to report."""

    transactions: int = 0
    budgets: int = 0
    recurring_transactions: int = 0
    rules: list[RuleSummary] = []
