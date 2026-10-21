"""The "ask the AI about a store" chat: what goes to Claude and what comes back.

Pure functions only: no database, no client.
"""
import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.categorization import MerchantMemory
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.services.categorization.ask_prompts import (
    ASK_INSTRUCTIONS,
    ASK_MAX_TOKENS,
    ASK_MODEL,
    HISTORY_WINDOW,
    MESSAGE_LENGTH,
    REPLY_LENGTH,
    SAMPLES,
    StoreContext,
    ask_params,
    build_ask_schema,
    build_ask_system_blocks,
    conversation_messages,
    parse_answer,
    render_store_context,
)
from app.services.categorization.prompts import (
    CLASSIFIER_MODEL,
    build_category_index,
    build_examples,
    build_system_blocks,
)

def _tree():
    casa = CategoryGroup(id=uuid.uuid4(), name="Casa", position=0, is_hidden=False)
    lazer = CategoryGroup(id=uuid.uuid4(), name="Lazer", position=1, is_hidden=False)
    cats = [
        Category(id=uuid.uuid4(), name=name, group_id=grp.id, is_hidden=False,
                 treat_as_transfer=False)
        for name, grp in (("Jardim", casa), ("Mercado", casa), ("Cinema", lazer))
    ]
    return cats, [casa, lazer]


def _index():
    cats, groups = _tree()
    return build_category_index(cats, groups), {c.name: c.id for c in cats}


def _ctx(ids, **overrides) -> StoreContext:
    values = {
        "name": None,
        "is_person": True,
        "count": 4,
        "total": Decimal("480.00"),
        "last_date": date(2026, 9, 30),
        "samples": ("PIX ENVIADO 123.456.789-09 AURINO", "PIX ENVIADO AURINO"),
        "kind": "d",
        "current_category_id": None,
        "suggested_category_id": ids["Jardim"],
        "alt_category_id": ids["Mercado"],
        "history": ((ids["Jardim"], 2), (uuid.uuid4(), 5)),
        "note": None,
    }
    values.update(overrides)
    return StoreContext(**values)


def _message(payload, *, stop_reason="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason,
    )


# --- context ----------------------------------------------------------------------


def test_store_context_never_carries_the_key():
    index, ids = _index()
    memory = MerchantMemory(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), merchant_key="PIX:#0a1b2c3d4e",
        counts={str(ids["Jardim"]): 3},
    )
    examples = build_examples([memory], index)
    blocks = build_ask_system_blocks(index, examples, _ctx(ids))
    for block in blocks:
        assert "PIX:#" not in block["text"]
        assert "0a1b2c3d4e" not in block["text"]
        assert "123.456.789-09" not in block["text"]


def test_render_store_context_is_readable_and_compact():
    index, ids = _index()
    ctx = _ctx(
        ids, name="Aurino Silva", current_category_id=ids["Cinema"],
        samples=("A 1", "B 2", "C 3", "D 4"), note="Aurino: cpf 123.456.789-09 jardineiro",
    )
    text = render_store_context(ctx, index)
    assert "Aurino Silva" in text
    data = json.loads(next(line for line in text.splitlines() if line.startswith("{")))
    short = {name: index.to_short(cid) for name, cid in ids.items()}
    assert data["q"] == 4 and data["v"] == 480 and data["u"] == "2026-09-30"
    assert data["s"] == ["A 1", "B 2", "C 3"] and len(data["s"]) == SAMPLES
    assert data["t"] == "d"
    assert (data["atual"], data["sug"], data["alt"]) == (
        short["Cinema"], short["Jardim"], short["Mercado"],
    )
    # Categories no longer in the index are dropped from the history.
    assert data["h"] == [[short["Jardim"], 2]]
    assert data["nota"] == "Aurino: cpf jardineiro"
    for cid in ids.values():
        assert str(cid) not in text
    assert '": ' not in text  # compact JSON


def test_render_store_context_names_a_person_without_name():
    index, ids = _index()
    assert "pessoa sem nome" in render_store_context(_ctx(ids), index)


def test_three_cache_breakpoints_and_tree_block_matches_classifier():
    index, ids = _index()
    examples = "JARDINS -> c1"
    blocks = build_ask_system_blocks(index, examples, _ctx(ids))
    assert len(blocks) == 3
    assert all(b["type"] == "text" for b in blocks)
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in blocks)
    assert blocks[0]["text"] == ASK_INSTRUCTIONS
    assert blocks[1]["text"] == build_system_blocks(index, examples)[1]["text"]
    assert blocks[2]["text"] == render_store_context(_ctx(ids), index)


def test_instructions_cover_scope_and_note():
    for needle in ("desta loja", "Regras", "Categorias", "suggestion", "note", "null"):
        assert needle in ASK_INSTRUCTIONS


# --- messages ---------------------------------------------------------------------


def test_person_text_is_sanitized():
    index, ids = _index()
    text = "meu cpf 123.456.789-09, cartão 4111 1111 1111 1111, conta 12345-6"
    messages = conversation_messages([("user", text)])
    params = ask_params(build_ask_system_blocks(index, "", _ctx(ids)), messages,
                        build_ask_schema(index))
    sent = json.dumps(params, ensure_ascii=False)
    for secret in ("123.456.789-09", "4111", "12345-6"):
        assert secret not in sent
    assert messages[0]["role"] == "user" and "cpf" in messages[0]["content"]


def test_message_is_cut_to_length():
    [message] = conversation_messages([("user", "a" * (MESSAGE_LENGTH + 50))])
    assert len(message["content"]) == MESSAGE_LENGTH


def test_history_window_starts_with_person():
    turns = [("user" if n % 2 == 0 else "assistant", f"m{n}") for n in range(25)]
    messages = conversation_messages(turns)
    assert len(messages) in (HISTORY_WINDOW, HISTORY_WINDOW - 1)
    assert messages[0]["role"] == "user"
    assert messages[-1] == {"role": "user", "content": "m24"}


def test_history_without_person_turns_is_empty():
    assert conversation_messages([("assistant", "oi")]) == []


# --- schema and params ------------------------------------------------------------


def test_ask_schema_only_accepts_workspace_ids():
    index, _ = _index()
    schema = build_ask_schema(index)
    assert list(schema["properties"]) == ["reply", "suggestion", "note"]
    assert schema["required"] == ["reply", "suggestion", "note"]
    assert schema["additionalProperties"] is False
    [suggestion, null] = schema["properties"]["suggestion"]["anyOf"]
    assert null == {"type": "null"}
    assert suggestion["required"] == ["c", "new", "p"]
    assert suggestion["additionalProperties"] is False
    [c_ref, _] = suggestion["properties"]["c"]["anyOf"]
    assert c_ref["enum"] == index.short_ids == ["c1", "c2", "c3"]
    [new, _] = suggestion["properties"]["new"]["anyOf"]
    assert new["required"] == ["g", "name"] and new["additionalProperties"] is False
    assert schema["properties"]["note"]["anyOf"][1] == {"type": "null"}
    # An empty tree leaves only null for `c`.
    empty = build_ask_schema(build_category_index([], []))
    assert empty["properties"]["suggestion"]["anyOf"][0]["properties"]["c"] == {"type": "null"}


def test_ask_params_use_haiku_low_effort_no_thinking():
    index, ids = _index()
    system = build_ask_system_blocks(index, "", _ctx(ids))
    messages = conversation_messages([("user", "é o jardineiro")])
    schema = build_ask_schema(index)
    assert ASK_MODEL == CLASSIFIER_MODEL == "claude-haiku-5-5"
    assert ask_params(system, messages, schema) == {
        "model": "claude-haiku-5-5",
        "max_tokens": ASK_MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        "system": system,
        "messages": messages,
    }
    assert ASK_MAX_TOKENS == 1024


# --- parsing ----------------------------------------------------------------------


def test_parse_drops_unknown_category_and_clamps():
    index, _ = _index()
    parsed = parse_answer(_message({
        "reply": "Pode ser jardim.", "suggestion": {"c": "c99", "new": None, "p": 180},
        "note": None,
    }), index)
    assert parsed is not None
    assert parsed.category_id is None and parsed.new_category is None
    assert parsed.confidence == 100


def test_parse_reads_category_and_new():
    index, ids = _index()
    parsed = parse_answer(_message({
        "reply": "  Jardim.  ", "suggestion": {"c": "c1", "new": None, "p": 85},
        "note": "Aurino: jardineiro semanal",
    }), index)
    assert parsed is not None
    assert (parsed.reply, parsed.category_id, parsed.confidence, parsed.note) == (
        "Jardim.", ids["Jardim"], 85, "Aurino: jardineiro semanal",
    )
    assert json.loads(parsed.raw) == {
        "reply": "Jardim.", "suggestion": {"c": "c1", "new": None, "p": 85},
        "note": "Aurino: jardineiro semanal",
    }
    assert ", " not in parsed.raw and '": ' not in parsed.raw

    created = parse_answer(_message({
        "reply": "Nova.", "suggestion": {"c": None, "new": {"g": "Casa", "name": "x" * 150},
                                         "p": -5},
        "note": None,
    }), index)
    assert created is not None
    assert created.new_category == ("Casa", "x" * 100)
    assert created.category_id is None and created.confidence == 0
    assert json.loads(created.raw)["suggestion"]["new"] == {"g": "Casa", "name": "x" * 100}


def test_parse_without_suggestion():
    index, _ = _index()
    parsed = parse_answer(_message({"reply": "O que é?", "suggestion": None, "note": None}),
                          index)
    assert parsed is not None
    assert (parsed.category_id, parsed.new_category, parsed.confidence) == (None, None, 0)
    assert json.loads(parsed.raw) == {"reply": "O que é?", "suggestion": None, "note": None}

    both_null = parse_answer(_message({
        "reply": "Hm.", "suggestion": {"c": None, "new": None, "p": 70}, "note": None,
    }), index)
    assert both_null is not None
    assert json.loads(both_null.raw)["suggestion"] is None


def test_parse_refusal_is_none():
    index, _ = _index()
    good = {"reply": "ok", "suggestion": None, "note": None}
    assert parse_answer(_message(good, stop_reason="refusal"), index) is None
    assert parse_answer(_message(good, stop_reason="max_tokens"), index) is None
    assert parse_answer(_message("{not json"), index) is None
    assert parse_answer(_message([1, 2]), index) is None
    assert parse_answer(SimpleNamespace(content=[], stop_reason="end_turn"), index) is None


def test_parse_note_cut_to_200():
    index, _ = _index()
    parsed = parse_answer(_message({
        "reply": "r" * (REPLY_LENGTH + 10), "suggestion": None,
        "note": "cpf 123.456.789-09 " + "n" * 300,
    }), index)
    assert parsed is not None
    assert parsed.note is not None and len(parsed.note) == 200
    assert "123.456.789-09" not in parsed.note
    assert len(parsed.reply) == REPLY_LENGTH
    assert json.loads(parsed.raw)["note"] == parsed.note
