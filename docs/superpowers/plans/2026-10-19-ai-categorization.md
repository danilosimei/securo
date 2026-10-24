# Categorização inteligente com Claude — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O Securo categoriza sozinho (regras → memória → Claude → usuário), mostra e desfaz tudo o que fez, aprende com cada correção e organiza a árvore de categorias, com uma UX de poucos cliques.

**Architecture:** Módulo de backend novo `app/services/categorization/` (proveniência, chave de loja, memória, pipeline, Claude, organizador) sobre cinco tabelas novas e a coluna `transactions.category_source`; tudo que fala com o Claude roda em tasks Celery; rotas novas em `app/api/categorization.py`. No frontend, página nova `/categorization` com abas Revisar · Aplicadas · Organizar, mais selo de origem, bloco "Como chegou aqui", toast pós-correção, card no dashboard e seção em Configurações.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Celery, SDK oficial `anthropic` 1.x (async), React + TS, shadcn/ui, Tailwind 4, react-query, sonner, i18next, vitest.

**Spec:** `docs/superpowers/specs/2026-10-19-ai-categorization-design.md` (ler inteiro antes de qualquer task).

## Global Constraints

- Branch `feat/ai-categorization`; commits Conventional Commits com descrição em pt-BR, sem rastro de IA.
- Backend gate (em `backend/`): `uv run --no-sync ruff check .`, `uv run --no-sync ty check .`, `uv run --no-sync pytest -n auto --dist loadfile -ra -W error`. Testes com FK/`ondelete` usam a fixture `postgres_sessions` (precisa `POSTGRES_TEST_URL`; subir com `docker run -d --name securo-pgtest -e POSTGRES_USER=securo_test -e POSTGRES_PASSWORD=securo_test -e POSTGRES_DB=securo_test -p 55432:5432 pgvector/pgvector:pg16` e exportar `POSTGRES_TEST_URL=postgresql+asyncpg://securo_test:securo_test@127.0.0.1:55432/securo_test`).
- Frontend gate (em `frontend/`): `npm run lint -- --max-warnings=0`, `npm run build`, `npm test`.
- Migração única nova: `backend/alembic/versions/097_ai_categorization.py` (`revision="097"`, `down_revision="096"`); `python3 backend/scripts/check_migration_chain.py` passa.
- Toda rota nova: escrita com `Depends(current_writable_workspace)` (meta-teste `tests/test_write_permission_coverage.py`), leitura com `Depends(current_workspace)`; entrada em `tests/api_contract/baseline.json` regenerada com `UPDATE_API_CONTRACT=1 uv run pytest tests/api_contract -n 0`; nenhuma rota chama o Claude no request.
- Toda tabela nova tem `workspace_id` (FK `workspaces.id`, `ondelete="CASCADE"`); FKs para `transactions` → `CASCADE`; para `categories`/`payees`/`users` → `SET NULL`. JSON com `JSON().with_variant(JSONB(), "postgresql")`.
- Origens (`category_source`): `none`, `user`, `rule`, `memory`, `ai`, `provider`, `installment`, `import`, `recurring`, `legacy`. Protegidas: `user`, `legacy`. Revisáveis pelo pipeline: `none`, `provider`, `memory`, `ai`.
- Modelos: categorizador `claude-haiku-5-5` com `thinking={"type": "disabled"}` e `output_config={"effort": "low", "format": {...}}`; organizador `claude-opus-5-5` com `thinking={"type": "adaptive"}`, `output_config={"effort": "medium", "format": {...}}`, `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, nunca em Batches. Usar exatamente esses IDs.
- Preços (US$/MTok): Haiku 5.5 entrada 0.10, saída 0.50; Opus 5.5 entrada 4.00, saída 20.00, cache-leitura 0.20. Escrita de cache = 1.25× entrada, leitura de cache Haiku = 0.1× entrada. Batches = 0.5× de tudo.
- Padrões: limite de auto-aplicação 90; teto mensal US$ 1.00; memória aplica com ≥ 2 escolhas e dominante ≥ 80%; lote ≤ 100 lojas; descrição de exemplo truncada em 60 caracteres; motivo ≤ 120 caracteres; categoria nova do organizador só com ≥ 3 transações ou ≥ R$ 200 em 12 meses; 2 a 6 categorias por grupo.
- Privacidade: nunca enviar CPF/CNPJ, número de conta/cartão, saldo, notas, anexos. Dígitos de 11 ou 14 posições (com ou sem pontuação) são removidos antes de qualquer envio.
- A chave da API nunca entra no repositório nem em logs.
- i18n: toda chave nova existe nos 19 arquivos de `frontend/src/locales/` (texto real em `en.json` e `pt-BR.json`; os demais recebem o texto em inglês); placeholders idênticos.
- UI: componentes existentes primeiro (`SectionCard`, `PageHeader`, `Segmented`, `CategoryIcon`, `CategorySelect`, `ui/*`, `ProposalCard`, padrão de pill de `projected-transaction-badge.tsx`); valores monetários sempre `mask(formatCurrency(...))`; ações de escrita só com `canWrite`, configurações com `canManage`.

**Desvio do spec (decisão do plano):** o padrão de regra sugerido após uma correção vem da chave de loja (`description contains <merchant_key>`), sem chamada ao Claude — mesmo resultado, custo zero. Atualizar a linha correspondente do spec (§8 tabela e §10 "Depois de corrigir") na Task 9.

## Review Focus

1. Transação editada à mão (origem `user`) sendo tocada por qualquer camada automática, inclusive `apply_all_rules` e reprocessamentos — esperado: nunca muda. Teste em Task 3 (`test_apply_all_rules_preserves_user_category_and_notes`) e Task 6 (`test_pipeline_skips_protected_sources`).
2. Desfazer depois que o usuário mexeu de novo na transação — esperado: o undo de um evento antigo não sobrescreve uma edição posterior; o item é pulado e contado como "ignorado". Teste em Task 2 (`test_undo_skips_when_transaction_changed_after_event`).
3. Categoria apagada ou mesclada enquanto há sugestões/eventos apontando para ela — esperado: nada quebra (SET NULL), sugestão some da fila. Teste em Task 1 (`test_delete_category_with_events_and_suggestions_postgres`).
4. Resposta do Claude incompleta ou estranha (refusal, `max_tokens`, ID de categoria inexistente, chave de loja que não foi pedida) — esperado: itens afetados vão para Revisar sem sugestão; nada é aplicado com categoria inválida. Teste em Task 10 (`test_apply_results_drops_unknown_ids_and_handles_refusal`).
5. Teto mensal atingido no meio de um backlog grande — esperado: o run para antes de chamar a API, marca `capped`, a UI avisa; regras e memória continuam. Teste em Task 10 (`test_classifier_stops_when_cap_reached`).

---

## Fatia 1 — Fundação de proveniência

### Task 1: Esquema (migração 097 + models)

**Files:**
- Create: `backend/app/models/categorization.py`
- Modify: `backend/app/models/transaction.py` (coluna `category_source`), `backend/app/models/__init__.py` (import + `__all__`), `backend/app/core/workspace_autostamp.py` (`_AUTOSTAMP_MODELS`, se aplicável)
- Create: `backend/alembic/versions/097_ai_categorization.py`
- Test: `backend/tests/test_categorization_models.py`

**Interfaces:**
- Produces (models em `app.models.categorization`):
  - `CategorizationSettings`: `id`, `workspace_id` (unique), `enabled: bool=True`, `auto_apply_threshold: int=90` (check 50–100), `monthly_cap_usd: Decimal=1.00`, `llm_connection_id: UUID|None` (FK `agent_llm_connections`/tabela real de `LlmConnection`, SET NULL), `owner_user_id: UUID|None` (SET NULL), `provider_category_map: dict` (JSON, default `{}`), `last_run_at: datetime|None`.
  - `CategorizationRun`: `id`, `workspace_id`, `kind` (`classify`|`organize`|`redistribute`), `trigger` (`ingest`|`manual`|`backlog`), `status` (`queued`|`running`|`processing_batch`|`done`|`failed`|`capped`), `model: str|None`, `batch_id: str|None`, `is_batch: bool`, `input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens` (int, default 0), `cost_usd: Decimal(10,6)=0`, `stats: dict` (JSON: `applied`, `suggested`, `memory`, `skipped`), `detail: dict` (JSON; organizador guarda a proposta aqui), `error: str|None`, `created_at`, `finished_at`.
  - `CategorizationEvent`: `id`, `workspace_id`, `transaction_id` (CASCADE), `run_id` (FK runs, SET NULL), `source`, `previous_source`, `previous_category_id`/`new_category_id` (SET NULL), `previous_payee_id`/`new_payee_id` (SET NULL), `confidence: int|None`, `reason: str|None` (String(120)), `model: str|None`, `user_id` (SET NULL), `created_at`, `undone_at: datetime|None`. Índices: `(workspace_id, created_at)`, `(transaction_id, created_at)`, `(run_id)`.
  - `CategorizationSuggestion`: `id`, `workspace_id`, `transaction_id` (CASCADE), `run_id` (SET NULL), `merchant_key: str`, `clean_name: str|None`, `category_id`/`alt_category_id` (SET NULL), `confidence: int|None`, `reason: str|None`, `new_category: dict|None` (JSON `{group, name}`), `status` (`pending`|`accepted`|`dismissed`), `created_at`, `resolved_at`. Unique parcial lógico: no máximo uma `pending` por transação (índice `(transaction_id, status)`).
  - `MerchantMemory`: `id`, `workspace_id`, `merchant_key` (unique por workspace), `counts: dict[str,int]` (JSON category_id→n), `last_user_category_id` (SET NULL), `ai_category_id` (SET NULL), `ai_confidence: int|None`, `ai_clean_name: str|None`, `updated_at`.
  - `Transaction.category_source: Mapped[str]` `String(16)`, `server_default="none"`, indexado com `workspace_id`.
- Migração: cria as 5 tabelas, adiciona a coluna, e faz `UPDATE transactions SET category_source='provider' WHERE category_id IS NOT NULL` (decisão §14.1 do spec).

- [ ] **Step 1: Testes** — `test_category_source_defaults_to_none` (cria Transaction sem categoria → `category_source == "none"`); `test_settings_unique_per_workspace` (segunda linha para o mesmo workspace → `IntegrityError`); `test_delete_category_with_events_and_suggestions_postgres` (usa `postgres_sessions`: cria categoria, transação, evento e sugestão apontando para ela, apaga a categoria → evento e sugestão continuam com `new_category_id`/`category_id` nulos); `test_delete_transaction_cascades_events_postgres`.
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_models.py -v` → FAIL (import error).
- [ ] **Step 3: Implementar models e migração** seguindo `ReconciliationEvent`/`InvoiceSettings` como molde (docstring curta explicando o porquê, `CheckConstraint` para enums).
- [ ] **Step 4: Rodar** — testes PASS; `python3 scripts/check_migration_chain.py` OK; `uv run --no-sync alembic upgrade head` contra o Postgres de teste sem erro e `alembic downgrade 096` reverte.
- [ ] **Step 5: Commit** — `feat(categorization): tabelas de proveniência, memória, sugestões e runs`

### Task 2: Serviço de eventos (registrar, proteger, desfazer)

**Files:**
- Create: `backend/app/services/categorization/__init__.py`, `backend/app/services/categorization/events.py`
- Test: `backend/tests/test_categorization_events.py`

**Interfaces:**
- Produces (`app.services.categorization.events`):
  - `PROTECTED_SOURCES: frozenset[str] = {"user", "legacy"}`; `REVIEWABLE_SOURCES: frozenset[str] = {"none", "provider", "memory", "ai"}`.
  - `def is_protected(tx: Transaction) -> bool`.
  - `async def record_change(session, tx: Transaction, *, source: str, new_category_id: UUID|None, new_payee_id: UUID|None|Unset = UNSET, confidence: int|None = None, reason: str|None = None, model: str|None = None, run_id: UUID|None = None, user_id: UUID|None = None) -> CategorizationEvent|None` — aplica a mudança em `tx` (category_id, payee_id se passado, category_source), grava o evento com os `previous_*`; não faz commit; retorna `None` (sem evento) quando nada muda; nunca lança (loga e retorna `None` em erro de escrita do evento, mas a mudança na transação é aplicada).
  - `async def record_bulk(session, workspace_id, tx_ids: list[UUID], *, source: str, new_category_id: UUID|None, user_id: UUID|None, run_id: UUID|None = None) -> int` — SELECT dos valores anteriores e um evento por linha que mudou.
  - `async def undo(session, workspace_id, *, event_ids: list[UUID]|None = None, run_id: UUID|None = None, user_id: UUID|None) -> UndoResult` — `UndoResult(restored: int, skipped: int)`. Restaura `previous_category_id`, `previous_payee_id` (só se o evento mudou payee) e `previous_source`; marca `undone_at`; grava evento `undo`. Pula (skipped) quando existe evento posterior não desfeito na mesma transação.
  - Quando `source == "user"`, `record_change`/`record_bulk` chamam `memory.learn_choice` (Task 5) — nesta task, deixar o gancho via função injetável `on_user_choice` vazia; a Task 5 liga.

- [ ] **Step 1: Testes** — `test_record_change_sets_source_and_previous_values`; `test_record_change_noop_returns_none`; `test_record_bulk_writes_one_event_per_changed_row`; `test_undo_by_run_restores_previous_category_and_source`; `test_undo_skips_when_transaction_changed_after_event` (evento `ai`, depois evento `user` na mesma transação; desfazer o `ai` → `skipped == 1`, categoria continua a do usuário); `test_is_protected`.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** no padrão de `reconciliation_history_service.record` (sem commit).
- [ ] **Step 4: Rodar** — PASS.
- [ ] **Step 5: Commit** — `feat(categorization): registro de eventos e desfazer`

### Task 3: Instrumentar todos os caminhos que gravam categoria

**Files:**
- Modify: `backend/app/services/transaction_service.py` (`_apply_update_to_row`/`update_transaction` incl. propagação a parcelas e cópia para o par de transferência; `bulk_update_category`; criação manual e série de parcelas)
- Modify: `backend/app/services/rule_service.py` (`apply_rules_to_transaction`, `apply_single_rule`, `apply_all_rules`), `backend/app/services/rule_engine.py` se a escrita única precisar retornar o que mudou
- Modify: `backend/app/services/connection_service.py` (fallbacks de categoria no OAuth callback ~1249 e no sync ~2394; separar herança de parcela de Pluggy; placeholder recorrente ~2357)
- Modify: `backend/app/services/import_service.py` (~940-1050), `backend/app/services/recurring_transaction_service.py` (~394), `backend/app/services/category_service.py` (`_transfer_category_references`)
- Test: `backend/tests/test_categorization_write_paths.py`

**Interfaces:**
- Consumes: `record_change`, `record_bulk`, `is_protected` (Task 2).
- Origens gravadas conforme a tabela do spec §6. Herança de parcela: `installment` quando a parcela anterior não é `user`; se a anterior é `user`, grava `user` (protegida).
- `apply_single_rule(..., overwrite_existing_categories=True)` e `apply_all_rules` passam a **pular transações protegidas**; `apply_all_rules` deixa de zerar notas de transações protegidas (corrige `rule_service.py:1488-1491`).
- `_transfer_category_references` registra um evento por transação movida mantendo a origem atual (`source` = origem anterior).

- [ ] **Step 1: Testes** (um por caminho, assert em `category_source` e no evento): `test_manual_update_marks_user`, `test_installment_propagation_marks_user`, `test_paired_transfer_copies_source`, `test_bulk_categorize_marks_user_with_events`, `test_rule_on_ingest_marks_rule`, `test_apply_single_rule_overwrite_skips_user`, `test_apply_all_rules_preserves_user_category_and_notes`, `test_sync_pluggy_category_marks_provider`, `test_sync_installment_inheritance_marks_installment`, `test_import_user_choice_marks_user`, `test_import_csv_column_marks_import`, `test_recurring_pending_marks_recurring`, `test_delete_category_transfer_records_events`.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar** chamando o serviço de eventos em cada ponto; nenhum caminho novo de escrita de `category_id` fora do serviço.
- [ ] **Step 4: Rodar a suíte inteira do backend** — tudo PASS (os testes existentes de sync/import/regras continuam verdes).
- [ ] **Step 5: Commit** — `feat(categorization): proveniência em todos os caminhos de categoria`

## Fatia 2 — Memória, pipeline sem IA e API de revisão

### Task 4: Chave de loja

**Files:**
- Create: `backend/app/services/categorization/merchant_key.py`
- Test: `backend/tests/test_merchant_key.py`

**Interfaces:**
- Produces: `def merchant_key(description: str|None, *, payee_name: str|None = None, payee_source: str|None = None, is_person_transfer: bool = False) -> str`; `def scrub_documents(text: str) -> str` (remove CPF/CNPJ com e sem pontuação); `def sample_description(text: str|None) -> str` (scrub + trunca em 60).
- Regra: payee com `payee_source == "manual"` → chave `P:<NOME NORMALIZADO>`; transferência para pessoa → `PIX:<NOME>` (se o nome for só documento → `PIX:#<sha1[:10]>`); senão, descrição normalizada.

- [ ] **Step 1: Testes parametrizados** — `"IFOOD *REST 8812"` e `"IFOOD*CLUB 4410"` → `"IFOOD"`; `"PG *UBER TRIP 1234 SAO PAULO BR"` → `"UBER TRIP"`; `"MP *MERCADOLIVRE PARC 03/10"` → `"MERCADOLIVRE"`; `"PAYPAL *NETFLIX.COM"` → `"NETFLIX.COM"`; `"Padaria São José"` → `"PADARIA SAO JOSE"`; Pix para `"MARIA S SILVA"` → `"PIX:MARIA S SILVA"`; Pix com nome `"123.456.789-09"` → começa com `"PIX:#"` e não contém dígitos do documento; payee manual "Academia Smart" → `"P:ACADEMIA SMART"`; `scrub_documents("TED 12.345.678/0001-90 X")` não contém `12345678000190`.
- [ ] **Step 2: Rodar e ver falhar.** — [ ] **Step 3: Implementar** (função pura; lista de prefixos e sufixos de cidade/UF como constantes no módulo). — [ ] **Step 4: Rodar** PASS.
- [ ] **Step 5: Commit** — `feat(categorization): chave de loja normalizada`

### Task 5: Memória

**Files:**
- Create: `backend/app/services/categorization/memory.py`
- Modify: `backend/app/services/categorization/events.py` (ligar `on_user_choice`)
- Test: `backend/tests/test_categorization_memory.py`

**Interfaces:**
- Produces: `async def learn_choice(session, workspace_id, key: str, category_id: UUID) -> None`; `async def lookup(session, workspace_id, keys: list[str]) -> dict[str, MemoryHit]` onde `MemoryHit(category_id: UUID|None, consistent: bool, distribution: dict[str,int], ai_category_id: UUID|None, ai_confidence: int|None, ai_clean_name: str|None)`; `def is_consistent(counts: dict[str,int]) -> tuple[UUID|None, bool]` (≥ 2 e ≥ 80%); `async def remember_ai_decision(session, workspace_id, key, category_id, confidence, clean_name) -> None`; `async def forget_ai_decisions(session, workspace_id, keys: list[str]|None = None) -> None` (None = todas; usado quando a árvore muda).
- `learn_choice` também limpa `ai_*` daquela chave.

- [ ] **Step 1: Testes** — `test_consistency_threshold` (`{a:4,b:1}` → a, True; `{a:1}` → False; `{a:3,b:2}` → False); `test_user_edit_feeds_memory` (edição manual via `update_transaction` incrementa a contagem da chave); `test_ai_events_do_not_feed_memory`; `test_learn_choice_clears_ai_cache`.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): memória por loja`

### Task 6: Configurações, pipeline sem IA e task Celery

**Files:**
- Create: `backend/app/services/categorization/settings.py`, `backend/app/services/categorization/pipeline.py`, `backend/app/tasks/categorization_tasks.py`
- Modify: `backend/app/worker.py` (`conf.include` + `beat_schedule` entrada `categorization-poll-batches` a cada 5 min), `connection_service.py` (após commit do OAuth ~1294 e do sync ~2496), `import_service.py` (após commit ~1067)
- Test: `backend/tests/test_categorization_pipeline.py`, `backend/tests/test_categorization_tasks.py`

**Interfaces:**
- `settings.py`: `async def get_settings(session, workspace_id) -> CategorizationSettings` (cria com defaults se não existe); `async def update_settings(session, workspace_id, patch: CategorizationSettingsUpdate) -> CategorizationSettings`.
- `pipeline.py`:
  - `async def collect_candidates(session, workspace_id) -> list[Candidate]` — transações `posted`, não transferência pareada, `category_source in REVIEWABLE_SOURCES`, sem sugestão `pending` nem `dismissed` para a mesma categoria atual; `Candidate(tx_id, key, description, payee_name, amount, type, account_type, date, current_category_id)`.
  - `def group_by_key(cands) -> dict[str, MerchantGroup]` — `MerchantGroup(key, tx_ids, samples: list[str] (≤2, via sample_description), count, median_amount, type, recurring: bool, account_type)`; `recurring` = ≥ 3 meses distintos com valor ±5%.
  - `async def memory_pass(session, workspace_id, groups, run_id) -> dict[str, MerchantGroup]` — aplica memória consistente (`source="memory"`) e a decisão de IA em cache com confiança ≥ limite (`source="ai"`, `model` do cache); retorna o restante.
  - `async def suggest_without_ai(session, workspace_id, groups, run_id) -> int` — cria sugestões `pending` sem categoria (usado quando não há conexão Claude ou teto atingido).
  - `async def run_categorization(session, workspace_id, *, trigger: str, use_ai: bool) -> CategorizationRun` — orquestra: candidatas → grupos → memória → (Task 10: Claude) ou `suggest_without_ai`; preenche `stats`.
- `categorization_tasks.py`: `categorize_workspace(workspace_id: str, trigger: str)` (padrão `asyncio.run` + `NullPool` + `use_timezone`; debounce: sai se há run `queued`/`running` do mesmo workspace); `poll_batches()` (Task 10 implementa o corpo); `def enqueue_categorization(workspace_id: UUID, trigger: str = "ingest") -> None` (helper com `celery_app.send_task` em `try/except`, nunca lança).

- [ ] **Step 1: Testes** — `test_collect_candidates_includes_none_and_provider_excludes_user`; `test_pipeline_skips_protected_sources`; `test_group_by_key_merges_ifood_variants_and_detects_recurring`; `test_memory_pass_applies_consistent_memory_as_memory_source`; `test_memory_pass_reuses_cached_ai_decision`; `test_run_without_ai_creates_pending_suggestions`; `test_enqueue_never_raises_when_broker_down`; `test_categorize_task_debounces` (função async interna com `patch`, padrão de `tests/test_sync_tasks.py`).
- [ ] **Step 2–4:** falhar → implementar → PASS (inclui suíte de sync/import existente verde).
- [ ] **Step 5: Commit** — `feat(categorization): pipeline com memória e enfileiramento pós-ingestão`

### Task 7: API de revisão, aplicadas, desfazer e resumo

**Files:**
- Create: `backend/app/api/categorization.py`, `backend/app/schemas/categorization.py`, `backend/app/services/categorization/review.py`
- Modify: `backend/app/main.py` (router), `backend/tests/api_contract/baseline.json` (+ `cases.py` se precisar de body), `backend/app/api/transactions.py` + `transaction_service` (filtro `category_source` em `GET /api/transactions`, aceita lista separada por vírgula)
- Test: `backend/tests/test_categorization_api.py`

**Interfaces (prefixo `/api/categorization`):**
- `GET /summary` → `{pending_groups, pending_transactions, applied_last_7d, last_run: {id,status,finished_at,stats}|null, ai_available: bool, month_cost_usd, monthly_cap_usd}` (alimenta badge do nav e card do dashboard).
- `GET /review` → `[{merchant_key, clean_name, count, total, samples, suggestion: {category_id, alt_category_id, confidence, reason}|null, new_category: {group,name}|null, is_person: bool, transaction_ids}]` ordenado por `total` desc; `?limit=50&offset=`.
- `POST /review/accept` `{merchant_key, category_id, transaction_ids?: [...]}` → aplica como `user` (aprende memória), marca sugestões `accepted`; retorna `{applied, event_run_id}`.
- `POST /review/dismiss` `{merchant_key}` → sugestões `dismissed`.
- `GET /review` também devolve `new_category_proposals: [{group, name, merchant_keys, count, total}]` — só propostas em que ≥ 3 lojas pendentes sugerem o mesmo `{group, name}` (comparação normalizada).
- `POST /review/create-category` `{group, name, merchant_keys}` → cria o grupo se não existir e a categoria (via serviços existentes, `group_id` validado), aplica a todas as transações pendentes dessas lojas como `user`, `forget_ai_decisions`; retorna `{category_id, applied}`.
- `GET /applied` → eventos não desfeitos de origem `ai`/`memory`/`rule` paginados, `?source=ai,memory&min_confidence=&max_confidence=&since=`; cada item `{event_id, run_id, transaction: {id, date, description, amount, currency}, source, previous_category_id, new_category_id, confidence, reason, created_at}`.
- `POST /undo` `{event_ids?: [...], run_id?: ...}` → `{restored, skipped}`.
- `GET /transactions/{tx_id}/provenance` → `{current_source, original_description, events: [...últimos 5]}`.
- `POST /similar` `{transaction_id}` → `{merchant_key, count, transaction_ids}` (mesma chave, não protegidas, categoria diferente da atual da transação de referência); `POST /similar/apply` `{transaction_id}` → aplica a categoria da referência como `user`.
- `GET /rule-draft/{tx_id}` → `{name, conditions: [{field:"description", op:"contains", value: <merchant_key sem prefixo>}], actions: [{type:"set_category", value: category_id}]}`.
- `GET|PUT /settings` (PUT exige papel de gestão: usar a dependência de `canManage` existente no backend; se não houver, `current_writable_workspace` + checagem de `ctx.role in {"owner","manager"}` → 403).
- `POST /run` `{scope: "backlog"}` → cria run e enfileira (`trigger="manual"`); retorna o run.

- [ ] **Step 1: Testes** — um por rota feliz + `viewer` recebe 403 nas de escrita; `test_accept_learns_memory_and_marks_user`; `test_undo_run_endpoint`; `test_similar_excludes_protected`; `test_rule_draft_uses_merchant_key`; `test_transactions_filter_by_category_source`.
- [ ] **Step 2–4:** falhar → implementar → PASS; regenerar baseline do contrato; meta-teste de permissão PASS.
- [ ] **Step 5: Commit** — `feat(categorization): API de revisão, auditoria e desfazer`

## Fatia 3 — Claude

### Task 8: Cliente Claude, custos e chave

**Files:**
- Modify: `backend/pyproject.toml` (+ `anthropic>=1,<2`), `backend/uv.lock` (via `scripts/lock.sh`)
- Create: `backend/app/services/categorization/claude_client.py`, `backend/app/services/categorization/costs.py`
- Test: `backend/tests/test_categorization_claude_client.py`

**Interfaces:**
- `costs.py`: `PRICES: dict[str, ModelPrice]` com os valores das Global Constraints; `def cost_usd(model: str, usage, *, batch: bool) -> Decimal` (usa `usage.input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`); `async def month_spend(session, workspace_id) -> Decimal` (soma `categorization_runs.cost_usd` do mês corrente no fuso do workspace).
- `claude_client.py`:
  - `async def resolve_client(session, settings: CategorizationSettings) -> AsyncAnthropic|None` — usa `settings.llm_connection_id` ou a conexão padrão do `owner_user_id` **filtrando `kind == "anthropic"`**; decripta com o helper de `app/agents/services/crypto.py`; `None` se não houver.
  - `async def create_message(client, params: dict) -> Message` (wrapper fino de `client.messages.create`, `max_retries=2`).
  - `async def create_batch(client, requests: list[Request]) -> str`; `async def batch_status(client, batch_id) -> str`; `async def batch_results(client, batch_id) -> AsyncIterator[(custom_id, Message|None, error_type|None)]`.
  - Tipos: `from anthropic.types.message_create_params import MessageCreateParamsNonStreaming`, `from anthropic.types.messages.batch_create_params import Request` (conforme a documentação do SDK; conferir no pacote instalado).

- [ ] **Step 1: Testes** — `test_cost_usd_haiku_batch_halves` (1M entrada + 1M saída Haiku em batch = 0.30); `test_cost_usd_counts_cache_read_and_write`; `test_resolve_client_ignores_non_anthropic_connections`; `test_resolve_client_none_without_connection`; `test_month_spend_sums_current_month_only`.
- [ ] **Step 2–4:** falhar → implementar → PASS; `uv sync --locked` OK.
- [ ] **Step 5: Commit** — `feat(categorization): cliente Claude e cálculo de custo`

### Task 9: Prompt e schema do categorizador

**Files:**
- Create: `backend/app/services/categorization/prompts.py`
- Modify: spec (`docs/superpowers/specs/2026-10-19-ai-categorization-design.md` §8 e §10 — registrar o desvio do padrão de regra)
- Test: `backend/tests/test_categorization_prompts.py`

**Interfaces:**
- `def build_category_index(categories, groups) -> CategoryIndex` — IDs curtos `c1..cN` em ordem determinística (grupo.position, grupo.name, categoria.name); só categorias atribuíveis (não ocultas); `CategoryIndex.to_short(uuid)`, `.from_short(str) -> UUID|None`, `.render() -> str` (linhas `c3 Lazer › Cinema`).
- `def build_examples(memory_rows, index, limit=40) -> str` — linhas `IFOOD -> c7`, ordenadas por total de escolhas desc e chave.
- `def build_system_blocks(index, examples) -> list[dict]` — bloco 1: instruções fixas (pt-BR, regras: escolha só IDs da lista; `p` honesto; nome limpo curto; `new` só quando nada serve; Pix para pessoa sem contexto → `c: null`, `p ≤ 40`); bloco 2: árvore + exemplos, com `cache_control: {"type": "ephemeral"}` no último bloco. Nada volátil.
- `def build_user_message(groups: list[MerchantGroup]) -> str` — JSON compacto `[{"k","s":[...],"n":count,"v":median,"t":"d|c","r":0|1,"a":"cc|ca"}]` (chaves curtas, sem espaços).
- `RESULT_SCHEMA: dict` — `{"type":"object","properties":{"items":{"type":"array","items":{"type":"object","properties":{"k":{"type":"string"},"c":{"type":["string","null"]},"alt":{"type":["string","null"]},"n":{"type":"string"},"p":{"type":"integer"},"r":{"type":"string"},"new":{"type":["object","null"],"properties":{"g":{"type":"string"},"name":{"type":"string"}},"required":["g","name"],"additionalProperties":false}},"required":["k","c","alt","n","p","r","new"],"additionalProperties":false}}},"required":["items"],"additionalProperties":false}`.
- `def classify_params(system_blocks, user_message, n_items) -> dict` — `model="claude-haiku-5-5"`, `max_tokens=min(16000, 80 * n_items + 200)`, `thinking={"type":"disabled"}`, `output_config={"effort":"low","format":{"type":"json_schema","schema":RESULT_SCHEMA}}`.

- [ ] **Step 1: Testes** — `test_category_index_is_deterministic_and_skips_hidden`; `test_system_blocks_are_byte_identical_across_calls` (mesmo input → mesmo JSON serializado; nenhuma data/uuid no texto); `test_user_message_has_no_documents` (grupo com CPF no sample → ausente); `test_classify_params_uses_haiku_low_effort_no_thinking`; `test_max_tokens_scales_with_items`.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): prompt cacheável e schema de saída`

### Task 10: Classificador (síncrono e em lote) + teto mensal

**Files:**
- Create: `backend/app/services/categorization/classifier.py`
- Modify: `pipeline.py` (`run_categorization` chama o classificador quando `use_ai`), `categorization_tasks.py` (`poll_batches`)
- Test: `backend/tests/test_categorization_classifier.py`

**Interfaces:**
- `async def classify(session, workspace_id, groups: dict[str, MerchantGroup], run: CategorizationRun, *, client) -> None` — checa `month_spend + estimativa` vs `monthly_cap_usd` antes de qualquer chamada (estimativa = tokens aproximados × preço); se estoura → `run.status="capped"`, `suggest_without_ai` para os grupos e retorna. Lotes de ≤ 100 grupos. Até 100 grupos no total → `create_message` direto; acima → um batch com um `Request` por lote (`custom_id = f"{run.id}:{i}"`), `run.status="processing_batch"`, `run.batch_id`.
- `async def apply_results(session, workspace_id, run, groups_by_key, items: list[dict], *, index, threshold, model) -> ApplyStats` — para cada item: ignora `k` desconhecida; `from_short` inválido → trata como `null`; `p ≥ threshold` e `c` válido → `record_change(source="ai", confidence=p, reason=r[:120], model=model, run_id=run.id)` em todas as transações do grupo + `remember_ai_decision`; senão → sugestão `pending` com `c`/`alt`; `n` → payee: reaproveita payee com o mesmo nome (case-insensitive) ou cria com `source="ai"`, nunca renomeia/funde, não troca payee de transação cuja `payee_id` atual tem `source == "manual"`; `new` → grava em `new_category` da sugestão. Grupos sem item na resposta → sugestão sem categoria.
- `stop_reason == "refusal"` ou `"max_tokens"` → todos os grupos do lote viram sugestão sem categoria; `run.stats["skipped"]` incrementa.
- Usage → `run.input_tokens` etc. e `run.cost_usd += cost_usd(model, usage, batch=...)`.
- `poll_batches()`: para cada run `processing_batch`, `batch_status`; se `ended`, itera `batch_results` e chama `apply_results` por lote (resultado `errored`/`expired` → grupos daquele lote viram sugestão), marca `done`.

- [ ] **Step 1: Testes** (SDK mockado devolvendo objetos com `.content[0].text`, `.stop_reason`, `.usage`): `test_high_confidence_applies_as_ai_with_reason`; `test_low_confidence_creates_suggestion_with_alt`; `test_apply_results_drops_unknown_ids_and_handles_refusal`; `test_clean_name_reuses_payee_and_never_renames`; `test_manual_payee_is_kept`; `test_classifier_stops_when_cap_reached`; `test_large_backlog_uses_batch_and_poll_applies`; `test_run_cost_accumulates`.
- [ ] **Step 2–4:** falhar → implementar → PASS; suíte inteira verde.
- [ ] **Step 5: Commit** — `feat(categorization): categorizador Claude com lotes e teto mensal`

## Fatia 4 — Interface

> Em todas as tasks de frontend: novas chaves em `categorization.*` nos 19 locales; `npm test` inclui `i18n.test.ts` e os testes de contrato de páginas/componentes. Copy em pt-BR curta e direta.

### Task 11: Cliente de API, tipos, nav com contador e casca da página

**Files:**
- Modify: `frontend/src/lib/api.ts` (namespace `categorization` com uma função por rota da Task 7), `frontend/src/types/index.ts` (`CategorySource`, `CategorizationSummary`, `ReviewGroup`, `AppliedEvent`, `Provenance`, `CategorizationSettings`, `OrganizerProposal`), `frontend/src/lib/nav-items.ts` (item `categorization` na seção Contas, abaixo de Transações, ícone `Sparkles`, `badgeQueryKey: ['categorization','summary']` lendo `pending_groups`), `frontend/src/components/app-layout.tsx` (render do pill âmbar `min-w-[18px] h-[18px] rounded-full bg-amber-500 text-white text-[10px]`, oculto quando 0), `frontend/src/lib/modules.ts` se o item exigir módulo, `frontend/src/App.tsx` (rota lazy `/categorization` com `ModuleRoute module="categories"`), `frontend/src/components/command-palette.tsx` (`NAV_ITEMS` "Categorização" keywords `categorizar revisar ia`), `frontend/src/lib/invalidate-queries.ts` (`invalidateCategorizationQueries`)
- Create: `frontend/src/pages/categorization.tsx` (PageHeader + `Segmented` `?tab=review|applied|organize` + header de status: último run, "Categorizar agora", link Configurações)
- Test: `frontend/src/pages/categorization.test.tsx`

**Interfaces:**
- Query keys: `['categorization','summary']`, `['categorization','review']`, `['categorization','applied', filters]`, `['categorization','provenance', txId]`, `['categorization','settings']`, `['categorization','organizer']`.
- "Categorizar agora": `categorization.run({scope:'backlog'})`, toast "Categorização iniciada"; enquanto houver run `queued|running|processing_batch`, `refetchInterval: 5000` no summary e o botão mostra `Loader2` "Categorizando…".
- Sem conexão Claude (`ai_available=false`): faixa discreta no header "IA desligada — só regras e memória · Configurar" (link para Configurações).

- [ ] **Step 1: Teste** — renderiza com summary mockado: aba padrão `review`; badge do nav mostra `3`; com `ai_available=false` mostra a faixa.
- [ ] **Step 2–4:** falhar → implementar → PASS + lint + build.
- [ ] **Step 5: Commit** — `feat(categorization): página de categorização e contador no menu`

### Task 12: Aba Revisar

**Files:**
- Create: `frontend/src/components/categorization/review-tab.tsx`, `frontend/src/components/categorization/review-group-row.tsx`
- Test: `frontend/src/components/categorization/review-tab.test.tsx`

**Comportamento (molde `reconciliation-queue.tsx`):**
- `SectionCard`; header "Para revisar · N lojas"; ação "Aceitar todas as sugestões" (aceita todos os grupos que têm `suggestion.category_id`), com confirmação inline (botão vira "Confirmar (N)" por 4 s).
- Linha por loja: nome limpo (ou chave), `count` transações · `mask(formatCurrency(total))`, motivo em `text-muted-foreground`; ações à direita: até 2 `Button variant="outline" size="sm"` com `CategoryIcon size="xs"` + nome (palpite e alternativa), `CategorySelect` compacto "Outra…", botão ghost `X` "Ignorar". Clique na linha expande e lista as transações (data, descrição original, valor).
- Pessoa (`is_person`): texto "Quem é {nome}?" no lugar do motivo; mesmas ações.
- Sugestões de categoria nova: bloco no topo "Criar Lazer › Games? · 6 transações" com "Criar e aplicar" / "Agora não".
- Teclado (quando a lista tem foco): `↑/↓` move, `1`/`2` aceita palpite/alternativa, `Enter` expande, `x` ignora. Legenda de atalhos discreta no rodapé do card.
- Aceitar: otimista (remove a linha), toast `sonner` "Aplicado a 12 transações" com ação "Desfazer" (chama `undo` com o `event_run_id` devolvido). Erro → reverte e `toast.error(extractApiError(err))`.
- Vazio: `CircleCheck` verde "Tudo categorizado".
- Sem `canWrite`: só leitura (sem botões).

- [ ] **Step 1: Testes** — `aceitar palpite remove a linha e mostra toast com Desfazer`; `atalho 1 aceita o palpite da linha focada`; `pessoa mostra pergunta`; `viewer não vê botões`; `estado vazio`.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): aba Revisar com ações rápidas e atalhos`

### Task 13: Aba Aplicadas

**Files:**
- Create: `frontend/src/components/categorization/applied-tab.tsx`
- Test: `frontend/src/components/categorization/applied-tab.test.tsx`

**Comportamento (molde `reconciliation-history.tsx`):**
- Agrupado por dia ("Hoje", "Ontem", data) e, dentro do dia, por loja ("iFood · 8 → Alimentação › Delivery"); expande para as transações.
- Filtros no topo: chips de origem (IA · Memória · Regra) e confiança (Todas · < 90 · ≥ 90).
- Cada grupo do dia com ação "Desfazer este lote" (por `run_id`); cada transação com checkbox nativo `accent-primary`; barra fixa de seleção no rodapé (copiar o padrão de `transactions.tsx`: pill de contagem + "Desfazer selecionadas" + "Limpar"); mobile usa `MobileBulkSelectionActions`.
- Trocar categoria numa linha (`CategorySelect`) = correção do usuário (PATCH da transação) → some da lista de "aplicadas pela IA" e ensina a memória.
- Undo: toast "3 desfeitas" (e "1 ignorada porque você já tinha editado" quando `skipped > 0`).

- [ ] **Step 1: Testes** — `agrupa por dia e loja`; `filtro de origem refaz a query`; `desfazer selecionadas chama undo com os ids`; `skipped aparece no toast`.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): aba Aplicadas com desfazer em lote`

### Task 14: Selo de origem e filtro em Transações

**Files:**
- Create: `frontend/src/components/categorization-origin-badge.tsx` (molde `projected-transaction-badge.tsx`)
- Modify: `frontend/src/pages/transactions.tsx` (`renderDescriptionCell`, ao lado do badge de sugestão; filtro "Origem" na barra de filtros, sincronizado com `?category_source=`), `frontend/src/components/mobile-transaction-row.tsx`, `frontend/src/pages/account-detail.tsx`, `frontend/src/components/transaction-drill-down.tsx` (Tooltip), `frontend/src/components/transaction-calendar-view.tsx`, `frontend/src/components/transactions-filter-bar.tsx`
- Test: `frontend/src/components/categorization-origin-badge.test.tsx`

**Interfaces:**
- `<CategorizationOriginBadge source={tx.category_source} pending={boolean} reason?={string} compact?={boolean} />` — `ai` violeta `Sparkles` "IA"; `memory` sky `Brain` "Memória"; `rule` slate `Wand2` "Regra"; `pending` âmbar `HelpCircle` "Revisar" como `Link` para `/categorization?tab=review`; `user`/`legacy`/`none`/`provider`/`import`/`recurring`/`installment` → não renderiza. `Tooltip` com o motivo quando houver. `compact` = só ícone (mobile, `text-[9px]`).
- `Transaction` (types) ganha `category_source: CategorySource` (o backend já serializa a coluna — confirmar no schema de transação e adicionar se faltar, com teste de contrato).

- [ ] **Step 1: Testes** — renderiza ícone/rótulo por origem; não renderiza para `user`; pendente vira link; tooltip mostra motivo.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): selo de origem nas listas de transações`

### Task 15: "Como chegou aqui" e toast pós-correção

**Files:**
- Create: `frontend/src/components/categorization/provenance-block.tsx`, `frontend/src/hooks/use-category-correction-toast.ts`
- Modify: `frontend/src/components/transaction-dialog.tsx` (bloco logo abaixo do `CategorySelect`), `frontend/src/pages/transactions.tsx`, `frontend/src/pages/account-detail.tsx`, `frontend/src/pages/dashboard.tsx` (onSuccess das mutações de update quando `category_id` mudou e de `bulkCategorize`)
- Test: `frontend/src/components/categorization/provenance-block.test.tsx`, `frontend/src/hooks/use-category-correction-toast.test.ts`

**Comportamento:**
- Bloco (só quando `current_source` ∈ {ai, memory, rule, provider} ou há eventos): caixa `p-3 text-sm bg-muted/50 border rounded-md`; linha 1 "Categorizado por IA · 94% · há 2 dias"; linha 2 motivo; "Original: <descrição original do banco> · <categoria anterior ou Sem categoria>"; botão outline pequeno "Desfazer" (undo do último evento). Colapsado por padrão em uma linha com chevron quando a origem é `rule`/`provider`.
- Toast pós-correção (`useCategoryCorrectionToast()` → `notifyCorrection(tx)`): consulta `POST /similar`; se `count > 0`, `toast("Categoria atualizada", { action: { label: "Aplicar às N parecidas", onClick }, cancel: { label: "Criar regra", onClick } , duration: 8000 })`; "Criar regra" abre o `RuleDialog` com `initialData` vindo de `GET /rule-draft/{id}` e `applyToExisting: true`. Sem parecidas → só "Criar regra".

- [ ] **Step 1: Testes** — bloco mostra original × aplicado e desfaz; toast com N parecidas aplica; sem parecidas não mostra a ação de aplicar.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): proveniência no detalhe e atalhos após corrigir`

### Task 16: Card do dashboard e Configurações

**Files:**
- Create: `frontend/src/components/categorization/categorization-card.tsx`, `frontend/src/components/categorization/categorization-settings-section.tsx`
- Modify: `frontend/src/pages/dashboard.tsx` (substitui o banner de não categorizadas ~868-904 pelo card), `frontend/src/pages/workspace-settings.tsx` (nova `<section>` com `canManage`)
- Test: `frontend/src/components/categorization/categorization-card.test.tsx`, `.../categorization-settings-section.test.tsx`

**Comportamento:**
- Card: âmbar quando `pending_transactions > 0` ("5 para revisar · 47 categorizadas automaticamente esta semana →" abre `/categorization`), verde quando 0 ("Tudo categorizado · 47 automáticas esta semana"); `Skeleton` carregando; com `ai_available=false` e pendências, mantém o texto antigo de não categorizadas + link.
- Configurações: `ui/switch` "Categorização automática"; `Segmented` "Quando aplicar sozinho": Conservador (95) · Equilibrado (90) · Agressivo (80); `Input` numérico "Teto mensal (US$)" com "Gasto este mês: US$ 0,003"; linha "Conexão Claude: <nome> · Trocar" (link para `/agents/connections`); botão "Organizar categorias" (vai para `?tab=organize`).

- [ ] **Step 1: Testes** — estados âmbar/verde/skeleton; mudar o segmented chama PUT com `auto_apply_threshold: 95`; sem `canManage` o formulário fica desabilitado.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): card no dashboard e configurações`

## Fatia 5 — Organizador de categorias

### Task 17: Organizador (backend)

**Files:**
- Create: `backend/app/services/categorization/organizer.py`
- Modify: `backend/app/services/category_service.py` (validar que `group_id` pertence ao workspace em criar/editar — corrige `:153` e `:226`), `backend/app/services/connection_service.py` (`_match_pluggy_category` consulta `settings.provider_category_map` por id antes do nome), `backend/app/api/categorization.py`, `backend/app/tasks/categorization_tasks.py`, `backend/tests/api_contract/baseline.json`
- Test: `backend/tests/test_categorization_organizer.py`

**Interfaces:**
- `async def build_organizer_input(session, workspace_id) -> str` — árvore atual com IDs curtos + resumo por chave de loja (12 meses: `k`, nome limpo, `c` atual, quantidade, total) em JSON compacto; nunca transações cruas.
- `ORGANIZER_SCHEMA` — `{"ops": [{"id": "o1", "op": "create_group|create_category|rename|move_category|merge|split_category", "target": "<id curto ou null>", "group": "<id curto ou nome novo>", "name": "...", "children": ["..."], "into": "<id curto>", "why": "...", "impact": {"tx": int, "total": number}}]}` com `additionalProperties: false`.
- `def organizer_params(system, user) -> dict` — `model="claude-opus-5-5"`, `max_tokens=16000`, `thinking={"type":"adaptive"}`, `output_config={"effort":"medium","format":{...}}`, `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`; chamada via `client.beta.messages.create(...)` (não batch).
- `def filter_ops(ops, stats) -> list[dict]` — descarta categoria nova com < 3 transações e < R$ 200, grupos fora de 2–6 categorias, terceiro nível.
- Task `organize_workspace(workspace_id)` → run `kind="organize"`, proposta em `run.detail["ops"]` com `status` por op (`pending|applied|rejected`).
- `async def apply_op(session, workspace_id, run_id, op_id, user_id) -> dict` — `create_group`/`create_category`/`rename`/`move_category` via serviços existentes; `merge` via `delete_category(transfer_to_id=...)` (eventos já instrumentados na Task 3); `split_category`: cria o grupo, reaproveita o id da categoria antiga como a primeira filha (renomeia + move), cria as demais, e enfileira run `kind="redistribute"` que classifica as transações daquela categoria só entre as filhas (inclusive `user`; `source="ai"`, eventos com o mesmo `run_id`). Depois de qualquer op: `forget_ai_decisions(...)`; após o primeiro run do organizador, preenche `provider_category_map` com os ids resolvidos pelo nome atual.
- Rotas: `POST /organizer/run`, `GET /organizer` (último run organize com ops), `POST /organizer/ops/{op_id}/apply`, `POST /organizer/ops/{op_id}/reject`.

- [ ] **Step 1: Testes** — `test_group_id_from_other_workspace_is_rejected`; `test_pluggy_match_survives_category_rename`; `test_filter_ops_drops_tiny_categories_and_third_level`; `test_split_reuses_old_category_id_keeping_budgets_and_rules`; `test_split_redistribution_can_move_user_transactions_with_events`; `test_merge_moves_transactions_with_events`; `test_organizer_params_opus_with_fallbacks`.
- [ ] **Step 2–4:** falhar → implementar → PASS; contrato e permissões verdes.
- [ ] **Step 5: Commit** — `feat(categorization): organizador de categorias com Opus`

### Task 18: Aba Organizar (frontend)

**Files:**
- Create: `frontend/src/components/categorization/organize-tab.tsx`
- Modify: `frontend/src/pages/categories.tsx` (link "Organizar com IA" no `SectionHeader action`)
- Test: `frontend/src/components/categorization/organize-tab.test.tsx`

**Comportamento:**
- Sem proposta: card com explicação de uma linha + botão "Analisar minhas categorias" (mostra "Leva ~1 min · custo ~US$ 0,20"); durante o run, `Loader2` + polling.
- Com proposta: à esquerda a árvore resultante prevista (estilo `categories.tsx`, novos em verde, removidos riscados); à direita a lista de operações como `ProposalCard` (âmbar → verde ao aplicar) com "why" e impacto ("14 transações · R$ 820"), botões "Aplicar" / "Recusar"; "Aplicar todas" no topo. `merge` pede confirmação no Dialog no padrão de `payees.tsx` ("Mesclar é irreversível").
- Após aplicar: `invalidateCategoryQueries` + `invalidateCategorizationQueries`.

- [ ] **Step 1: Testes** — lista ops; aplicar chama a rota e o card fica verde; merge exige confirmação.
- [ ] **Step 2–4:** falhar → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(categorization): aba Organizar`

## Fatia 6 — Entrega no Tarsonis

### Task 19: Build, deploy e primeira execução (pedir confirmação ao usuário antes do push e do deploy)

- [ ] **Step 1:** Gates completos (backend e frontend) verdes, com a saída na tela.
- [ ] **Step 2:** Revisão final da branch inteira (reviewer independente).
- [ ] **Step 3:** `git push -u origin feat/ai-categorization` (fork do usuário).
- [ ] **Step 4:** No Tarsonis (arm64): `git clone --branch feat/ai-categorization https://github.com/danilosimei/securo /opt/securo-src` (ou `git pull`), `docker build -t securo-backend:ai-cat backend/` e `docker build -t securo-frontend:ai-cat frontend/`; conferir que as imagens sobem (`docker run --rm securo-backend:ai-cat python -c "import app.main"`).
- [ ] **Step 5:** Pela API do Portainer (stack id 10, env inteiro preservado): trocar as imagens de backend/worker/beat/frontend para as tags `:ai-cat` e adicionar `AGENTS_ENABLED=true`; `pullImage: false`. Backup do banco antes (`systemctl start docker-backup.service` e aguardar terminar).
- [ ] **Step 6:** Conferir `alembic current` = `097` no container do backend; criar a `LlmConnection` `kind="anthropic"` padrão pela API do Securo (a chave fica só no banco, cifrada); `GET /api/categorization/summary` com `ai_available: true`.
- [ ] **Step 7:** Rodar o backlog ("Categorizar agora"), acompanhar o batch até `done`, conferir custo do run (< US$ 0,05), contagens de aplicadas × sugestões, e abrir as telas no navegador (Revisar, Aplicadas, selo, bloco, card).
- [ ] **Step 8:** Rodar o organizador, revisar a proposta com o usuário antes de aplicar.
- [ ] **Step 9:** Atualizar `HANDOFF.md` e a memória (Tarsonis: imagens próprias, como rebuildar).
