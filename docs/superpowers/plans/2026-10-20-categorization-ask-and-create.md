# Categorização — perguntar à IA, criar na hora e aplicar às parecidas — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Na seção Categorização a pessoa conversa com a IA sobre uma loja, cria categoria/grupo sem sair da tela, é convidada a aplicar a escolha às transações parecidas e vê em Aplicadas tudo o que tem categoria, com placar; as chaves de loja deixam de juntar lojas atrás de prefixos de adquirente.

**Architecture:** Backend em `app/services/categorization/` ganha quatro módulos pequenos (`creation.py` criar-ou-reaproveitar, `choose.py` aplicar uma escolha, `ask_prompts.py` + `ask.py` conversa, `rekey.py` regravação de chaves) sobre a migração 099 (nota da loja, `kind='ask'`); a conversa é sem estado no servidor (o cliente reenvia o histórico) e é a única rota que chama o Claude no request. No frontend, `CategorySelect` aprende a criação estilo Notion e uma ação de rodapé, e entram `SimilarPrompt`, `StoreNote`, `AskProvider`/`AskDialog` e `Scoreboard`.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Celery, Redis, SDK `anthropic` 1.x (async), React + TS, shadcn/ui, Tailwind 4, react-query, sonner, i18next, vitest.

**Spec:** `docs/superpowers/specs/2026-10-20-categorization-ask-and-create-design.md` (ler inteiro, inclusive "Decisões tomadas na leitura do construtor"); complementa `docs/superpowers/specs/2026-10-19-ai-categorization-design.md` (já construído).

## Global Constraints

- Branch `feat/ai-categorization`; commits Conventional Commits com descrição em pt-BR, sem rastro de IA.
- Backend gate (em `backend/`): `uv run --no-sync ruff check .`, `uv run --no-sync ty check .`, `uv run --no-sync pytest -n auto --dist loadfile -ra -W error`. Testes com FK/locks/unicidade real usam `postgres_sessions` (`POSTGRES_TEST_URL=postgresql+asyncpg://securo_test:securo_test@127.0.0.1:55432/securo_test`, container `securo-pgtest`).
- Frontend gate (em `frontend/`): `npm run lint -- --max-warnings=0`, `npm run build`, `npm test`.
- Migração nova única: `backend/alembic/versions/099_merchant_note_and_ask_runs.py` (`revision="099"`, `down_revision="098"`); `python3 backend/scripts/check_migration_chain.py` passa. A 098 já tem `categorization_events.memory_pick`.
- Toda rota nova: escrita com `Depends(current_writable_workspace)` (meta-teste `tests/test_write_permission_coverage.py`), leitura com `Depends(current_workspace)`; `tests/api_contract/baseline.json` regenerada com `UPDATE_API_CONTRACT=1 uv run pytest tests/api_contract -n 0`. Erros feitos à mão usam `detail={"code": ..., "message": ...}` (o formato que `extractApiError` lê).
- Nenhuma rota chama o Claude no request, **exceto `POST /api/categorization/ask`** (spec §1): timeout de 15 s por mensagem; 20 mensagens por minuto por workspace; 30 mensagens da pessoa por conversa.
- Conversa: `claude-haiku-5-5`, `thinking={"type": "disabled"}`, `output_config={"effort": "low", "format": {"type": "json_schema", ...}}`, saída `{reply, suggestion?, note?}`; contexto fixo com `cache_control`; cada mensagem é um `CategorizationRun(kind="ask")` e conta no teto mensal. O resultado nunca grava sozinho a decisão em cache da IA (`MerchantMemory.ai_*`) nem sugestões.
- Privacidade: texto da pessoa e nota passam por `scrub_documents` + `strip_account_numbers` antes de qualquer envio; chaves de loja nunca vão à API (só ids curtos `c1`… e o contexto legível).
- Nota da loja: ≤ 200 caracteres, coluna da memória; salvar/editar/apagar esquece a decisão em cache da IA da loja e não conta escolha.
- Memória: uma aceitação conta **uma** escolha por loja (R16), e o evento que a carrega fica com `memory_pick=True` (`events.mark_picks`/`events.mark_latest_picks`), para o desfazer devolver exatamente essa escolha.
- Parecidas (só na seção Categorização): mesma loja, categoria diferente, origem em `none, provider, memory, ai, import, recurring, installment`; ficam fora `user`, `legacy`, `rule`. O toast de correção das outras telas não muda.
- Criação: nome comparado sem acento e sem caixa (grupo no workspace, categoria no grupo); existente visível/atribuível é reaproveitado.
- i18n: toda chave nova existe nos **17** `frontend/src/locales/*.json` (texto real em `en.json` e `pt-BR.json`; os demais em inglês); placeholders idênticos.
- UI: componentes existentes primeiro (`SectionCard`, `Segmented`, `CategoryIcon`, `CategorySelect`, `ui/*`); valores sempre `mask(formatCurrency(...))`; escrita só com `canWrite` (quem só lê vê o botão desabilitado com o motivo).

## Review Focus

1. Aceitar no chat depois que a loja mudou (linha categorizada à mão por outra aba, pendências já aceitas) — esperado: na Revisar, linhas protegidas ficam como estão e o aplicado conta só o resto; sem nenhuma linha, 404 e o diálogo avisa. Teste em Task 2 (`test_choose_pending_skips_rows_categorized_by_hand_since`).
2. A pessoa digita CPF, cartão ou conta no chat — esperado: nada disso aparece nos `params` enviados; a nota salva também sai limpa. Testes em Task 7 (`test_person_text_is_sanitized`) e Task 1 (`test_set_note_scrubs_documents_and_truncates`).
3. A IA propõe "saúde › farmácia" quando já existe "Saúde › Farmácia" — esperado: Aceitar reaproveita, nada duplicado; o cartão já mostra como existente. Testes em Task 2 (`test_choose_reuses_existing_names_ignoring_accents`) e Task 8 (`test_ask_new_category_matching_existing_becomes_category_id`).
4. "Aplicar às N" logo depois de aceitar: as N não podem incluir as que acabaram de ser aplicadas, as de regra nem as da pessoa, e o clique aplica exatamente as N mostradas. Testes em Task 3 (`test_similar_for_categorization_excludes_rule_and_person_rows`, `test_apply_similar_only_the_shown_rows`).
5. Regravação de chaves rodada duas vezes ou com lote em voo — esperado: segunda rodada não muda nada; workspace ocupado é pulado e reportado. Testes em Task 13 (`test_rekey_is_idempotent`, `test_rekey_skips_busy_workspace`).

---

## Fatia 1 — Nota da loja, criar na hora e parecidas

### Task 1: Esquema 099 e nota da loja (backend)

**Files:**
- Create: `backend/alembic/versions/099_merchant_note_and_ask_runs.py`
- Modify: `backend/app/models/categorization.py` (`MerchantMemory`, check de `CategorizationRun.kind`), `backend/app/services/categorization/merchant_key.py` (recebe `strip_account_numbers` e suas regexes vindas de `prompts.py`; `prompts.py` reexporta o nome), `backend/app/services/categorization/memory.py`, `backend/app/services/categorization/review.py` (`review_queue`, `provenance`), `backend/app/api/categorization.py`, `backend/app/schemas/categorization.py`, `tests/api_contract/baseline.json`
- Test: `backend/tests/test_categorization_models.py`, `backend/tests/test_categorization_memory.py`, `backend/tests/test_categorization_notes.py` (novo)

**Interfaces:**
- Produces:
  - `MerchantMemory.note: Mapped[Optional[str]]` `String(200)`; `MerchantMemory.note_updated_at: Mapped[Optional[datetime]]` `DateTime(timezone=True)`.
  - `app.models.categorization.RUN_KINDS = ("classify", "organize", "redistribute", "ask")`; o check `ck_categorization_run_kind` usa `in_clause("kind", RUN_KINDS)`. A 099 adiciona as duas colunas e recria o check (drop + create); downgrade inverso (apaga runs `ask` antes de recriar o check antigo).
  - `merchant_key.strip_account_numbers(text: str) -> str` (movida, mesmo comportamento) e `merchant_key.sanitize_text(text: str) -> str` = `strip_account_numbers(scrub_documents(text))` sem truncar.
  - `memory.NOTE_LENGTH = 200`; `MemoryHit.note: str | None = None` (preenchido por `lookup`).
  - `async def memory.set_note(session, workspace_id, key: str, note: str | None) -> MerchantMemory` — `sanitize_text`, corta em `NOTE_LENGTH`, vazio vira `None`; cria a linha se faltar (`counts={}`, via `_get_or_create`); só quando o texto muda: grava `note`, `note_updated_at=now` e zera `ai_category_id`/`ai_confidence` daquela chave. Não conta escolha nem marca `memory_pick`. Sem commit.
  - Rota `PUT /api/categorization/notes` (escrita) `NoteRequest{merchant_key: str 1..255, note: str|None max_length=400}` → `NoteRead{merchant_key: str, note: str|None}` (chave em `stored_key`).
  - `ReviewGroup.note: Optional[str]` (da memória da chave); `Provenance.store_note: Optional[NoteRead]` (chave da transação via `memory.key_for_transaction`; `None` quando não há nota).

- [ ] **Step 1: Testes** — models: `test_merchant_memory_note_defaults_to_null`, `test_run_kind_accepts_ask` (insere run `kind="ask"` sem erro; `kind="chat"` → `IntegrityError`). memory: `test_set_note_creates_row_without_picks` (sem linha → linha com `counts == {}` e `is_consistent({}) == (None, False)`), `test_set_note_scrubs_documents_and_truncates` (entrada `"Aurino jardineiro CPF 123.456.789-09 conta 12345-6 " + "x"*300` → resultado sem `"123.456.789-09"`, sem `"12345-6"`, `len <= 200`), `test_set_note_forgets_ai_decision` (`ai_category_id` preenchido → `None`), `test_set_note_unchanged_keeps_ai_decision`, `test_clearing_note_keeps_counts`, `test_lookup_returns_note`. API (`test_categorization_notes.py`, helpers importados de `tests.test_categorization_api`): `test_put_note_creates_and_clears`, `test_put_note_requires_write` (viewer → 403), `test_review_group_carries_note`, `test_provenance_carries_store_note`. Migração: `test_migration_099_round_trip_postgres` (`alembic upgrade head` / `downgrade 098` no Postgres de teste).
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_models.py tests/test_categorization_memory.py tests/test_categorization_notes.py -v` → FAIL (atributo/rota inexistente).
- [ ] **Step 3: Implementar** modelo, migração, `set_note`, rota e campos; `prompts.py` passa a importar `strip_account_numbers` de `merchant_key` (evita o ciclo `memory → prompts → memory`).
- [ ] **Step 4: Rodar** os testes acima + `tests/test_categorization_prompts.py` → PASS; `python3 scripts/check_migration_chain.py` OK; baseline regenerada; gate do backend verde.
- [ ] **Step 5: Commit** — `feat(categorization): nota da loja na memória e execuções da conversa no razão`

### Task 2: Criar na hora e aplicar uma escolha (`POST /choose`)

**Files:**
- Create: `backend/app/services/categorization/creation.py`, `backend/app/services/categorization/choose.py`
- Modify: `backend/app/services/categorization/review.py` (`create_category` passa a usar `creation.find_or_create_category`; `_find_or_create_group` sai daqui; `_assignable_category` → público `ensure_assignable`; `_apply_as_user` → público `apply_as_user`), `backend/app/api/categorization.py`, `backend/app/schemas/categorization.py`, `tests/api_contract/baseline.json`
- Test: `backend/tests/test_categorization_choose.py` (novo)

**Interfaces:**
- Consumes: `memory.set_note` (Task 1); `review.accept`, `review.apply_as_user`, `review.ensure_assignable`, `memory.keys_for`, `memory.learn_choice`, `events.mark_picks`.
- Produces (`creation.py`):
  - `@dataclass(frozen=True) class Created: category_id: UUID; group_id: UUID; created_group: bool; created_category: bool`.
  - `async def find_or_create_category(session, workspace_id, user_id, *, group: str, name: str) -> Created` — grupo visível de mesmo nome normalizado (`review._norm_name`) é reaproveitado (oculto não); categoria atribuível de mesmo nome no grupo é reaproveitada; o que for criado recebe `suggest_style`; se criou algo, `memory.forget_ai_decisions(session, workspace_id)` (a árvore mudou). Sem commit.
  - `def suggest_style(name: str, group_name: str, group_color: str | None) -> tuple[str, str]` → `(icon, color)`. Ícone: primeira entrada de `STYLE_KEYWORDS` cujo radical aparece em `fold(name)` e depois em `fold(group_name)`; senão `"circle-help"` (categoria) / `"folder"` (grupo, via `suggest_group_style(group_name) -> tuple[str, str]`). Tabela (radical → ícone, todos existentes em `frontend/src/lib/category-icons.ts`): `CARRO|AUTO|VEICUL→car`, `COMBUST|GASOL|POSTO→fuel`, `TRANSPORT|UBER|TAXI→bus`, `VIAG|HOTEL|PASSAG→plane`, `CASA|MORAD|ALUGU→house`, `JARDIN|PLANTA→tree-pine`, `MERCAD|SUPERMERC→shopping-cart`, `RESTAUR|ALIMENT|COMIDA|DELIVER→utensils-crossed`, `CAFE|PADAR→coffee`, `FARMAC|REMED→pill`, `SAUDE|MEDIC|CONSULT→stethoscope`, `ACADEM|ESPORT→dumbbell`, `PRESENT→gift`, `ROUPA|VESTU→shirt`, `PET|CACHOR|VETERIN→dog`, `FILHO|BEBE|ESCOLA→baby`, `EDUCA|CURSO|FACULD→graduation-cap`, `TRABALH|EMPRES|NEGOC→briefcase`, `ASSINAT|STREAM→tv`, `INTERNET|CELULAR→wifi`, `LUZ|ENERG→lightbulb`, `AGUA→droplets`, `JOGO|LAZER→gamepad-2`, `MANUTEN|REFORM→wrench`. Cor: a do grupo quando existe e não é o cinza padrão `"#6B7280"`; senão `PALETTE[int(sha1(fold(group_name)).hexdigest(), 16) % len(PALETTE)]` com `PALETTE = ("#6366f1", "#22c55e", "#f59e0b", "#ef4444", "#06b6d4", "#a855f7", "#ec4899", "#14b8a6")`.
- Produces (`choose.py`):
  - `@dataclass class ChooseOutcome: category_id: UUID; created_group: bool; created_category: bool; applied: review.Applied`.
  - `async def choose(session, workspace_id, user_id, *, merchant_key: str, category_id: UUID | None, new_category: tuple[str, str] | None, transaction_ids: list[UUID] | None, note: str | None | Unset = UNSET) -> ChooseOutcome` — exatamente um entre `category_id` e `new_category` (senão `ValueError`). `transaction_ids=None` (Revisar): `review.accept(...)` sobre as pendentes da loja (proteção de origem como hoje). Com ids (Aplicadas ou seleção): carrega as linhas do workspace (nenhuma → `review.NotFound`), aplica a **todas** como escolha da pessoa (é a ação explícita dela sobre essas linhas, inclusive `user`), resolve as sugestões pendentes delas como `accepted`, ensina uma escolha por chave distinta (`memory.keys_for`) e marca `memory_pick` no primeiro evento de cada chave. `note` presente → `memory.set_note` na `merchant_key`. Sem commit.
  - Rota `POST /api/categorization/choose` (escrita) `ChooseRequest{merchant_key: str 1..255, category_id: UUID|None, new_category: NewCategoryDraft|None, transaction_ids: list[UUID]|None max_length=500, note: str|None max_length=400}` (`note` ausente = não muda, `null` = apaga — via `model_fields_set`); `NewCategoryDraft{group: str 1..100, name: str 1..100}` → `ChooseResult{category_id, created_group, created_category, applied, event_ids}`. `ValueError` → 422, `NotFound` → 404.

- [ ] **Step 1: Testes** — `test_choose_creates_group_and_category_and_applies_pending` (`{"group": "Transporte", "name": "Carro"}` sem esse grupo → ambos criados, ícone `car`; as 2 pendentes ficam `user` na categoria; `counts == {cat: 1}`; exatamente 1 evento com `memory_pick`), `test_choose_reuses_existing_names_ignoring_accents` (existe "Saúde" › "Farmácia"; pedido `{"group": "saude", "name": "FARMACIA"}` → `created_group is False`, `created_category is False`, mesmos ids, contagem de grupos/categorias igual), `test_choose_new_group_with_same_named_category` (`{"group": "Nimbloo", "name": "Nimbloo"}`), `test_choose_hidden_group_is_not_reused`, `test_choose_pending_skips_rows_categorized_by_hand_since` (uma das pendentes virou `user` → `applied == 1`, a `user` intacta), `test_choose_with_transaction_ids_applies_to_those_rows_and_counts_one_pick` (2 linhas da loja, uma `ai`, outra `user` → ambas mudam; a contagem sobe 1; desfazer os dois eventos tira 1), `test_choose_saves_note_and_forgets_ai_cache`, `test_choose_rejects_both_or_neither` (422), `test_choose_requires_write` (403), `test_choose_unknown_rows_is_404`; `test_suggest_style` parametrizado: `("Carro", "Transporte", None)` → ícone `car`; `("Jardinagem", "Casa", "#22c55e")` → `("tree-pine", "#22c55e")`; `("Xyz", "Abc", None)` → ícone `circle-help` e a mesma cor nas duas chamadas.
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_choose.py -v` → FAIL.
- [ ] **Step 3: Implementar** os dois módulos, a rota e o refactor de `review.create_category` (comportamento igual: `test_create_category_applies_to_stores` continua verde).
- [ ] **Step 4: Rodar** `tests/test_categorization_choose.py tests/test_categorization_api.py` → PASS; baseline regenerada; gate verde.
- [ ] **Step 5: Commit** — `feat(categorization): escolher categoria nova ou existente para uma loja, reaproveitando nomes iguais`

### Task 3: Parecidas da seção Categorização (backend)

**Files:**
- Modify: `backend/app/services/categorization/review.py` (`similar`, `apply_similar`), `backend/app/api/categorization.py`, `backend/app/schemas/categorization.py`, `tests/api_contract/baseline.json`
- Test: `backend/tests/test_categorization_similar.py` (novo)

**Interfaces:**
- Produces:
  - `review.similar(session, workspace_id, tx_id, *, exclude_rule: bool = False) -> tuple[Transaction, str, list[SimilarRow]]` com `SimilarRow(id, date, description, amount, currency, type, category_id, category_source)`; `exclude_rule=True` acrescenta `"rule"` às origens excluídas (`PROTECTED_SOURCES`). Padrão `False`: o toast de correção das outras telas fica igual.
  - `review.apply_similar(session, workspace_id, user_id, tx_id, *, exclude_rule: bool = False, transaction_ids: list[UUID] | None = None) -> Applied` — com ids, aplica só à interseção com o conjunto parecido; continua ensinando uma escolha e marcando `memory_pick` em um evento.
  - `SimilarRequest` ganha `exclude_rule: bool = False` e `transaction_ids: Optional[list[UUID]] = Field(None, max_length=5000)` (só `/similar/apply` usa); `SimilarResult` ganha `transactions: list[SimilarTransaction]` (todas as N, mesma ordem de `transaction_ids`).

- [ ] **Step 1: Testes** — `test_similar_for_categorization_excludes_rule_and_person_rows` (mesma loja com uma linha por origem `none, provider, memory, ai, import, recurring, installment, rule, user, legacy`, todas em categoria ≠ da referência → com `exclude_rule` os ids são exatamente as 7 primeiras; sem, inclui `rule`), `test_similar_excludes_rows_already_at_the_category` (as recém-aplicadas, na mesma categoria da referência, não contam), `test_similar_lists_the_rows_it_counts` (`[t.id for t in transactions] == transaction_ids`, `count == len`), `test_apply_similar_only_the_shown_rows` (3 parecidas, pedido com 2 ids + 1 id de outra loja → `applied == 2`), `test_apply_similar_marks_one_pick` (um evento com `memory_pick`; desfazer tudo devolve a contagem anterior).
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_similar.py -v`.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** `tests/test_categorization_similar.py tests/test_categorization_api.py` → PASS (os testes antigos de `similar` seguem verdes); baseline; gate.
- [ ] **Step 5: Commit** — `feat(categorization): parecidas da categorização deixam regra e escolha da pessoa de fora e aplicam só as mostradas`

### Task 4: "Outra..." cria categoria e grupo (frontend)

**Files:**
- Modify: `frontend/src/lib/inline-create.ts` (+ `inline-create.test.ts`), `frontend/src/components/category-select.tsx` (+ `category-select.test.tsx`), `frontend/src/components/categorization/review-group-row.tsx`, `frontend/src/components/categorization/review-tab.tsx` (+ test), `frontend/src/components/categorization/applied-tab.tsx` (+ test), `frontend/src/lib/api.ts`, `frontend/src/types/index.ts`, `frontend/src/locales/*.json`

**Interfaces:**
- Consumes: `POST /api/categorization/choose` (Task 2).
- Produces:
  - types `NewCategoryDraft {group: string; name: string}`, `ChooseRequest {merchant_key: string; category_id?: string; new_category?: NewCategoryDraft; transaction_ids?: string[]; note?: string | null}`, `ChooseResult {category_id: string; created_group: boolean; created_category: boolean; applied: number; event_ids: string[]}`; `categorization.choose(payload: ChooseRequest): Promise<ChooseResult>`.
  - `export interface GroupedCreateDraft { group: string; name: string; groupExists: boolean }`; `export type GroupedCreateOption = { kind: 'inGroup'; draft: GroupedCreateDraft } | { kind: 'pickGroup'; name: string } | { kind: 'newGroup'; draft: GroupedCreateDraft }`; `export function groupedCreateOptions(search: string, categories: Category[], groups: CategoryGroup[], suggestedGroupId: string | null): GroupedCreateOption[]`. Regras (comparação com `normalizeText`): busca vazia → `[]`; com `/` → corta na primeira barra, `grupo / nome` ambos não vazios → só `[inGroup]` (nome do grupo existente quando casa, `groupExists` conforme), ou `[]` se o nome já existe nesse grupo; sem barra → `[]` se alguma categoria já tem esse nome; senão, na ordem: `inGroup` no grupo sugerido (se `suggestedGroupId` resolve), `pickGroup` (se há outro grupo além do sugerido), `newGroup {group: nome, name: nome}` (omitido se já existe grupo com esse nome).
  - `CategorySelect` novas props: `onCreateInGroup?: (draft: GroupedCreateDraft) => void` (presente → substitui a criação plana de `creatable`; só com `canWrite`), `suggestedGroupId?: string | null`, `footerAction?: { label: string; icon?: LucideIcon; disabled?: boolean; hint?: string; onSelect: () => void }` (último item da lista, sempre visível; `hint` vira `title`). `pickGroup` troca a lista por um segundo passo com os grupos (busca filtra; Escape volta ao primeiro passo); escolher um grupo chama `onCreateInGroup({group, name, groupExists: true})`.
  - Revisar: `ReviewGroupRow` recebe `onChoose(payload: Omit<ChooseRequest, 'merchant_key'>)`; o "Outra..." usa `onCreateInGroup={(d) => onChoose({ new_category: { group: d.group, name: d.name } })}` e `suggestedGroupId` = grupo de `suggestion.category_id`. `ReviewTab.choose(group, payload)` chama `categorization.choose({ merchant_key, ...payload })` **sem** `transaction_ids` (pendentes da loja, decisão 1), com o mesmo fluxo de `accept` (pendente, toast com desfazer, invalidação).
  - Aplicadas: o "Outra..." da linha usa `onCreateInGroup` → `choose({ merchant_key: event.merchant_key, transaction_ids: [event.transaction.id], new_category })`, depois o mesmo `fixed`/toast de `correct`.
  - i18n: `categorization.create.inGroup` ("Criar \"{{name}}\" em {{group}}"), `categorization.create.pickGroup` ("Criar \"{{name}}\" em outro grupo…"), `categorization.create.newGroup` ("Criar \"{{name}}\" como grupo novo"), `categorization.create.inNewGroup` ("Criar \"{{name}}\" no grupo novo {{group}}"), `categorization.create.chooseGroup` ("Escolha o grupo"), `categorization.create.created` ("Categoria criada e aplicada").

- [ ] **Step 1: Testes** — lib: `groupedCreateOptions('Carro', cats, groups, 'g-transporte')` → kinds `['inGroup', 'pickGroup', 'newGroup']`, primeiro `draft.group === 'Transporte'`; `'Transporte / Carro'` → `[{kind:'inGroup', draft:{group:'Transporte', name:'Carro', groupExists:true}}]`; `'Jardim / Rosas'` → um `inGroup` com `groupExists:false`; `'mercado'` com "Mercado" existente → `[]`; `'transporte / CARRO'` com Carro em Transporte → `[]`; `'Nimbloo'` com grupo "Nimbloo" existente → sem `newGroup`. Componente: digitar "Carro" mostra "Criar "Carro" em Transporte" e escolher chama `onCreateInGroup({group:'Transporte', name:'Carro', groupExists:true})`; `pickGroup` mostra os grupos e escolher "Casa" chama com `group:'Casa'`; viewer não vê opção de criar; `footerAction` aparece e chama `onSelect`, desabilitado com `title`. Revisar: criar no grupo chama `categorization.choose` com `{merchant_key, new_category:{group, name}}` e sem `transaction_ids`. Aplicadas: criar chama `choose` com `transaction_ids: [txId]`.
- [ ] **Step 2: Rodar e ver falhar** — `npm test -- inline-create category-select review-tab applied-tab`.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** os testes → PASS; gate do frontend verde.
- [ ] **Step 5: Commit** — `feat(categorization): criar categoria ou grupo direto no "Outra..." da revisão e das aplicadas`

### Task 5: Pergunta das parecidas (frontend)

**Files:**
- Create: `frontend/src/components/categorization/similar-prompt.tsx` (+ `similar-prompt.test.tsx`)
- Modify: `frontend/src/components/categorization/review-tab.tsx`, `review-group-row.tsx`, `applied-tab.tsx` (+ testes), `frontend/src/lib/api.ts`, `frontend/src/types/index.ts`, `frontend/src/locales/*.json`

**Interfaces:**
- Consumes: Task 3.
- Produces:
  - api: `similar(transactionId: string, opts?: { excludeRule?: boolean })` (corpo `{transaction_id, exclude_rule}`); `applySimilar(transactionId: string, opts?: { excludeRule?: boolean; transactionIds?: string[] })`; `SimilarResult.transactions: SimilarTransaction[]` (`{id, date, description, amount, currency, type, category_id, category_source}`).
  - `<SimilarPrompt referenceId: string; storeName: string; currency: string; onDone: () => void />` — ao montar chama `similar(referenceId, { excludeRule: true })`; `count === 0` → `onDone()` e não renderiza nada; senão "Aplicar também às {{count}} parecidas desta loja?" com **Aplicar às N** (`applySimilar(referenceId, { excludeRule: true, transactionIds: ids })` → toast com desfazer via `categorization.undo({event_ids})`, invalida, `onDone`), **Ver quais** (alterna a lista das N: data, descrição, valor mascarado) e **Não** (`onDone`). Só com `canWrite`.
  - Revisar (decisão de UI deste plano): uma loja respondida (aceitar, "Outra...", criar, e depois o Aceitar do chat) com N > 0 **fica na lista** em estado respondido: `ReviewTab` guarda `answered: Map<merchantKey, { group: ReviewGroup; index: number; referenceId: string }>` (`referenceId = group.transaction_ids[0]`), e `visible` = grupos do servidor sem as chaves respondidas, com os instantâneos respondidos reinseridos no índice guardado (limitado ao tamanho); a linha mostra o `SimilarPrompt` no lugar dos botões e mantém o foco; `onDone` remove o instantâneo e leva o foco à próxima linha.
  - Aplicadas: a linha corrigida sai da lista (`fixed`), então o `SimilarPrompt` aparece numa barra no topo da lista com o nome da loja (`similarFor: { referenceId, storeName } | null`), depois de `correct` ou `choose`.
  - i18n: `categorization.similar.ask` (com `{{count}}`, plural), `categorization.similar.apply` ("Aplicar às {{count}}"), `categorization.similar.show` ("Ver quais"), `categorization.similar.hide`, `categorization.similar.no` ("Não"), `categorization.similar.applied` ("{{count}} parecidas atualizadas", plural), `categorization.similar.store` ("Em {{store}}").

- [ ] **Step 1: Testes** — `similar-prompt.test.tsx`: com 2 parecidas mostra o texto de 2; "Ver quais" lista as 2 descrições; "Aplicar às 2" chama `applySimilar(ref, { excludeRule: true, transactionIds: ['a', 'b'] })` e o toast tem ação de desfazer que chama `undo({ event_ids })`; `count 0` chama `onDone` e não renderiza; viewer não vê botões. `review-tab.test.tsx`: depois de aceitar uma loja com 2 parecidas, a linha continua visível com a pergunta mesmo após o refetch sem a loja; "Não" a remove. `applied-tab.test.tsx`: depois de corrigir uma linha, a barra com o nome da loja aparece.
- [ ] **Step 2: Rodar e ver falhar** — `npm test -- similar-prompt review-tab applied-tab`.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** → PASS; gate.
- [ ] **Step 5: Commit** — `feat(categorization): toda escolha manual pergunta pelas parecidas da loja, com desfazer`

### Task 6: Nota da loja visível e apagável (frontend)

**Files:**
- Create: `frontend/src/components/categorization/store-note.tsx` (+ `store-note.test.tsx`)
- Modify: `review-group-row.tsx`, `provenance-block.tsx` (+ test), `frontend/src/lib/api.ts`, `frontend/src/types/index.ts`, `frontend/src/locales/*.json`

**Interfaces:**
- Consumes: Task 1 (`PUT /notes`, `ReviewGroup.note`, `Provenance.store_note`).
- Produces: types `ReviewGroup.note: string | null`, `NoteRead {merchant_key: string; note: string | null}`, `Provenance.store_note: NoteRead | null`; api `setNote(payload: NoteRead): Promise<NoteRead>`; `<StoreNote merchantKey: string; note: string; className?: string />` — linha "Nota: {{note}}" com botão X (`aria-label` `categorization.note.delete`, só `canWrite`) → `setNote({ merchant_key, note: null })` → toast `categorization.note.deleted` + `invalidateCategorizationQueries`. Usado na linha da Revisar (abaixo do motivo, quando há nota) e no `ProvenanceBlock` ("Como chegou aqui"). i18n: `categorization.note.label` ("Nota: {{note}}"), `categorization.note.delete` ("Apagar nota"), `categorization.note.deleted` ("Nota apagada").

- [ ] **Step 1: Testes** — `StoreNote` mostra o texto e o X chama `setNote({ merchant_key: 'K', note: null })`; viewer não vê o X; a linha da Revisar com `note` mostra a nota; `ProvenanceBlock` com `store_note` mostra a nota.
- [ ] **Step 2: Rodar e ver falhar** — `npm test -- store-note provenance-block review-tab`.
- [ ] **Step 3: Implementar.** — [ ] **Step 4: Rodar** → PASS; gate.
- [ ] **Step 5: Commit** — `feat(categorization): nota da loja aparece na revisão e em "Como chegou aqui" e pode ser apagada`

## Fatia 2 — Perguntar à IA

### Task 7: Prompt da conversa e nota no lote (funções puras)

**Files:**
- Create: `backend/app/services/categorization/ask_prompts.py`
- Modify: `backend/app/services/categorization/prompts.py` (`category_context`, `build_user_message(..., notes=None)`, linha do campo `o` em `INSTRUCTIONS`), `backend/app/services/categorization/classifier.py` (`_plan` recebe as notas de `memory_rows`; `_category_index` → público `category_index`; `_account` → público `account_usage`)
- Test: `backend/tests/test_categorization_ask_prompts.py` (novo), `backend/tests/test_categorization_prompts.py`, `backend/tests/test_categorization_classifier.py`

**Interfaces:**
- Produces (`prompts.py`): `def category_context(index: CategoryIndex, examples: str) -> str` (o texto do 2º bloco de hoje; `build_system_blocks` passa a usá-la, bytes iguais); `build_user_message(groups, history=None, notes: dict[str, str] | None = None)` — item ganha `"o": sanitize_text(nota)[:NOTE_LENGTH]` quando a chave tem nota; `INSTRUCTIONS` ganha, na lista de entrada: `- o (só às vezes): nota que a pessoa escreveu sobre esta loja; é o sinal mais forte, acima de h.`
- Produces (`ask_prompts.py`):
  - `ASK_MODEL = CLASSIFIER_MODEL`; `ASK_MAX_TOKENS = 1024`; `REPLY_LENGTH = 600`; `MESSAGE_LENGTH = 2000`; `HISTORY_WINDOW = 20`; `SAMPLES = 3`.
  - `@dataclass(frozen=True) class StoreContext: name: str | None; is_person: bool; count: int; total: Decimal; last_date: date | None; samples: tuple[str, ...]; kind: str  # "d" | "c"; current_category_id: UUID | None; suggested_category_id: UUID | None; alt_category_id: UUID | None; history: tuple[tuple[UUID, int], ...]; note: str | None`.
  - `ASK_INSTRUCTIONS: str` (pt-BR) dizendo: você ajuda a escolher a categoria **desta loja** para esta pessoa; respostas curtas (até 3 frases), pode perguntar de volta; pedidos fora disso (outras lojas, regras, relatórios, dados da conta) são recusados com educação indicando onde fazer (Regras, Categorias); ao propor, preencha `suggestion` com `c` (id da lista) ou `new` `{g, name}` (prefira grupo existente; grupo novo só se nenhum serve), `p` calibrado como no classificador; `note`: resumo curto (≤ 200 caracteres) do contexto que a pessoa deu, no estilo "Aurino: jardineiro semanal", ou null se nada novo; nunca peça documentos, conta ou cartão; responda no idioma da pessoa.
  - `def render_store_context(ctx: StoreContext, index: CategoryIndex) -> str` — rótulos legíveis + JSON compacto: nome (ou "pessoa sem nome"), `q`, `v` (total), `u` (data ISO), `s` (≤ `SAMPLES`, `prompt_sample`), `t`, `atual`/`sug`/`alt` (ids curtos), `h` (`[id, vezes]`), `nota` (`sanitize_text`). Nunca a chave.
  - `def build_ask_system_blocks(index, examples: str, ctx: StoreContext) -> list[dict]` — três blocos de texto, cada um com `cache_control: {"type": "ephemeral"}`: `ASK_INSTRUCTIONS`, `category_context(index, examples)`, `render_store_context(ctx, index)`.
  - `def build_ask_schema(index) -> dict` — `{reply: string, suggestion: null | {c: enum(ids)|null, new: null | {g: string, name: string}, p: integer}, note: null | string}`, todos `required`, `additionalProperties: false`, `reply` primeiro.
  - `def conversation_messages(turns: Sequence[tuple[str, str]]) -> list[dict]` — `sanitize_text` + corte em `MESSAGE_LENGTH` em cada conteúdo, só as últimas `HISTORY_WINDOW` e sem turnos `assistant` no começo.
  - `def ask_params(system, messages, schema) -> dict` — `{"model": ASK_MODEL, "max_tokens": ASK_MAX_TOKENS, "thinking": {"type": "disabled"}, "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}}, "system": system, "messages": messages}`.
  - `@dataclass(frozen=True) class ParsedAnswer: reply: str; category_id: UUID | None; new_category: tuple[str, str] | None; confidence: int; note: str | None; raw: str` e `def parse_answer(message, index) -> ParsedAnswer | None` — `None` para `refusal`, `max_tokens` ou JSON inválido; `c` fora do índice vira `None`; `c` e `new` nulos = sem sugestão; `p` limitado a 0–100; `new` cortado em 100 por campo; `reply` em `REPLY_LENGTH`; `note` com `sanitize_text` e 200; `raw` = JSON compacto (`separators=(",", ":")`) do objeto já saneado, que o cliente devolve como turno `assistant`.

- [ ] **Step 1: Testes** — `test_store_context_never_carries_the_key` (contexto de uma loja de chave `PIX:#0a1b2c3d4e`: nenhum bloco contém `"PIX:#"` nem `"0a1b2c3d4e"`), `test_person_text_is_sanitized` (`"meu cpf 123.456.789-09, cartão 4111 1111 1111 1111, conta 12345-6"` → mensagem sem `"123.456.789-09"`, `"4111"`, `"12345-6"`), `test_history_window_starts_with_person` (25 turnos alternados começando por `user` → 20 ou 19 mensagens, a primeira `user`), `test_ask_schema_only_accepts_workspace_ids`, `test_ask_params_use_haiku_low_effort_no_thinking` (igualdade exata das chaves acima), `test_three_cache_breakpoints_and_tree_block_matches_classifier` (`blocks[1]["text"] == build_system_blocks(index, ex)[1]["text"]`), `test_parse_drops_unknown_category_and_clamps` (`c:"c99"`, `p:180` → `category_id None`, `confidence 100`), `test_parse_refusal_is_none`, `test_parse_note_cut_to_200`; `test_batch_item_carries_store_note` (`notes={"IFOOD": "Aurino: jardineiro"}` → item com `"o"`), `test_batch_note_is_sanitized`; classifier `test_classify_sends_memory_notes` (memória com nota → `params["messages"][0]["content"]` contém a nota).
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_ask_prompts.py tests/test_categorization_prompts.py tests/test_categorization_classifier.py -v`.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** → PASS (testes antigos de prompt atualizados só onde o texto de `INSTRUCTIONS` é comparado literalmente); gate.
- [ ] **Step 5: Commit** — `feat(categorization): prompt da conversa sobre uma loja e nota da loja no lote`

### Task 8: `POST /ask` — conversa, limites e custo

**Files:**
- Create: `backend/app/services/categorization/ask.py`
- Modify: `backend/app/core/rate_limit.py` (extrai `hit`), `backend/app/api/categorization.py`, `backend/app/schemas/categorization.py`, `backend/app/services/categorization/review.py` (`summary` ganha `ai_enabled`), `tests/api_contract/baseline.json`
- Test: `backend/tests/test_categorization_ask.py` (novo), `backend/tests/test_rate_limit.py`

**Interfaces:**
- Consumes: Task 7; `classifier.category_index`, `classifier.account_usage`, `classifier.committed_spend`, `classifier.estimate_cost`, `claude_client.resolve_client`, `claude_client.create_message`, `settings.get_settings`, `memory.lookup`, `prompts.build_examples`.
- Produces (`ask.py`):
  - `MAX_PERSON_MESSAGES = 30`; `TIMEOUT_SECONDS = 15`; `RATE_LIMIT = (20, 60)`.
  - `class AskError(Exception)` com `code: str`: `Unavailable(code)` para `"disabled"`, `"no_connection"`, `"capped"`; `ConversationLimit` (`"conversation_limit"`); `AskTimeout` (`"timeout"`); `ModelError` (`"model_error"`).
  - `async def store_context(session, workspace_id, merchant_key: str, transaction_ids: list[UUID] | None) -> tuple[StoreContext, list[Transaction]]` — linhas: com ids, essas do workspace; sem, as pendentes da loja sobre linhas abertas (mesmo critério de `review._open_row`); nenhuma → `review.NotFound`. Nome: `clean_name` da sugestão mais recente → `ai_clean_name` da memória → `review._readable_key` (exceto `UNKNOWN`/`PIX:#`, que ficam `None`); total = soma de `abs(amount_primary or amount)`; `samples` = até 3 `prompt_sample` distintos, os mais frequentes; `current_category_id` = categoria mais comum das linhas; sugestão/alt = da pendente mais recente; `history`/`note` da memória.
  - `@dataclass class AskSuggestion: category_id: UUID | None; new_category: dict[str, str] | None; group_exists: bool; confidence: int; applies_to: int` e `@dataclass class AskAnswer: reply: str; suggestion: AskSuggestion | None; note: str | None; assistant_turn: str; person_messages_left: int`.
  - `async def ask(session, workspace_id, *, merchant_key: str, transaction_ids: list[UUID] | None, turns: list[tuple[str, str]]) -> AskAnswer`, nesta ordem: último turno não é `user` → `ValueError`; turnos `user` > 30 → `ConversationLimit`; `settings.enabled` falso → `Unavailable("disabled")`; sem cliente → `Unavailable("no_connection")`; monta índice, exemplos, contexto e `params`; `committed_spend + estimate_cost(params, batch=False) > monthly_cap_usd` → `Unavailable("capped")` sem chamar a API; chama `create_message(client.with_options(timeout=TIMEOUT_SECONDS, max_retries=0), params)` dentro de `asyncio.timeout(TIMEOUT_SECONDS)`; grava um `CategorizationRun(kind="ask", trigger="manual", status="done", model=ASK_MODEL, finished_at=now)` com `account_usage(run, usage, batch=False)`; timeout → run `status="failed"`, `error="timeout"`, `cost_usd = estimate_cost(...)` e `AskTimeout`; `LLMRateLimitError`/outro `LLMError` → run `failed` com o erro e `ModelError` (`LLMAuthError` → `Unavailable("no_connection")`); `parse_answer` `None` → resposta fixa "Não consegui responder agora; tente dizer de outro jeito." sem sugestão. `new_category` que casa (normalizado) com categoria atribuível existente no grupo de mesmo nome vira `category_id`; `group_exists` = há grupo visível com esse nome. `applies_to = len(linhas)`; `person_messages_left = 30 - turnos user`; `assistant_turn = parsed.raw`. Nunca chama `remember_ai_decision`, nem grava sugestão/evento. Fecha o cliente no `finally`. Só `flush`, sem commit.
  - `app.core.rate_limit.hit(key: str, max_requests: int, window_seconds: int) -> None` (o corpo de hoje de `RateLimiter.__call__`, que passa a chamá-la com a chave de IP).
  - API: `async def ask_rate_limit(ctx: WorkspaceContext = Depends(current_writable_workspace)) -> None` → `rate_limit.hit(f"rate_limit:categorization-ask:{ctx.workspace.id}", *ask.RATE_LIMIT)`; rota `POST /api/categorization/ask` com `Depends(current_writable_workspace)` e `dependencies=[Depends(ask_rate_limit)]`; `AskRequest{merchant_key: str 1..255, transaction_ids: list[UUID]|None max_length=500, messages: list[AskMessage] 1..80}`, `AskMessage{role: Literal["user","assistant"], content: str 1..4000}` → `AskResponse{reply, suggestion: AskSuggestionRead|None, note, assistant_turn, person_messages_left}`. Erros: `ConversationLimit` e `capped` → 409; `disabled`/`no_connection` → 503; timeout → 504; `ModelError` → 502; `NotFound` → 404; `ValueError` → 422; sempre `detail={"code", "message"}` (mensagem em pt-BR) e **commit antes de levantar** (o run da falha precisa ficar no razão).
  - `CategorizationSummary.ai_enabled: bool` (= `settings.enabled`).

- [ ] **Step 1: Testes** (cliente falso: `monkeypatch.setattr(ask, "resolve_client", ...)` devolvendo objeto com `with_options()`/`close()`, e `monkeypatch.setattr(ask, "create_message", fake)` que guarda `params` e devolve `SimpleNamespace(content=[SimpleNamespace(type="text", text=json)], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=900, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=800))`) — `test_ask_returns_reply_and_suggestion_with_applies_to` (3 pendentes → `applies_to == 3`, `c1` → uuid certo), `test_ask_from_applied_rows_applies_to_those_rows`, `test_ask_never_writes_memory_or_suggestions` (memória `ai_*`, sugestões e eventos iguais antes/depois), `test_ask_records_cost_run` (1 run `kind="ask"`, `cost_usd > 0`, entra em `month_spend`), `test_ask_30th_person_message_is_allowed_and_31st_is_409`, `test_ask_capped_is_409_without_calling_model`, `test_ask_disabled_is_503`, `test_ask_without_connection_is_503`, `test_ask_timeout_is_504_and_books_estimate` (`TIMEOUT_SECONDS=0.01`, fake dorme 0.1 → 504 e run `failed` com custo = estimativa), `test_ask_sends_no_merchant_key_or_documents` (loja `PIX:#…` e texto com CPF → `params` serializado sem a chave e sem o CPF), `test_ask_new_category_matching_existing_becomes_category_id`, `test_ask_last_turn_must_be_person` (422), `test_ask_requires_write` (403), `test_summary_reports_ai_enabled`. Em `tests/test_rate_limit.py` (que tem o Redis contador): `test_ask_rate_limit_per_workspace` (serviço `ask.ask` trocado por um que devolve `AskAnswer` fixo; 20 → 200, 21ª → 429; a chave contada contém o id do workspace).
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_ask.py tests/test_rate_limit.py -v`.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** → PASS; baseline; gate.
- [ ] **Step 5: Commit** — `feat(categorization): conversa com a IA sobre uma loja, com teto, limite por conversa e por minuto`

### Task 9: Diálogo "Perguntar à IA" (frontend)

**Files:**
- Create: `frontend/src/lib/categorization-ask.ts` (+ test), `frontend/src/components/categorization/ask-provider.tsx`, `ask-dialog.tsx` (+ `ask-dialog.test.tsx`), `ask-suggestion-card.tsx`
- Modify: `frontend/src/pages/categorization.tsx` (envolve as abas em `AskProvider`), `review-group-row.tsx`, `review-tab.tsx`, `applied-tab.tsx` (+ testes), `frontend/src/lib/api.ts`, `frontend/src/types/index.ts`, `frontend/src/locales/*.json`

**Interfaces:**
- Consumes: Task 8 (`POST /ask`, `summary.ai_enabled`), Task 2 (`choose`), Task 4 (`footerAction`, `onCreateInGroup`), Task 5 (estado respondido / barra das parecidas).
- Produces:
  - types: `AskMessage {role: 'user' | 'assistant'; content: string}`, `AskRequest {merchant_key: string; transaction_ids?: string[]; messages: AskMessage[]}`, `AskSuggestion {category_id: string | null; new_category: NewCategoryDraft | null; group_exists: boolean; confidence: number; applies_to: number}`, `AskResponse {reply: string; suggestion: AskSuggestion | null; note: string | null; assistant_turn: string; person_messages_left: number}`; `CategorizationSummary.ai_enabled: boolean`; api `ask(payload: AskRequest): Promise<AskResponse>`.
  - `lib/categorization-ask.ts`: `ASK_MAX_PERSON_MESSAGES = 30`; `export type AskBlocked = 'readOnly' | 'disabled' | 'noConnection' | 'capped'`; `export function askBlocked(summary: CategorizationSummary | undefined, canWrite: boolean): AskBlocked | null` (ordem: `readOnly`, `disabled` (`!ai_enabled`), `noConnection` (`!ai_available`), `capped` (`Number(month_cost_usd) >= Number(monthly_cap_usd)`); sem `summary` → `null`); `export interface ChatTurn { role: 'user' | 'assistant'; text: string; wire: string; answer?: AskResponse }`; `export function toWire(turns: ChatTurn[]): AskMessage[]` (assistente → `wire` = `assistant_turn`); `export function personCount(turns: ChatTurn[]): number`; `export function askErrorKey(err: unknown): string` (código `conversation_limit|capped|disabled|no_connection|timeout|model_error`, 429 → `rateLimited`; resto → `categorization.ask.errors.generic`).
  - `AskProvider` + `useAsk(): { open: (target: AskTarget) => void; blocked: AskBlocked | null }`; `AskTarget {merchantKey: string; storeName: string; count: number; total: number; currency: string; lastDate: string | null; samples: string[]; transactionIds?: string[]; suggestedGroupId: string | null; onAccepted: (result: ChooseResult) => void}`. Conversas num `Map<merchantKey, ChatTurn[]>` no estado do provider (reabrir retoma; recarregar zera); a conversa de uma loja é apagada depois de Aceitar.
  - `AskDialog` (`DialogContent` com `sm:max-w-2xl` e altura `h-[min(80vh,44rem)]`): cabeçalho com nome, "{{count}} transações · total mascarado · última {{date}}" e até 3 exemplos; turnos; indicador de digitação; `textarea` (Enter envia, Shift+Enter quebra linha); contador "{{used}} de 30"; ao chegar a 30 o campo fica desabilitado com `categorization.ask.limitReached`; erros pelo `askErrorKey`. Cada resposta com `suggestion` mostra `AskSuggestionCard`: caminho da categoria (existente ou "nova", grupo "novo" quando `!group_exists`), confiança, "aplica nas {{count}} transações desta loja", **Aceitar** e **Outra categoria**. Aceitar → `choose({ merchant_key, transaction_ids: target.transactionIds, category_id | new_category, note: answer.note ?? undefined })` → fecha, `onAccepted(result)`. Outra categoria → `CategorySelect` dentro do cartão (com `onCreateInGroup` e `suggestedGroupId`); escolher aplica do mesmo jeito (com a nota).
  - Pontos de entrada (desabilitados com `title` = motivo de `askBlocked` quando bloqueados): botão com ícone `Sparkles` na linha da Revisar e na linha da Aplicadas, e `footerAction` "Perguntar à IA" no "Outra..." das duas. Revisar: `transactionIds` ausente, `onAccepted` → toast com desfazer + estado respondido (Task 5) + foco na linha. Aplicadas: `transactionIds: [event.transaction.id]`, `onAccepted` → `fixed` + barra das parecidas.
  - i18n `categorization.ask.*`: `button` ("Perguntar à IA"), `title`, `context` (`{{count}}`, `{{total}}`, `{{date}}`), `placeholder`, `send`, `counter` (`{{used}}`, `{{max}}`), `limitReached`, `thinking`, `appliesTo` (plural), `newCategory`, `newGroup`, `confidence` (`{{value}}`), `accept`, `other`, `accepted`, `blocked.readOnly|disabled|noConnection|capped`, `errors.conversation_limit|capped|disabled|no_connection|timeout|model_error|rateLimited|generic`.

- [ ] **Step 1: Testes** — lib: `askBlocked` para cada motivo e `null` com tudo ok; `toWire` usa `assistant_turn` nos turnos do assistente; `askErrorKey` de um erro axios 409 `{code:'conversation_limit'}`. Diálogo: enviar "é o jardineiro" chama `ask` com `messages` `[{role:'user', content:'é o jardineiro'}]` e, na segunda, o turno do assistente com `assistant_turn`; o cartão mostra "aplica nas 3 transações"; Aceitar com sugestão nova chama `choose({ merchant_key:'K', new_category:{group:'Casa', name:'Jardinagem'}, note:'Aurino: jardineiro semanal' })` e fecha; reabrir a mesma loja mostra a conversa; com 30 mensagens da pessoa o campo fica desabilitado; erro 504 mostra a mensagem de timeout. Linhas: com `ai_enabled:false` o botão da Revisar fica desabilitado com o motivo; viewer vê desabilitado com `blocked.readOnly`.
- [ ] **Step 2: Rodar e ver falhar** — `npm test -- categorization-ask ask-dialog review-tab applied-tab`.
- [ ] **Step 3: Implementar.** — [ ] **Step 4: Rodar** → PASS; gate.
- [ ] **Step 5: Commit** — `feat(categorization): perguntar à IA sobre uma loja em diálogo, aceitar cria e aplica`

## Fatia 3 — Aplicadas completa e placar

### Task 10: Filtros Confirmadas, Suas e Outras origens + placar (backend)

**Files:**
- Modify: `backend/app/services/categorization/review.py` (`applied_events`, novas `origin_rows`, `scoreboard`), `backend/app/api/categorization.py` (`_sources`, rota), `backend/app/schemas/categorization.py`, `tests/api_contract/baseline.json`
- Test: `backend/tests/test_categorization_applied_views.py` (novo)

**Interfaces:**
- Produces:
  - `review.applied_events(..., confirmed: bool = False)` — `True` troca `changes_category()` pelo seu oposto (`previous_category_id IS NOT DISTINCT FROM new_category_id`) e restringe a `source in ("ai", "memory")`.
  - `USER_ORIGINS = ("user", "legacy")`; `OTHER_ORIGINS = ("provider", "import", "recurring", "installment")`; `async def review.origin_rows(session, workspace_id, *, origins: tuple[str, ...], limit: int, offset: int) -> tuple[list[dict], int]` — transações categorizáveis (postadas, sem par de transferência, `source != "opening_balance"`, `is_ignored` falso) com categoria e `category_source in origins`, por `Transaction.created_at desc, id desc`; cada item no formato de `AppliedEvent` com `event_id`/`run_id`/`previous_category_id`/`confidence`/`reason` do último evento não desfeito da linha (ou `None`), `source` = origem da linha, `new_category_id` = categoria atual, `created_at` = `Transaction.created_at`, `merchant_key`/`store_name` de `_store_labels`.
  - `GET /applied?source=`: `ai,memory,rule` combináveis (como hoje); `confirmed`, `user` (→ `USER_ORIGINS`) e `other` (→ `OTHER_ORIGINS`) só sozinhos; mistura → 422. `AppliedEvent.event_id: Optional[UUID]`.
  - `async def review.scoreboard(session, workspace_id) -> dict` → `{"total", "decided", "to_review", "waiting"}` sobre a mesma base categorizável: `to_review` = linhas com sugestão pendente sobre linha aberta; `decided` = com categoria e fora de `to_review`; `waiting` = o resto (sem categoria e sem pendente). Rota `GET /api/categorization/scoreboard` (leitura) → `Scoreboard{total, decided, to_review, waiting}`.

- [ ] **Step 1: Testes** — `test_applied_confirmed_lists_only_confirmations` (evento ai mesma categoria aparece; mudança não; desfeito não), `test_applied_user_lists_rows_by_origin` (linha `user` com evento e linha `legacy` sem evento → 2 itens, o `legacy` com `event_id None`), `test_applied_other_lists_bank_import_recurring_installment`, `test_applied_rejects_mixing_views` (`source=ai,user` → 422), `test_scoreboard_sums_to_total` (pendente, categorizada, sem categoria sem pendente; excluídas: par de transferência, `status="pending"`, saldo inicial, ignorada → `total == 3` e `decided + to_review + waiting == total`), `test_scoreboard_categorized_row_with_pending_suggestion_is_to_review`.
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_applied_views.py -v`.
- [ ] **Step 3: Implementar.** — [ ] **Step 4: Rodar** → PASS; `tests/test_categorization_api.py` verde; baseline; gate.
- [ ] **Step 5: Commit** — `feat(categorization): aplicadas mostram confirmações, escolhas da pessoa e outras origens, e placar da seção`

### Task 11: Filtros novos e placar (frontend)

**Files:**
- Create: `frontend/src/lib/categorization-scoreboard.ts` (+ test), `frontend/src/components/categorization/scoreboard.tsx`
- Modify: `applied-tab.tsx` (+ test), `frontend/src/pages/categorization.tsx`, `frontend/src/lib/api.ts`, `frontend/src/types/index.ts`, `frontend/src/locales/*.json`

**Interfaces:**
- Consumes: Task 10.
- Produces:
  - types `Scoreboard {total: number; decided: number; to_review: number; waiting: number}`; `AppliedEvent.event_id: string | null`; `AppliedFilters.source` aceita `'confirmed' | 'user' | 'other'`; api `scoreboard(): Promise<Scoreboard>` (query key `['categorization', 'scoreboard']`, invalidada por `invalidateCategorizationQueries`).
  - `export function scoreboardParts(s: Scoreboard): { decided: number; toReview: number; waiting: number }` — porcentagens inteiras que somam 100 (maior resto; empate na ordem decided, toReview, waiting); `total 0` → zeros.
  - `<Scoreboard />` ao lado da linha de status da página (visível em todas as abas): "{{total}} transações · {{decided}}% decididas · {{review}}% para revisar" e, se `waiting > 0`, " · {{waiting}}% aguardando a IA"; `Tooltip` com `categorization.scoreboard.excluded` (transferências entre contas, pendentes e saldo inicial ficam fora).
  - Aplicadas: chips IA, Memória, Regra (combináveis) + Confirmadas, Suas, Outras origens (exclusivos: escolher um limpa os outros; escolher IA/Memória/Regra limpa os exclusivos). Linha com `event_id null`: chave `tx-${transaction.id}`, sem caixa de seleção nem desfazer; "Outra...", criação e Perguntar à IA continuam.
  - i18n: `categorization.applied.filters.confirmed` ("Confirmadas"), `.user` ("Suas"), `.other` ("Outras origens"), `categorization.scoreboard.summary`, `.waiting`, `.excluded`.

- [ ] **Step 1: Testes** — lib: `{total:3, decided:1, to_review:1, waiting:1}` → `{decided:34, toReview:33, waiting:33}`; `{total:0,...}` → zeros; `{total:4, decided:3, to_review:1, waiting:0}` → `75/25/0`. Aplicadas: clicar "Suas" pede `source: 'user'` e desmarca "IA"; item com `event_id: null` renderiza sem checkbox. Placar: com `{total:120, decided:90, to_review:30, waiting:0}` mostra "120 transações · 75% decididas · 25% para revisar" e não mostra "aguardando".
- [ ] **Step 2: Rodar e ver falhar** — `npm test -- categorization-scoreboard applied-tab`.
- [ ] **Step 3: Implementar.** — [ ] **Step 4: Rodar** → PASS; gate.
- [ ] **Step 5: Commit** — `feat(categorization): filtros de confirmadas, suas e outras origens e placar no topo da seção`

## Fatia 4 — Dados reais e sobras do worker

### Task 12: Prefixos de adquirente na chave de loja (v2)

**Files:**
- Modify: `backend/app/services/categorization/merchant_key.py`
- Test: `backend/tests/test_merchant_key.py`

**Interfaces:**
- Produces: `MERCHANT_KEY_VERSION = 2`; `merchant_key(description, *, payee_name=None, payee_source=None, is_person_transfer=False, version: int = MERCHANT_KEY_VERSION) -> str` — `version=1` reproduz exatamente o comportamento de hoje (a Task 13 precisa dele para achar as chaves antigas). Regra v2 (só na descrição; payee e Pix inalterados): depois de `_strip_leading`, se o texto começa com um token único de 2 a 5 letras seguido de `*` (`^[A-Z]{2,5}\*`), o prefixo sai e o resto é normalizado como hoje; exceções: prefixo em `BRAND_STAR_PREFIXES = frozenset({"UBER", "IFOOD", "CASAR"})` continua a marca (cabeça, como hoje); resto genérico — vazio depois de normalizar, começando por `PENDING`/`PENDENTE`, ou só país/UF/números — vira `PREFIX_BRANDS.get(prefixo, prefixo)` com `PREFIX_BRANDS = {"UBR": "UBER", "IFD": "IFOOD"}`; a mesma checagem de resto genérico vale para os prefixos já conhecidos de `STAR_PREFIXES` (por isso `IFD*BR` → `IFOOD`). Só um prefixo curto é retirado por descrição; cabeça com espaço ("ZEE NOW *...") ou com dígito ("99*") segue a regra de hoje.
- Produces (teste): `REAL_PREFIX_CASES: list[tuple[str, str]]` no módulo de teste (a Task 13 importa).

- [ ] **Step 1: Testes** — `test_real_acquirer_prefixes` parametrizado em `REAL_PREFIX_CASES` (descrição → chave v2, sem payee; as continuações sem exemplo no spec são ilustrativas): `Mlp*Netshoes`→`NETSHOES`, `Mlp*Kabum`→`KABUM`, `Dl*Aliexpress`→`ALIEXPRESS`, `Ebn *Amazon`→`AMAZON`, `Ebn *Spotify`→`SPOTIFY`, `Ebn*Spotify`→`SPOTIFY`, `Ubr* Pending.Uber.Com`→`UBER`, `UBER * PENDING`→`UBER`, `Uber *Trip`→`UBER`, `Pb*Ubiquiti 1/3`→`UBIQUITI`, `Zig*Op Henio Polinesia`→`OP HENIO POLINESIA`, `Tst*Coco Bambu`→`COCO BAMBU`, `Vindi*Smartfit`→`SMARTFIT`, `Sq *Padaria Real`→`PADARIA REAL`, `Tm *Ticketmaster`→`TICKETMASTER`, `Evo*Bodytech`→`BODYTECH`, `NYX*Cosmeticos`→`COSMETICOS`, `Zp *Restaurante Bom`→`RESTAURANTE BOM`, `Dm*Loja Teste`→`LOJA TESTE`, `Vmt*Estacionamento`→`ESTACIONAMENTO`, `Wow*Acai`→`ACAI`, `Bkg*Hotel At Booking`→`HOTEL AT BOOKING`, `CTLP*FIRST CLASS VENDI`→`FIRST CLASS VENDI`, `CTLP*FIRST CLASS VENDI (IOF)`→`FIRST CLASS VENDI IOF`, `Casar *Pre*Sente`→`CASAR`, `Ec *Padaria`→`PADARIA`, `Pg *Primepassapor`→`PRIMEPASSAPOR`, `Mp *Aliexpress 3/3`→`ALIEXPRESS`, `Ifd*Baker Bar e Restau`→`BAKER BAR E RESTAU`, `IFD*BR`→`IFOOD`, `Pag*Steam 3/3`→`STEAM`, `Sumup *Amici e Cucina`→`AMICI E CUCINA`, `Mlp*`→`MLP`. `test_star_heads_that_stay` (sem mudança): `IFOOD *REST 8812`→`IFOOD`, `Petlove Saud*Petlove S`→`PETLOVE SAUD`, `99food *99food`→`99FOOD`, `Anthropic* Claude Sub`→`ANTHROPIC`, `Mercadolivre*Mercadol 2/2`→`MERCADOLIVRE`, `Zee Now *Zee Dog 2/3`→`ZEE NOW`, `New Balance *0104`→`NEW BALANCE`, `99*`→`99`, `Flexpag*Celpe`→`CELPE`, `IOF de volta de Ubr* Pending.Uber.Com`→`IOF DE VOLTA DE UBR`. `test_version_1_keeps_previous_keys`: v1 de `Mlp*Netshoes`→`MLP`, `Ebn *Spotify`→`EBN`, `IFD*BR`→`BR`, `Bkg*Hotel At Booking`→`BKG`. Um teste antigo que contradiga §5 do spec é atualizado citando o spec no nome.
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_merchant_key.py -v`.
- [ ] **Step 3: Implementar.** — [ ] **Step 4: Rodar** a suíte inteira do backend (as chaves entram em pipeline, memória e revisão) → PASS; gate.
- [ ] **Step 5: Commit** — `fix(categorization): prefixo curto de adquirente antes do asterisco não vira a loja`

### Task 13: Regravação das chaves (script idempotente)

**Decisão:** script, não migração Alembic. A regravação precisa da função Python de chave nas duas versões (v1 para saber a quem a linha da memória pertencia, v2 para o destino), do ORM async e do lock do workspace; numa migração Alembic ela congelaria na história uma lógica que muda, rodaria à toa em todo banco novo e não teria `--dry-run` para conferir as fusões antes. O script roda uma vez no deploy, depois do `alembic upgrade head`, e rodar de novo não muda nada.

**Files:**
- Create: `backend/app/services/categorization/rekey.py`, `backend/scripts/rekey_merchant_keys.py`
- Modify: `backend/scripts/README.md` (seção "rekey_merchant_keys.py": quando e como rodar: `docker exec <backend> python scripts/rekey_merchant_keys.py --dry-run`, depois sem `--dry-run`)
- Test: `backend/tests/test_categorization_rekey.py` (novo)

**Interfaces:**
- Consumes: `merchant_key(..., version=1|2)` (Task 12), `MerchantMemory.note`/`note_updated_at` (Task 1), `categorization_tasks.lock_workspace`, `tasks.BUSY_STATUSES`.
- Produces:
  - `@dataclass class RekeyReport: workspace_id: UUID; suggestions: int = 0; merged: int = 0; split: int = 0; seeded: int = 0; skipped: str | None = None`.
  - `async def rekey_workspace(session, workspace_id, *, dry_run: bool = False) -> RekeyReport` — pega `lock_workspace`; workspace com run `classify`/`redistribute` em `BUSY_STATUSES` → `skipped="busy"` sem mudar nada. Para cada transação do workspace (payee carregado como em `memory.keys_for`) calcula `old = stored_key(v1)` e `new = stored_key(v2)`; `mapping[old] = {new...}`. Sugestões (todos os status: as `dismissed` seguram o "ignorar") com chave ≠ `stored_key(v2)` da sua transação → regravadas. Memória, por linha de chave `K` com `mapping[K] - {K}` não vazio: um destino só (`{N}`) → **fusão** em `N` (cria se faltar): `counts` somados, `last_user_category_id` da linha de `updated_at` mais recente, `note`/`note_updated_at` da nota mais recente, `ai_category_id`/`ai_confidence` zerados, `ai_clean_name` do destino ou da origem; `K` apagada. Dois ou mais destinos (inclui `{K, N}`) → **divisão**: `K` apagada e cada destino re-semeado com as transações `category_source == "user"` com categoria daquela chave nova (`counts` = uma escolha por transação, por categoria; `last_user_category_id` = a da mais recente por `date`), somando em linha que já exista; `ai_*` zerados. Chaves sem transação ficam como estão. `memory_pick` dos eventos não muda: um desfazer posterior devolve/retira a escolha na chave nova (que é a que `keys_for` calcula). `dry_run` → calcula o relatório e faz `rollback`.
  - `async def rekey_all(session_maker, *, dry_run: bool = False, workspace_id: UUID | None = None) -> list[RekeyReport]` — uma transação (e um commit) por workspace.
  - Script: `argparse` com `--dry-run` e `--workspace UUID`; imprime uma linha por workspace (`<id> sugestões=… fundidas=… divididas=… semeadas=… [pulado: busy]`); sai 0.

- [ ] **Step 1: Testes** — `test_rekey_pending_suggestions` (sugestão `MLP` de `Mlp*Netshoes` → `NETSHOES`, de `Mlp*Kabum` → `KABUM`), `test_rekey_merges_converging_rows` (só `Ebn *Spotify`: memória `EBN` `{A:2}` nota antiga e `SPOTIFY` `{A:1,B:1}` nota nova → uma linha `SPOTIFY` `{A:3,B:1}`, nota nova, `ai_category_id None`; `EBN` some), `test_rekey_splits_mixed_prefix_and_reseeds_from_user_rows` (memória `MLP` `{A:5}`; `Mlp*Netshoes` `user` A ×2, `Mlp*Kabum` `user` B ×1, `Mlp*Kabum` `ai` C → sem `MLP`; `NETSHOES {A:2}`, `KABUM {B:1}`), `test_rekey_is_idempotent` (segunda rodada: relatório zerado e linhas idênticas), `test_rekey_dry_run_changes_nothing`, `test_rekey_skips_busy_workspace`, `test_rekey_real_prefixes` (para cada descrição de `REAL_PREFIX_CASES` com sugestão de chave v1 → chave final == valor esperado), `test_undo_after_rekey_takes_the_pick_from_the_new_key` (aceitar em v1, regravar, desfazer → contagem da chave nova diminui 1), `test_rekey_all_postgres` (via `postgres_sessions`, fusão em linha existente respeita `uq_merchant_memory_workspace_key`). O script é coberto por `test_rekey_script_parses_args` (`main(["--dry-run"])` com `rekey_all` trocado por fake).
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_rekey.py -v`.
- [ ] **Step 3: Implementar.** — [ ] **Step 4: Rodar** → PASS; gate.
- [ ] **Step 5: Commit** — `feat(categorization): script idempotente regrava chaves de loja e funde a memória das lojas que mudaram`

### Task 14: Sobras do worker (lote B2)

**Files:**
- Modify: `backend/app/services/categorization/classifier.py` (`committed_spend`, `_submit_batch`), `backend/app/tasks/categorization_tasks.py` (`_categorize_in`), `backend/app/services/categorization/review.py` (`request_run`: tira o `from sqlalchemy import or_` local; `or_` já vem do topo)
- Test: `backend/tests/test_categorization_classifier.py`, `backend/tests/test_categorization_tasks.py`

**Interfaces:**
- Produces:
  - `committed_spend(session, workspace_id) -> Decimal` com **um** `SELECT` em `categorization_runs` (`cost_usd`, `status`, `kind`, `detail`, `created_at`) onde `created_at` no mês local **ou** (`kind in BATCH_KINDS` e `status == "processing_batch"`); soma em Python `cost_usd` das linhas do mês + `batch_estimate(detail)` das em voo (mesmo resultado de hoje, num único instantâneo). O fuso continua vindo de `get_workspace_timezone`.
  - `_submit_batch`: logo depois de `create_batch` devolver o id, grava o rastreio (`batch_id`, `is_batch`, `model`, `status="processing_batch"`, `detail.batch` com a estimativa) e faz **commit** antes de qualquer outro trabalho (as sugestões do que ficou de fora vêm depois). Se gravar o rastreio falhar, `rollback`, grava o mesmo rastreio com um `UPDATE` direto do run numa transação nova, commit, e relança.
  - `_categorize_in`, no caminho de erro: run relido em `processing_batch` com `batch_id` fica nesse status (só `error` recebe o nome da exceção), para o beat recolher o lote pago.

- [ ] **Step 1: Testes** — `test_committed_spend_is_one_statement` (listener `before_cursor_execute` no engine conta exatamente 1 statement com `categorization_runs`), `test_committed_spend_counts_month_cost_and_inflight_estimates` (done deste mês 0.10 + `processing_batch` do mês passado com estimativa 0.05 + done do mês passado 1.00 → `0.15`), `test_batch_stays_collectable_when_work_after_send_fails` (backlog > `SYNC_LIMIT`, teto que cabe só um pedaço, `suggest_without_ai` trocado por um que levanta → numa sessão nova: `status == "processing_batch"`, `batch_id` gravado, estimativa em `detail`; `poll` recolhe depois), `test_batch_tracked_when_writing_tracking_fails_once` (`_merge_detail` levanta na primeira chamada → run em `processing_batch` com `batch_id`), `test_concurrent_batch_submissions_respect_the_cap_postgres` (`postgres_sessions`; um run `classify` e um `redistribute` do mesmo workspace chamando `_submit_batch` com `asyncio.gather`, teto que cabe um lote → `create_batch` chamado uma vez e o outro run termina `capped`).
- [ ] **Step 2: Rodar e ver falhar** — `uv run --no-sync pytest tests/test_categorization_classifier.py tests/test_categorization_tasks.py -v`.
- [ ] **Step 3: Implementar.** — [ ] **Step 4: Rodar** a suíte inteira do backend → PASS; gate.
- [ ] **Step 5: Commit** — `fix(categorization): gasto comprometido numa consulta só e lote pago nunca fica sem rastreio`
