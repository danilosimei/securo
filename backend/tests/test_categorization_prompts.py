import json
import re
import uuid
from decimal import Decimal

from app.models.categorization import MerchantMemory
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.services.categorization.pipeline import MerchantGroup
from app.services.categorization.prompts import (
    CLASSIFIER_MODEL,
    OUTPUT_FIELDS,
    build_category_index,
    EXAMPLES_PER_CATEGORY,
    build_corrections,
    build_examples,
    build_history,
    build_result_schema,
    build_system_blocks,
    build_user_message,
    category_context,
    classify_params,
    prompt_sample,
)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}/\d{2}/\d{4}\b")


def group(name: str, position: int, hidden: bool = False) -> CategoryGroup:
    return CategoryGroup(id=uuid.uuid4(), name=name, position=position, is_hidden=hidden)


def category(
    name: str,
    grp: CategoryGroup | None,
    hidden: bool = False,
    transfer: bool = False,
    description: str | None = None,
) -> Category:
    return Category(
        id=uuid.uuid4(), name=name, group_id=grp.id if grp else None, is_hidden=hidden,
        treat_as_transfer=transfer, description=description,
    )


def tree():
    casa = group("Casa", 1)
    lazer = group("Lazer", 0)
    oculto = group("Arquivo", 2, hidden=True)
    cats = [
        category("Mercado", casa),
        category("Cinema", lazer),
        category("Aluguel", casa),
        category("Streaming", lazer),
        category("Velha", oculto),
        category("Escondida", casa, hidden=True),
        category("Outros", None),
    ]
    return cats, [casa, lazer, oculto]


def by_name(cats, name):
    return next(c for c in cats if c.name == name)


def merchant(
    key: str,
    samples: list[str],
    *,
    count: int = 1,
    amount: str = "10.00",
    type_: str = "debit",
    recurring: bool = False,
    account_type: str | None = "checking",
) -> MerchantGroup:
    return MerchantGroup(
        key=key,
        tx_ids=[uuid.uuid4() for _ in range(count)],
        samples=samples,
        count=count,
        median_amount=Decimal(amount),
        type=type_,
        recurring=recurring,
        account_type=account_type,
    )


def memory(key: str, counts: dict[uuid.UUID, int]) -> MerchantMemory:
    return MerchantMemory(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        merchant_key=key,
        counts={str(k): v for k, v in counts.items()},
    )


# --- category index -------------------------------------------------------


def test_category_index_is_deterministic_and_skips_hidden():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    again = build_category_index(list(reversed(cats)), list(reversed(groups)))

    assert index.render() == again.render()
    assert index.render().splitlines() == [
        "c1 Lazer › Cinema",
        "c2 Lazer › Streaming",
        "c3 Casa › Aluguel",
        "c4 Casa › Mercado",
        "c5 Outros",
    ]
    assert index.to_short(by_name(cats, "Velha").id) is None
    assert index.to_short(by_name(cats, "Escondida").id) is None
    assert index.short_ids == ["c1", "c2", "c3", "c4", "c5"]


def test_category_index_marks_transfer_categories():
    casa = group("Finanças", 0)
    cats = [category("Transferências", casa, transfer=True), category("Taxas", casa)]
    assert build_category_index(cats, [casa]).render().splitlines() == [
        "c1 Finanças › Taxas",
        "c2 Finanças › Transferências (movimentação)",
    ]


def test_category_index_round_trips_ids():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    mercado = by_name(cats, "Mercado").id
    assert index.to_short(mercado) == "c4"
    assert index.from_short("c4") == mercado
    assert index.from_short("c99") is None
    assert index.from_short(None) is None
    assert index.to_short(uuid.uuid4()) is None


# --- examples -------------------------------------------------------------


def test_examples_use_dominant_choice_and_hide_private_keys():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    mercado = by_name(cats, "Mercado").id
    cinema = by_name(cats, "Cinema").id
    aluguel = by_name(cats, "Aluguel").id
    rows = [
        memory("P:CARREFOUR", {mercado: 5}),
        memory("CINEMARK", {cinema: 2}),
        memory("PIX:JOAO SILVA", {aluguel: 3}),
        memory("PIX:#0a1b2c3d4e", {aluguel: 9}),
        memory("UNKNOWN", {mercado: 9}),
        memory("SPLIT", {mercado: 1, cinema: 1}),  # no dominant choice
        memory("HIDDEN", {by_name(cats, "Velha").id: 7}),  # not assignable
        memory("EMPTY", {}),
    ]
    text = build_examples(rows, index)
    # Grouped by category (tree order), so every category with a dominant
    # store shows up.
    assert text.splitlines() == [
        "CINEMARK -> c1",
        "Pix JOAO SILVA -> c3",
        "CARREFOUR -> c4",
    ]
    assert "#" not in text and "UNKNOWN" not in text and "P:" not in text


def test_examples_take_three_per_category_and_cover_every_category():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    mercado = by_name(cats, "Mercado").id
    cinema = by_name(cats, "Cinema").id
    aluguel = by_name(cats, "Aluguel").id
    rows = [memory(f"LOJA {i:02d}", {mercado: 2 + (i % 5)}) for i in range(50)]
    rows += [memory("CINEMARK", {cinema: 1}), memory("IMOBILIARIA", {aluguel: 1})]
    text = build_examples(rows, index)
    lines = text.splitlines()
    assert EXAMPLES_PER_CATEGORY == 3
    assert [line for line in lines if line.endswith("-> c4")].__len__() == 3
    # Few picks elsewhere still get their slot.
    assert "CINEMARK -> c1" in lines and "IMOBILIARIA -> c3" in lines
    # Most picked first inside a category; the same input in any order is the same text.
    mercado_lines = [line for line in lines if line.endswith("-> c4")]
    assert mercado_lines == ["LOJA 04 -> c4", "LOJA 09 -> c4", "LOJA 14 -> c4"]
    assert text == build_examples(list(reversed(rows)), index)


# --- descriptions ---------------------------------------------------------


def test_tree_line_carries_the_description_and_others_stay_as_before():
    casa = group("Casa", 0)
    cats = [
        category("Mercado", casa, description="  Supermercado e açougue.\n Não inclui restaurante. "),
        category("Aluguel", casa),
    ]
    index = build_category_index(cats, [casa])
    assert index.render().splitlines() == [
        "c1 Casa › Aluguel",
        "c2 Casa › Mercado: Supermercado e açougue. Não inclui restaurante.",
    ]
    assert build_category_index(
        [category("Mercado", casa), category("Aluguel", casa)], [casa]
    ).render() == "c1 Casa › Aluguel\nc2 Casa › Mercado"


def test_description_with_cpf_and_account_comes_out_clean():
    casa = group("Casa", 0)
    cats = [
        category("Aluguel", casa, description="Pago ao João, CPF 123.456.789-09, conta 0012345-6 ag 1234"),
    ]
    line = build_category_index(cats, [casa]).render()
    assert "123.456.789-09" not in line and "12345678909" not in line
    assert "0012345" not in line
    assert line.startswith("c1 Casa › Aluguel: Pago ao João")
    assert "\n" not in line


# --- corrections ----------------------------------------------------------


def test_corrections_use_the_format_and_skip_unnamed_stores():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    mercado, cinema = by_name(cats, "Mercado").id, by_name(cats, "Cinema").id
    gone = by_name(cats, "Velha").id
    pairs = [
        ("P:CARREFOUR", mercado, cinema),
        ("PIX:#0a1b2c3d4e", mercado, cinema),
        ("UNKNOWN", mercado, cinema),
        ("CINEMARK", cinema, mercado),
        ("CARREFOUR", mercado, cinema),  # same line again, once
        ("SUMIU", mercado, gone),  # a category no longer in the tree
    ]
    text = build_corrections(pairs, index)
    assert text.splitlines() == ["CARREFOUR -> c4 (não c1)", "CINEMARK -> c1 (não c4)"]


def test_corrections_are_limited_and_recent_first():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    mercado, cinema = by_name(cats, "Mercado").id, by_name(cats, "Cinema").id
    pairs = [(f"LOJA {i:02d}", mercado, cinema) for i in range(30)]
    lines = build_corrections(pairs, index).splitlines()
    assert len(lines) == 20 and lines[0] == "LOJA 00 -> c4 (não c1)"


def test_corrections_join_the_cached_block_and_stay_out_when_empty():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    with_corrections = build_system_blocks(index, "X -> c1", "LOJA -> c4 (não c1)")[1]["text"]
    assert "LOJA -> c4 (não c1)" in with_corrections
    assert category_context(index, "X -> c1") == build_system_blocks(index, "X -> c1")[1]["text"]
    assert "(não" not in category_context(index, "X -> c1")


# --- system prompt --------------------------------------------------------


def test_system_blocks_are_byte_identical_across_calls():
    cats, groups = tree()
    mercado = by_name(cats, "Mercado").id
    rows = [memory("P:CARREFOUR", {mercado: 3})]

    def render() -> str:
        index = build_category_index(cats, groups)
        blocks = build_system_blocks(index, build_examples(rows, index))
        return json.dumps(blocks, ensure_ascii=False, sort_keys=True)

    first = render()
    assert first == render()
    assert not UUID_RE.search(first)
    assert not DATE_RE.search(first)


def test_system_blocks_cache_the_tree_and_examples():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    blocks = build_system_blocks(index, "CARREFOUR -> c4")
    assert [b["type"] for b in blocks] == ["text", "text"]
    # Two breakpoints: the instructions stay cached when the tree changes.
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert "c4 Casa › Mercado" in blocks[1]["text"]
    assert "CARREFOUR -> c4" in blocks[1]["text"]


def test_system_prompt_is_long_enough_to_cache():
    # Haiku 5.5 caches prefixes of 512+ tokens; ~4 chars per token keeps a
    # margin even for a workspace with an empty tree and no examples.
    cats, groups = tree()
    blocks = build_system_blocks(build_category_index([], []), "")
    assert len(blocks[0]["text"]) / 4 >= 600
    assert len(build_system_blocks(build_category_index(cats, groups), "")[1]["text"]) > 0


def test_withdrawal_rule_leaves_c_null_without_a_cash_category():
    """Unsure means `c` null everywhere; no fallback to a generic category."""
    text = build_system_blocks(build_category_index([], []), "")[0]["text"]
    [rule] = [line for line in text.splitlines() if line.startswith("- Saque")]
    assert rule.endswith("use a categoria de saque ou dinheiro, se existir; senão c null.")
    assert "genérica" not in rule


# --- user message ---------------------------------------------------------


def test_user_message_has_no_documents_nor_keys():
    groups = [
        merchant("PIX:#0a1b2c3d4e", ["PIX ENVIADO 123.456.789-09"]),
        merchant("P:LOJA SECRETA", ["COMPRA 12.345.678/0001-95 LOJA"]),
    ]
    text, ids = build_user_message(groups)
    assert "123.456.789-09" not in text and "12345678909" not in text
    assert "12.345.678/0001-95" not in text
    assert "PIX:#" not in text and "0a1b2c3d4e" not in text
    assert "P:LOJA" not in text
    assert set(ids.values()) == {"PIX:#0a1b2c3d4e", "P:LOJA SECRETA"}


def test_prompt_samples_drop_account_and_card_numbers():
    long_digits = re.compile(r"\d{4,}")
    cases = {
        "TED 341 AG 1234 CC 0012345-6 JOAO": "TED 341 JOAO",
        "PIX ENVIADO ***.456.789-** JOAO": "PIX ENVIADO JOAO",
        "TRANSF CONTA: 98765-4 AGÊNCIA 0001 MARIA": "TRANSF MARIA",
        "COMPRA CARTAO 5502 **** **** 1234 LOJA": "COMPRA CARTAO LOJA",
        "COMPRA CARTAO FINAL 1234 LOJA": "COMPRA CARTAO LOJA",
        "PEDIDO 1234567 LOJA": "PEDIDO LOJA",
        "Transferência enviada|123.456.789-09": "Transferência enviada",
    }
    for raw, expected in cases.items():
        cleaned = prompt_sample(raw)
        assert cleaned == expected, raw
        assert not long_digits.search(cleaned), raw
    # Short numbers that carry meaning stay.
    assert prompt_sample("PARC 03/10 LOJA") == "PARC 03/10 LOJA"
    assert prompt_sample("UBER *TRIP") == "UBER *TRIP"
    text, _ = build_user_message([merchant("X", ["TED 341 AG 1234 CC 0012345-6 JOAO"])])
    assert not long_digits.search(text.replace('"i1"', ""))


def test_user_message_is_compact_and_maps_item_ids():
    groups = [
        merchant(
            "UBER", ["UBER *TRIP"], count=3, amount="23.5", recurring=False,
            account_type="credit_card",
        ),
        merchant(
            "NETFLIX", ["NETFLIX.COM", "NETFLIX"], count=4, amount="55.90",
            recurring=True, account_type="credit_card",
        ),
        merchant("RENDIMENTO", ["RENDIMENTO RDB"], amount="7", type_="credit",
                 account_type="savings"),
        merchant("XPTO", ["XPTO"], account_type=None),
    ]
    text, ids = build_user_message(groups)
    again, ids_again = build_user_message(list(reversed(groups)))

    assert (text, ids) == (again, ids_again)
    assert " " not in text.replace("NETFLIX.COM", "").replace("UBER *TRIP", "").replace(
        "RENDIMENTO RDB", ""
    )
    items = json.loads(text)
    assert [i["k"] for i in items] == ["i1", "i2", "i3", "i4"]
    assert {ids[i["k"]] for i in items} == {"UBER", "NETFLIX", "RENDIMENTO", "XPTO"}
    netflix = next(i for i in items if ids[i["k"]] == "NETFLIX")
    assert netflix == {
        "k": netflix["k"], "s": ["NETFLIX.COM", "NETFLIX"], "q": 4, "v": 55.9,
        "t": "d", "m": 1, "a": "cc",
    }
    uber = next(i for i in items if ids[i["k"]] == "UBER")
    assert uber["v"] == 23.5 and uber["m"] == 0
    assert all("h" not in i for i in items)
    rendimento = next(i for i in items if ids[i["k"]] == "RENDIMENTO")
    assert rendimento["t"] == "c" and rendimento["a"] == "ca" and rendimento["v"] == 7
    assert next(i for i in items if ids[i["k"]] == "XPTO")["a"] == "o"


def test_user_message_carries_previous_choices_as_history():
    groups = [merchant("PADARIA X", ["PADARIA X"]), merchant("UBER", ["UBER"])]
    history = {"PADARIA X": [("c7", 1), ("c3", 4), ("c9", 1), ("c2", 1)], "OTHER": [("c1", 2)]}
    text, ids = build_user_message(groups, history)
    items = {ids[i["k"]]: i for i in json.loads(text)}
    assert items["PADARIA X"]["h"] == [["c3", 4], ["c2", 1], ["c7", 1]]
    assert "h" not in items["UBER"]
    assert '"h":[["c3",4],["c2",1],["c7",1]]' in text


def test_build_history_maps_counts_to_short_ids():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    mercado, cinema = by_name(cats, "Mercado").id, by_name(cats, "Cinema").id
    rows = [
        memory("PADARIA X", {mercado: 3, cinema: 2, by_name(cats, "Velha").id: 9}),
        memory("GONE", {by_name(cats, "Velha").id: 2}),
        memory("EMPTY", {}),
    ]
    assert build_history(rows, index) == {"PADARIA X": [("c4", 3), ("c1", 2)]}


def test_user_message_rounds_median_to_cents():
    text, _ = build_user_message([merchant("A", ["A"], amount="10.555")])
    assert json.loads(text)[0]["v"] == 10.56


# --- schema and params ----------------------------------------------------


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_result_schema_is_strict_with_enum_ids():
    cats, groups = tree()
    schema = build_result_schema(build_category_index(cats, groups))
    item = schema["properties"]["items"]["items"]
    # Name and reason come before the decision: free reasoning, thinking off.
    assert list(item["properties"]) == list(OUTPUT_FIELDS) == [
        "k", "n", "r", "c", "alt", "p", "new",
    ]
    assert item["required"] == list(OUTPUT_FIELDS)
    nullable_ids = {"anyOf": [
        {"type": "string", "enum": ["c1", "c2", "c3", "c4", "c5"]}, {"type": "null"},
    ]}
    assert item["properties"]["c"] == nullable_ids
    assert item["properties"]["alt"] == nullable_ids
    assert item["properties"]["k"] == {"type": "string"}
    assert item["properties"]["p"] == {"type": "integer"}
    new = item["properties"]["new"]["anyOf"]
    assert new[1] == {"type": "null"}
    assert new[0]["required"] == ["g", "name"]
    for node in _walk(schema):
        assert not isinstance(node.get("type"), list)
        for banned in ("minLength", "maxLength", "minimum", "maximum"):
            assert banned not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])


def test_result_schema_without_categories_only_allows_null():
    schema = build_result_schema(build_category_index([], []))
    item = schema["properties"]["items"]["items"]
    assert item["properties"]["c"] == {"type": "null"}


def test_classify_params_uses_haiku_low_effort_no_thinking():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    blocks = build_system_blocks(index, "")
    schema = build_result_schema(index)
    params = classify_params(blocks, "[]", 3, schema)
    assert params["model"] == CLASSIFIER_MODEL == "claude-haiku-5-5"
    assert params["thinking"] == {"type": "disabled"}
    assert params["output_config"] == {
        "effort": "low", "format": {"type": "json_schema", "schema": schema},
    }
    assert params["system"] is blocks
    assert params["messages"] == [{"role": "user", "content": "[]"}]
    assert "temperature" not in params


def test_max_tokens_scales_with_items():
    schema = build_result_schema(build_category_index([], []))
    assert classify_params([], "[]", 1, schema)["max_tokens"] == 320
    assert classify_params([], "[]", 100, schema)["max_tokens"] == 12200
    assert classify_params([], "[]", 500, schema)["max_tokens"] == 16000


def test_prompt_samples_drop_spaced_documents():
    from app.services.categorization.merchant_key import scrub_documents
    from app.services.categorization.prompts import strip_account_numbers

    for raw in ("PIX 123 456 789 09 JOAO", "TED 12 345 678 0001 90 JOAO"):
        cleaned = prompt_sample(raw)
        assert cleaned == raw.split()[0] + " JOAO", raw
    assert strip_account_numbers(scrub_documents("PIX 123 456 789 09 JOAO")) == "PIX JOAO"


# --- store notes ------------------------------------------------------------------


def test_batch_item_carries_store_note():
    groups = [merchant("IFOOD", ["IFOOD *REST"]), merchant("UBER", ["UBER"])]
    text, ids = build_user_message(
        groups, {"IFOOD": [("c3", 1), ("c2", 1)]}, notes={"IFOOD": "Aurino: jardineiro"}
    )
    items = {ids[i["k"]]: i for i in json.loads(text)}
    assert items["IFOOD"]["o"] == "Aurino: jardineiro"
    assert list(items["IFOOD"])[-2:] == ["h", "o"]
    assert "o" not in items["UBER"]
    # No notes: the same bytes as before.
    assert build_user_message(groups, notes={})[0] == build_user_message(groups)[0]


def test_batch_note_is_sanitized():
    text, _ = build_user_message(
        [merchant("IFOOD", ["IFOOD"])],
        notes={"IFOOD": "cpf 123.456.789-09 cartão 4111 1111 1111 1111 " + "x" * 300},
    )
    [item] = json.loads(text)
    assert "123.456.789-09" not in item["o"] and "4111" not in item["o"]
    assert item["o"].startswith("cpf cartão x")
    assert len(item["o"]) == 200


def test_instructions_explain_the_note_field():
    text = build_system_blocks(build_category_index([], []), "")[0]["text"]
    lines = text.splitlines()
    note_line = (
        "- o (só às vezes): nota que a pessoa escreveu sobre esta loja; "
        "é o sinal mais forte, acima de h."
    )
    assert note_line in lines
    assert lines.index(note_line) == next(
        n for n, line in enumerate(lines) if line.startswith("- h (só às vezes)")
    ) + 1


def test_category_context_is_the_cached_tree_block():
    cats, groups = tree()
    index = build_category_index(cats, groups)
    assert build_system_blocks(index, "X -> c1")[1]["text"] == category_context(index, "X -> c1")
    assert category_context(build_category_index([], []), "") == (
        "Categorias desta pessoa (id Grupo › Categoria: descrição, quando ela escreveu uma):\n"
        "(nenhuma)\n\n"
        "Exemplos desta pessoa (loja -> id), até 3 por categoria, dos mais usados para os menos usados:\n"
        "(nenhum ainda)"
    )


def test_instructions_tell_the_model_to_follow_descriptions_and_corrections():
    from app.services.categorization.prompts import INSTRUCTIONS, RULES_NOTE

    assert RULES_NOTE in INSTRUCTIONS and "{RULES_NOTE}" not in INSTRUCTIONS
