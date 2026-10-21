"""Pure rule evaluation engine — no DB access."""
import re
import unicodedata
import uuid
from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.transaction import Transaction


def _strip_accents(text: str) -> str:
    """Remove diacritics (accents), preserving case."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def compile_rule_regex(pattern: str) -> re.Pattern[str]:
    """Compile a regex using Securo's runtime normalization and safety policy."""
    effective_pattern = _strip_accents(pattern)
    try:
        compiled = re.compile(effective_pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError("Invalid regular expression") from exc
    if compiled.search("") is not None:
        raise ValueError("Regular expression must not match an empty string")
    return compiled


def _normalize(text: str) -> str:
    """Normalize text: uppercase and remove diacritics (accents)."""
    return _strip_accents(text.upper())


#: What a lost accented letter became in some production descriptions
#: ("Remunera��o"): one per letter. In a transaction's text it
#: stands for any one letter of the rule value.
REPLACEMENT_CHAR = "�"
#: A letter, or the character standing for one.
_LETTER_OR_REPLACEMENT = r"(?:[^\W\d_]|�)"
#: Escapes of letter classes and anchors, whose letter is not a literal.
_CLASS_ESCAPES = frozenset("dDsSwWbBAZ")
#: Escapes followed by a fixed number of hex digits.
_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}


def _same_at(text: str, start: int, value: str) -> bool:
    """`value` sits at `text[start:]`, a U+FFFD of the text standing for any
    one letter of the value."""
    if start < 0 or start + len(value) > len(text):
        return False
    return all(
        t == v or (t == REPLACEMENT_CHAR and v.isalpha())
        for t, v in zip(text[start:start + len(value)], value, strict=True)
    )


def _contains(text: str, value: str) -> bool:
    if REPLACEMENT_CHAR not in text:
        return value in text
    return any(_same_at(text, i, value) for i in range(len(text) - len(value) + 1))


def _starts_with(text: str, value: str) -> bool:
    if REPLACEMENT_CHAR not in text:
        return text.startswith(value)
    return _same_at(text, 0, value)


def _ends_with(text: str, value: str) -> bool:
    if REPLACEMENT_CHAR not in text:
        return text.endswith(value)
    return _same_at(text, len(text) - len(value), value)


def _equals(text: str, value: str) -> bool:
    if REPLACEMENT_CHAR not in text:
        return text == value
    return len(text) == len(value) and _same_at(text, 0, value)


def _escape_end(pattern: str, i: int) -> int:
    """End of the escape at `pattern[i]` (a backslash)."""
    code = pattern[i + 1:i + 2]
    if code in _HEX_ESCAPES:
        return min(len(pattern), i + 2 + _HEX_ESCAPES[code])
    if code == "N" and pattern.startswith("{", i + 2):
        close = pattern.find("}", i + 3)
        return len(pattern) if close < 0 else close + 1
    return min(len(pattern), i + 2)


def _class_end(pattern: str, i: int) -> tuple[int, bool]:
    """End of the class at `pattern[i]` ("["), and whether it may match a
    letter: not negated, and holding a literal letter or a word class."""
    j = i + 1
    negated = pattern.startswith("^", j)
    if negated:
        j += 1
    if pattern.startswith("]", j):
        j += 1
    letters = False
    while j < len(pattern) and pattern[j] != "]":
        if pattern[j] == "\\":
            end = _escape_end(pattern, j)
            code = pattern[j + 1:j + 2]
            letters = letters or code == "w" or (
                end - j == 2 and code.isalpha() and code not in _CLASS_ESCAPES
            )
            j = end
            continue
        letters = letters or pattern[j].isalpha()
        j += 1
    return min(len(pattern), j + 1), letters and not negated


def _group_head_end(pattern: str, i: int) -> int:
    """End of the head of the "(?" construct at `pattern[i]`: the part with
    names and flags, whose letters are not literals."""
    j = i + 2
    head = pattern[j:j + 1]
    if head == "<" and pattern[j + 1:j + 2] in ("=", "!"):
        return j + 2
    if head in ("P", "<"):
        stops = ">)"
    elif head in ("#", "("):
        stops = ")"
    else:
        while j < len(pattern) and (pattern[j].isalpha() or pattern[j] == "-"):
            j += 1
        return min(len(pattern), j + 1)
    while j < len(pattern) and pattern[j] not in stops:
        j += 1
    return min(len(pattern), j + 1)


def _letters_or_replacement(pattern: str) -> str:
    """`pattern` with each literal letter, word class and class that may
    match a letter also accepting U+FFFD; other escapes, group names and
    flags are kept as they are."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            end = _escape_end(pattern, i)
            out.append(_LETTER_OR_REPLACEMENT if pattern[i + 1:end] == "w" else pattern[i:end])
            i = end
        elif char == "[":
            end, letters = _class_end(pattern, i)
            body = pattern[i:end]
            out.append(f"(?:{body}|{REPLACEMENT_CHAR})" if letters else body)
            i = end
        elif char == "(" and pattern.startswith("?", i + 1):
            end = _group_head_end(pattern, i)
            out.append(pattern[i:end])
            i = end
        elif char.isalpha():
            out.append(f"(?:{char}|{REPLACEMENT_CHAR})")
            i += 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _regex_search(pattern: str, text: str) -> bool:
    """`compile_rule_regex(pattern)` searched in `text`; when the text holds
    U+FFFD, each one may also stand for a letter the pattern expects.

    Raises ValueError for a pattern `compile_rule_regex` refuses.
    """
    if compile_rule_regex(pattern).search(text) is not None:
        return True
    if REPLACEMENT_CHAR not in text:
        return False
    try:
        lenient = re.compile(_letters_or_replacement(_strip_accents(pattern)), re.IGNORECASE)
    except re.error:
        return False
    return lenient.search(text) is not None


def _to_decimal(val) -> Decimal:
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return Decimal("0")


def _to_date(val) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val)
        except ValueError:
            return None
    return None


def _match_condition(condition: dict, tx: "Transaction") -> bool:
    field = condition.get("field", "")
    op = condition.get("op", "")
    value = condition.get("value")

    tx_val = getattr(tx, field, None)

    # A blank value matches everything: "" is a substring of any string, every
    # string starts/ends with it, an empty regex always matches, and numeric
    # comparisons fall back to 0. Creation now rejects these, but rules saved
    # before that validation existed are still in users' databases — refuse to
    # match rather than recategorizing their whole ledger. Explicit 0/False
    # are real values and pass through.
    if value is None or (isinstance(value, str) and not value.strip()):
        return False

    # String operators
    if op in ("contains", "not_contains", "starts_with", "ends_with", "equals", "not_equals", "regex"):
        tx_str = _normalize(str(tx_val or ""))
        val_str = _normalize(str(value or ""))

        # A U+FFFD in the text stands for one letter of the value (see
        # `REPLACEMENT_CHAR`).
        if op == "contains":
            return _contains(tx_str, val_str)
        if op == "not_contains":
            return not _contains(tx_str, val_str)
        if op == "starts_with":
            return _starts_with(tx_str, val_str)
        if op == "ends_with":
            return _ends_with(tx_str, val_str)
        if op == "equals":
            return _equals(tx_str, val_str)
        if op == "not_equals":
            return not _equals(tx_str, val_str)
        if op == "regex":
            try:
                # Strip accents from the pattern so it lines up with the
                # normalized text, but keep its case: uppercasing a regex
                # inverts escape classes (\s -> \S, \b -> \B, \d -> \D) and
                # breaks inline flags. Case is already handled by IGNORECASE.
                return _regex_search(str(value or ""), tx_str)
            except ValueError:
                return False

    # Numeric operators
    if op in ("gt", "gte", "lt", "lte"):
        if field == "date":
            tx_date = _to_date(tx_val)
            val_date = _to_date(value)
            if tx_date is None or val_date is None:
                return False
            if op == "gt":
                return tx_date > val_date
            if op == "gte":
                return tx_date >= val_date
            if op == "lt":
                return tx_date < val_date
            if op == "lte":
                return tx_date <= val_date

        tx_num = _to_decimal(tx_val)
        val_num = _to_decimal(value)
        if op == "gt":
            return tx_num > val_num
        if op == "gte":
            return tx_num >= val_num
        if op == "lt":
            return tx_num < val_num
        if op == "lte":
            return tx_num <= val_num

    return False


def _is_group(node: dict) -> bool:
    """A condition list entry is a group when it carries its own condition list."""
    return isinstance(node, dict) and isinstance(node.get("conditions"), list)


def _match_group(group: dict, tx: "Transaction") -> bool:
    """Evaluate one nested group: its leaves joined by the group's own operator.

    Groups hold leaves only, which caps a rule at two levels. A nested group
    would reach `_match_condition` with no `field`/`op` and evaluate to False;
    creation rejects them, so this only guards hand-edited data.
    """
    conditions = group.get("conditions") or []
    if not conditions:
        return False
    results = [_match_condition(c, tx) for c in conditions]
    if group.get("op") == "or":
        return any(results)
    return all(results)  # "and" is default


def evaluate_conditions(conditions_op: str, conditions: list[dict], tx: "Transaction") -> bool:
    """Return True if the transaction matches the rule's conditions.

    Each entry is either a leaf condition (`field`/`op`/`value`) or a group that
    joins its own leaves with its own operator, letting a rule mix AND and OR —
    e.g. `type is debit AND (description contains UBER OR contains 99POP)`.
    """
    if not conditions:
        return False
    results = [
        _match_group(node, tx) if _is_group(node) else _match_condition(node, tx)
        for node in conditions
    ]
    if conditions_op == "or":
        return any(results)
    return all(results)  # "and" is default


def merge_notes(existing: str | None, incoming: str | None) -> str | None:
    """Combine two note strings the way `append_notes` combines tags.

    Used when an incoming charge is folded into a row that already has notes:
    the existing text is never dropped, the incoming one is only appended when
    it is not already in there.
    """
    incoming = (incoming or "").strip()
    if not incoming:
        return existing
    existing = (existing or "").strip()
    if not existing:
        return incoming
    if incoming in existing:
        return existing
    return f"{existing} {incoming}"


def apply_rule_actions(
    actions: list[dict],
    tx: "Transaction",
    category_already_set: bool,
    *,
    skip_description: bool = False,
    assignable_category_ids: Collection[uuid.UUID] | None = None,
) -> bool:
    """Apply actions in-place and return the updated category-set flag.

    Only a category in `assignable_category_ids` is ever written. A rule that
    names any other one keeps its remaining actions and drops the
    categorization, leaving the transaction uncategorized.

    That covers two cases. A hidden category means the user stopped using it,
    so the pickers no longer offer it and neither should a rule. A category
    that is simply gone means the rule went stale: its id lives inside a JSON
    action with no foreign key behind it, and writing it would fail the insert
    and take the whole import or sync batch with it.

    Passing nothing skips the check, for callers with no workspace at hand.
    """
    for action in actions:
        op = action.get("op")
        value = action.get("value")

        if op == "set_category" and not category_already_set:
            try:
                category_id = uuid.UUID(str(value))
            except (ValueError, AttributeError):
                continue
            if assignable_category_ids is not None and category_id not in assignable_category_ids:
                continue
            tx.category_id = category_id
            category_already_set = True

        elif op == "set_description":
            if skip_description:
                continue
            description = str(value or "").strip()
            if not description or description == tx.description:
                continue
            if getattr(tx, "original_description", None) is None:
                tx.original_description = tx.description
            tx.description = description
            tx.description_is_rule_managed = True

        elif op == "set_payee":
            try:
                tx.payee_id = uuid.UUID(str(value))
            except (ValueError, AttributeError):
                pass

        elif op == "append_notes":
            new_tags = str(value or "").strip()
            if not new_tags:
                continue
            existing = tx.notes or ""
            if new_tags not in existing:
                tx.notes = (existing + " " + new_tags).strip() if existing else new_tags

        elif op == "ignore":
            tx.is_ignored = True

    return category_already_set
