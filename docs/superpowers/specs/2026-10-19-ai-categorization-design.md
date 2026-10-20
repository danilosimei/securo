# Categorização inteligente com Claude — design

Status: aprovado · 2026-10-19 · fork `danilosimei/securo` (roda no Tarsonis)

Marcações: **[decisão minha]** = escolha do autor do design, sujeita a revisão.

## 1. Por quê

Hoje 2.356 de 3.683 transações (64%) estão sem categoria e a árvore de categorias é rasa (16
categorias em 6 grupos, metade em "Outros"). As 1.327 categorizadas vieram todas do sync (mapa
estático do Pluggy ou uma das 6 regras ativas) — nenhuma tem origem registrada. Categorizar é trabalho
manual: o motor de regras é bom, mas corrigir uma transação não ensina nada e não existe sugestão
automática. Sem categoria confiável, nenhuma visão de gastos (projeto seguinte) presta.

Objetivo: o usuário quase não precisa categorizar. O Securo aplica sozinho o que tem certeza, pergunta
só o que não tem, aprende com cada correção e organiza a árvore num nível de detalhe útil — sempre
mostrando o que fez e como era o original, com desfazer.

## 2. Objetivos, não-objetivos, anti-objetivos

**Objetivos**
- Categorizar automaticamente transações novas (sync, import) e o backlog.
- Aprender com o usuário: memória por loja/pessoa, alimentada pelo histórico e por cada correção.
- Sugerir e organizar a árvore (Grupo → Categoria), com aprovação item a item.
- Rastreabilidade: origem, confiança, motivo e estado original de cada aplicação; desfazer individual
  e em lote.
- UX de primeira: poucos cliques, tudo num lugar óbvio, consistente com o resto do Securo (§10).
- Custo de API mínimo (§8).

**Não-objetivos**
- Mais de dois níveis de categoria; split de uma transação em várias categorias.
- Visões/gráficos de gastos (projeto separado, depois deste).
- Substituir o motor de regras.

**Anti-objetivos**
- Sobrescrever categoria posta à mão pelo usuário.
- Aplicar algo que o usuário não vê nem desfaz.
- Criar categoria sem aprovação, ou dezenas de categorias com 1 transação cada.
- Conta de API perceptível (alvo: centavos de dólar por mês).
- Mandar dado sensível desnecessário para a API (número de conta, CPF/CNPJ, notas, saldo).
- Quebrar a arquitetura: regras, sync Pluggy, import, orçamentos, recorrentes e testes de contrato
  continuam funcionando.

## 3. Restrições e encaixe na arquitetura

Confirmado contra o código (`backend/`, `frontend/`):

- **Camadas**: `app/api/*` → `app/services/*` → `app/models/*`, schemas em `app/schemas/*`. Leitura com
  `Depends(current_workspace)`, escrita com `Depends(current_writable_workspace)`
  (`app/core/workspace_context.py`). Toda rota de escrita nova passa pelo meta-teste
  `tests/test_write_permission_coverage.py`.
- **Escopo**: tudo por `workspace_id` (Category, CategoryGroup, Payee, Rule, Transaction já são). As
  tabelas novas também — com FKs com `ondelete` explícito (transaction → CASCADE; category/payee →
  SET NULL), senão `delete_category`, exclusão de import e bulk delete passam a falhar em Postgres (os
  testes SQLite não pegam; usar a fixture `postgres_sessions`).
- **Instância atual**: 1 usuário, 1 workspace. O design continua multi-membro-correto, mas sem UI de
  escolha de "de quem é a chave": é a do dono do workspace, guardada nas settings (§11).
- **Migração**: `alembic/versions/097_ai_categorization.py` (guard `scripts/check_migration_chain.py`);
  models registrados em `app/models/__init__.py`.
- **Celery**: módulo novo `app/tasks/categorization_tasks.py`, incluído em `worker.py` `conf.include`;
  padrão `asyncio.run` + engine própria com `NullPool` + `use_timezone(...)` + `engine.dispose()`.
  Enfileiramento por `celery_app.send_task(...)` em `try/except`.
- **LLM**: o provider httpx atual (`app/agents/providers/anthropic.py`) só faz streaming e não suporta
  cache, `output_config`, effort nem Batches. A feature usa o **SDK oficial `anthropic`** (dependência
  nova; `scripts/lock.sh` + `uv.lock` commitado) num módulo próprio `app/services/ai_categorization/`.
  A chave vem de uma `LlmConnection` com `kind='anthropic'` (decriptada por
  `build_provider_for_connection`; o worker já tem o mesmo `SECRET_KEY`), cadastrada pelo módulo
  de IA (§14, item 2).
- **Contrato de API**: cada rota nova entra em `tests/api_contract/baseline.json`
  (`UPDATE_API_CONTRACT=1 uv run pytest tests/api_contract -n 0`); nenhuma rota chama o Claude no
  request (o teste bloqueia rede de saída).
- **Frontend**: shadcn/ui (new-york) + Tailwind 4 + lucide + sonner + react-query + i18n. Toda chave
  i18n nova existe nos **19** locales (`locales/i18n.test.ts`); páginas/componentes novos passam nos
  testes de contrato de importação.

## 4. Pipeline de decisão

Candidatas: transações com `category_source` em {`none`, `provider`, `memory`, `ai`} e sem sugestão
pendente rejeitada. Transações `user` (e `legacy`, onde usado) são protegidas.

1. **Regras** — inalteradas, rodam na ingestão como hoje.
2. **Memória** por chave de loja (§5). Sem custo.
3. **Claude** (§7) para o restante, agrupado por chave de loja.
4. **Usuário**, na aba Revisar.

Categoria do Pluggy e herança de parcela, hoje misturadas na mesma variável
(`connection_service.py` ~1196-1250 e ~2270-2395), passam a ser gravadas com origens distintas
(`provider` e `installment`). `provider` é palpite revisável; `installment` herda a origem da parcela
anterior (se ela era `user`, é protegida).

## 5. Chave de loja e memória

**Chave de loja** (`merchant_key`) — **[decisão minha]** sempre a descrição normalizada; o `payee_id`
só é usado como chave quando o payee foi criado à mão (`Payee.source == "manual"`), porque o sync cria
um payee por descritor bruto e anularia o agrupamento.
- Normalização: maiúsculas, sem acento, remove prefixos de adquirente/carteira (`PG *`, `MP *`,
  `PAYPAL *`, `EBANX *`, `IFD*`…), números/IDs, `PARC 03/10`, sufixos de cidade/UF.
  "IFOOD *REST 8812" e "IFOOD*CLUB 4410" → `IFOOD`. Função pura, testável.
- Pix/TED para pessoa física: `PIX:<NOME NORMALIZADO>`. Se o único identificador for CPF/CNPJ, a
  chave usa um hash dele e o documento **não** vai para a API.

**Memória** (`merchant_memory`, por workspace): `merchant_key`, contagem por `category_id`, última
escolha do usuário, `ai_category_id` + `ai_confidence` (cache da decisão da IA), `updated_at`.
- Semeada com o histórico de origem `user` (e `legacy`, onde usado). Palpites do Pluggy
  **não** semeiam a memória.
- Atualizada só por ações do usuário (edição, bulk, aceite em Revisar) — nunca pela própria IA.
- Aplica quando a loja é consistente: ≥ 2 escolhas e a dominante com ≥ 80% **[decisão minha]**.
  Loja inconsistente vai para o Claude com a distribuição como contexto.
- Mudança de escolha do usuário passa a valer para as próximas; as antigas só mudam se ele aceitar
  "aplicar às N anteriores".
- O cache de decisão da IA é invalidado quando o usuário corrige aquela loja ou a árvore muda.

**Nome limpo** — **[decisão minha]** o Claude devolve um nome legível ("iFood"). A transação passa a
apontar para um payee com esse nome (reaproveita se existir, cria com `source="ai"` se não). Nunca
renomeia nem funde payees existentes. Não mexe em transação cujo payee veio de regra ou foi escolhido
à mão. A descrição original do banco nunca é alterada.

## 6. Proveniência e proteção

**Coluna nova** `transactions.category_source` (enum string: `none`, `user`, `rule`, `memory`, `ai`,
`provider`, `installment`, `import`, `recurring`, `legacy`), indexada — o selo e o filtro por origem
leem só ela.

**Tabela** `categorization_events`: `id`, `workspace_id`, `transaction_id` (CASCADE), `run_id`,
`source`, `previous_category_id`/`new_category_id` (SET NULL), `previous_payee_id`/`new_payee_id`
(SET NULL), `previous_source`, `confidence`, `reason` (≤ 120 car.), `model`, `created_at`,
`undone_at`.

**Serviço** `categorization_event_service.record(...)` no padrão de
`reconciliation_history_service.record` (sem commit, nunca lança). Chamado em todos os caminhos que
gravam `category_id`:

| Caminho | Local | Origem gravada |
|---|---|---|
| Edição manual (inclui propagação a parcelas irmãs) | `transaction_service._apply_update_to_row` / `update_transaction` | `user` |
| Bulk categorize (UI e MCP) | `transaction_service.bulk_update_category` — passa a fazer SELECT dos valores anteriores antes do UPDATE | `user` |
| Par de transferência | `transaction_service` (cópia para o par) | mesma do par |
| Regras na ingestão / criação | `rule_service.apply_rules_to_transaction`, `rule_engine` | `rule` |
| `apply_single_rule` (com e sem overwrite), `apply_all_rules` | `rule_service` | `rule` |
| Pluggy / herança de parcela | `connection_service` (OAuth callback e sync) | `provider` / `installment` |
| Placeholder recorrente casado, pendentes recorrentes | `connection_service`, `import_service`, `recurring_transaction_service` | `recurring` |
| Import (preview escolhido pelo usuário / sugestão de regra / coluna CSV) | `import_service` | `user` / `rule` / `import` |
| Exclusão de categoria com transferência (merge) | `category_service._transfer_category_references` | mantém a origem, registra evento |
| Pipeline novo | `ai_categorization` | `memory` / `ai` |

**Proteção**: `user` nunca é tocado por camada automática. `apply_all_rules` passa a respeitar a
proteção (corrige o bug que hoje apaga categoria e notas manuais, `rule_service.py:1488-1491` —
necessário porque a feature dá a esse botão um papel maior).

**Limpar a categoria** (a pessoa deixa a transação sem categoria) grava origem `none`, com o evento
em nome dela: a transação volta a ser revisável, mas, enquanto essa limpeza for o último evento de
pé, memória e Claude só sugerem a categoria (vai para Revisar), nunca a aplicam sozinhos.

**Desfazer** restaura `previous_*` e grava evento `undo`; em lote por `run_id` ou seleção. Uma
confirmação (o pipeline assume um palpite na categoria que ele já tinha) não conta como aplicada
nem é desfeita pelo lote.

## 7. Claude — categorizador

**Unidade de trabalho = chave de loja.** Uma decisão por loja vale para todas as suas transações
candidatas.

**Entrada por item**: id do item por requisição (`i1`, `i2`…, mapeado no backend para a chave de loja —
a chave nunca vai à API, porque `PIX:#<hash>` de um CPF é reversível por força bruta), até 2 descrições
de exemplo truncadas em 60 caracteres (sem documentos, agência, conta ou cartão), quantidade, valor
mediano, débito/crédito, se parece recorrente, tipo de conta e, quando a pessoa já escolheu categorias
para aquela loja, o histórico `h` (até 3 pares `[id curto, vezes]`, do mais escolhido para o menos).

**Contexto fixo (cacheado)**: instruções; árvore com IDs curtos (`c1`, `c2`… mapeados no backend, em
ordem determinística); até ~40 exemplos reais do usuário (loja → categoria).

**Saída** (structured outputs, schema estrito) por item: `k`, `c` (ID curto ou `null`), `alt`, `n`
(nome limpo), `p` (0–100), `r` (motivo ≤ 12 palavras), `new` (`{grupo, nome}` opcional).

**Aplicação**: `p ≥ limite` (padrão 90, configurável) aplica com origem `ai`; abaixo, vira
`categorization_suggestions` (pendente em Revisar, com `c` e `alt`). `new` acumula como sugestão de
categoria (§9). `stop_reason: "refusal"` (o Haiku não tem fallback no servidor): os itens do lote vão
para Revisar sem sugestão.

**Execução** — nada no request HTTP:
- **Pós-ingestão**: helper `enqueue_categorization(workspace_id)` chamado depois dos commits do OAuth
  callback, do sync e do import. A task é por workspace, com debounce (não enfileira se já há uma
  pendente) e pega as candidatas criadas desde o último run — sem mudar a assinatura de
  `sync_connection`.
- **Backlog / "Categorizar agora"**: o botão cria um run (task Celery). Poucas lojas (≤ 100) → Messages
  API normal; mais que isso → Message Batches. A UI acompanha o run por polling do status.
- **Batches**: task do beat consulta runs em `processing` a cada poucos minutos e aplica resultados
  (chave por `custom_id`).

## 8. Tokens e modelos

| Tarefa | Modelo | Config | Por quê |
|---|---|---|---|
| Categorizar lojas | `claude-haiku-5-5` | thinking desligado, `effort: low` | Classificação curta; $0,10/$0,50 por MTok |
| Padrão de regra a partir de correção | — (sem modelo) | `description contains <chave de loja>` | A chave de loja já é o padrão generalizado; custo zero (desvio do plano, ver §10) |
| Organizador da árvore | `claude-opus-5-5` | adaptive, `effort: medium`, `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`), chamada normal (fallbacks não funciona em Batches) | Raro, exige julgamento; ~US$ 0,20 por execução |

**[decisão minha]** — o padrão do Claude seria Opus para tudo; Haiku no volume é escolha de custo
pedida pelo usuário ("otimize os tokens").

Otimizações, em ordem de impacto:
1. Não chamar a API: regras → memória → cache de decisão por loja → deduplicação por loja.
2. Lotes de até 100 lojas.
3. Message Batches (−50%) para backlog e pós-sync.
4. Prompt caching do contexto fixo (mínimo 512 tokens nesses modelos); nada volátil no prefixo.
5. Saída mínima (IDs curtos, chaves de 1 letra), `max_tokens` pelo tamanho do lote.
6. Entrada mínima (§7).
7. Teto mensal (padrão US$ 1); ao atingir, a camada 3 pausa e a UI avisa.

**Custo**: tabela própria `categorization_runs` (`workspace_id`, tipo, modelo, status, `batch_id`,
tokens de entrada/saída/cache-escrita/cache-leitura, flag batch, custo calculado, contadores). O teto
mensal soma essa tabela. Os preços ficam numa constante própria do módulo (não no `_PRICING_PER_M`
do agents, que não conhece cache nem batch).

Ordem de grandeza: backlog atual ≈ 300–500 lojas → menos de US$ 0,01; uso diário, frações de centavo.

## 9. Organizador de categorias

**Sob demanda**, na aba Organizar (§10), sugerido no primeiro uso.

**Entrada**: árvore atual + resumo por loja (chave, nome limpo, categoria atual, quantidade e valor em
12 meses). Nunca transações cruas.

**Saída**: operações com justificativa e impacto: `create_group`, `create_category`, `rename`,
`move_category`, `merge`, `split_category`.

**Regras**: dois níveis; grupos amplos, categorias específicas; categoria nova só com ≥ 3 transações ou
≥ R$ 200 em 12 meses **[decisão minha]**; 2 a 6 categorias por grupo; reaproveitar antes de criar.

**Como aplica sem quebrar nada**
- `split_category` (ex.: "Lazer" vira grupo "Lazer" com Assinaturas/Ingressos/Cinema): cria o grupo,
  **reaproveita o id da categoria antiga como uma das filhas** (renomeada e movida) e cria as demais.
  Orçamentos, regras e recorrentes que apontam para o id antigo continuam válidos. As transações da
  categoria são redistribuídas pelo Claude entre as filhas — inclusive as `user`, porque o usuário
  aprovou dividir aquela categoria — com eventos e desfazer. Por isso a filha escolhida é aplicada
  qualquer que seja a confiança (o evento guarda a confiança); sem categoria, a transação fica na
  primeira filha, sem sugestão. Uma redistribuição que para no teto mensal pode ser tentada de novo.
- `merge`: usa o `delete_category(transfer_to_id=...)` existente (já move transações, recorrentes,
  regras e soma orçamentos), agora instrumentado com eventos.
- `move_category`/`rename`: `PATCH` existente.
- **Mapa do Pluggy**: o match hoje é por nome (`connection_service.py:214`, `_match_pluggy_category`)
  e quebra se a categoria for renomeada. A feature grava o mapeamento resolvido por id (na primeira
  execução) e o match passa a consultar o id antes do nome.
- **Furo pré-existente**: criar/editar categoria não valida se `group_id` é do workspace
  (`category_service.py:153`, `:226`). Corrigido aqui, porque o organizador move categorias.
- **Desfazer** — **[decisão minha]**: as recategorizações de transações são desfeitas como qualquer
  run. As mudanças de estrutura (criar, renomear, mover, dividir, mesclar) são aprovadas uma a uma e
  não têm desfazer automático; mesclar pede confirmação extra porque é irreversível.

**No dia a dia**: quando ≥ 3 lojas pendentes sugerem a mesma categoria nova, Revisar mostra
"Criar Lazer › Games? (6 transações)". Nunca cria sozinho.

## 10. Experiência (onde fica cada coisa e com o quê)

Princípio: **um lugar para cuidar de categorização**, sinais pequenos e consistentes nas telas que o
usuário já usa, e ação a um clique onde a dúvida aparece.

| Peça | Onde | Componentes/padrões reaproveitados |
|---|---|---|
| **Página "Categorização"** `/categorization` | Item novo na sidebar, seção **Contas**, logo abaixo de Transações (`lib/nav-items.ts`, `lib/modules.ts`, rota em `App.tsx` com `ModuleRoute module="categories"`), com **contador âmbar** de pendências (campo novo `badgeQueryKey` em `NavItem`, mesmo pill da fila de conciliação). Entrada também no command palette ("Revisar categorias") | `PageHeader`; abas `Segmented` endereçáveis por `?tab=` (padrão de `rules.tsx`): **Revisar · Aplicadas · Organizar**. Header com status do último run, botão "Categorizar agora" (estimativa de custo antes) e link para Configurações |
| **Revisar** | Aba `?tab=review` | Molde da `ReconciliationQueue`: `SectionCard`, header com contador, linha por **loja** expansível (mostra as transações daquela loja). Na linha: nome limpo + chave, quantidade, total (`mask(formatCurrency)`), motivo da IA; ações = 2 `Button outline size="sm"` com `CategoryIcon` + nome (palpites), `CategorySelect` "outra…", "Aceitar todas". Perguntas de Pix ("Quem é Maria Silva?") como linhas próprias. Sugestões de categoria nova em bloco separado no topo. Atalhos de teclado: `1`/`2` aceita palpite, `↓` próxima loja. Estado vazio positivo (padrão `CircleCheck` verde) |
| **Aplicadas** | Aba `?tab=applied` | Molde da `ReconciliationHistory` (mapa ícone/cor por origem), agrupado por dia e loja; filtros por origem e confiança; checkbox nativo + **barra fixa de seleção** copiada de `transactions.tsx` para "Desfazer selecionadas"; "Desfazer este lote" por run. Corrigir uma linha ensina a memória |
| **Organizar** | Aba `?tab=organize`; também link no header de `/categories` | Árvore no estilo de `categories.tsx` (grupos colapsáveis); cada operação como `ProposalCard` (âmbar → verde ao aplicar) com impacto ("14 transações, R$ 820"); diff em linha (verde novo, riscado o que sai); mesclar usa o Dialog de confirmação no padrão de `payees.tsx` |
| **Selo de origem** | Ao lado do título da transação em `transactions.tsx` (`renderDescriptionCell`), `mobile-transaction-row.tsx`, `account-detail.tsx`, drill-down e calendário | Componente novo `categorization-origin-badge.tsx` no molde de `projected-transaction-badge.tsx` (pill `text-[10px]` + ícone `h-3 w-3`): `Sparkles` IA (violeta), `Brain` memória, `Wand2`/`Cog` regra; pendente = âmbar com `Link` para Revisar. `Tooltip` com o motivo. Sem selo para `user`/`legacy` (menos ruído). Filtro "Origem" na barra de filtros de Transações |
| **"Como chegou aqui"** | `transaction-dialog.tsx`, logo abaixo do `CategorySelect` | Caixa `bg-muted/50 border rounded-md` (padrão do banner de recorrente vinculada): original (descrição do banco, categoria anterior) × aplicado (categoria, nome limpo, quem decidiu, confiança, motivo, data) + botão "Desfazer". "Criar regra" continua no footer |
| **Depois de corrigir** | Qualquer edição de categoria (Transações, conta, dashboard, drill-down) | Toast `sonner` com ações: "Aplicar às 8 parecidas" e "Criar regra". Regra abre o `RuleDialog` com `initialData` pré-preenchido pela chave de loja (`description contains <chave>`, ex.: `contém IFOOD`; prefixos `P:`/`PIX:` removidos; chave com hash `PIX:#…` não gera regra por descrição), sem chamada ao Claude — mesmo resultado, custo zero (desvio do plano) e prévia (`POST /rules/preview`). Padrão novo de toast com ação (inclui "Desfazer" após aceitar em lote) |
| **Tela inicial** | Substitui o banner de não categorizadas (`dashboard.tsx` ~868-904) | Mesmo lugar e estados (âmbar/verde): "47 categorizadas automaticamente esta semana · 5 para revisar →" abre `/categorization`; `Skeleton` no carregamento |
| **Configurações** | Seção nova em `workspace-settings.tsx` (protegida por `canManage`) | `ui/switch` (ligar/desligar), limite de confiança como `Segmented` (Conservador 95 · Equilibrado 90 · Agressivo 80), teto mensal (`Input` numérico em US$) com gasto do mês, conexão Claude usada |

Permissões: ações de escrita só com `canWrite`; configurações com `canManage`. Mobile: abas viram
`Segmented` rolável; linhas de Revisar empilham ações; barra de seleção usa
`MobileBulkSelectionActions`.

## 11. Configurações e dados novos

Tabela `categorization_settings` (uma linha por workspace, padrão de `InvoiceSettings`): `enabled`,
`auto_apply_threshold`, `monthly_cap_usd`, `llm_connection_id`, `owner_user_id`,
`provider_category_map` (JSON), `last_run_at`.

Tabelas novas (migração 097): `categorization_settings`, `categorization_runs`,
`categorization_suggestions`, `categorization_events`, `merchant_memory`; coluna
`transactions.category_source`.

## 12. Privacidade

Vai para a API: descrição truncada, nome da contraparte (nunca CPF/CNPJ), valor, débito/crédito, tipo
de conta, nomes das categorias. Não vai: número de conta/cartão, documento, saldo, notas, anexos.

## 13. Fora do escopo, registrado

- Regras não aceitam condição "sem categoria"/"categoria atual".
- `merge_payees` não reaponta regras `set_payee`.
- `propose_categorize` do MCP anuncia a rota `POST /api/transactions/categorize`, que não existe.

## 14. Decisões do dono do produto (2026-10-19)

1. As 1.327 transações já categorizadas entram como **revisáveis** (`provider`), sem restrição: a IA
   pode recategorizá-las, com eventos e desfazer. A origem `legacy` não é usada nesta instância (fica
   no enum para workspaces onde se queira congelar o histórico) e o histórico existente **não** semeia
   a memória.
2. A chave do Claude é cadastrada pelo módulo de IA do Securo: `AGENTS_ENABLED=true` na stack do
   Tarsonis e uma `LlmConnection` `kind='anthropic'` marcada como padrão. A chave nunca entra no
   repositório.
3. Prioridade explícita: UX simples e intuitiva, ações rápidas (um clique, atalhos), tokens otimizados.

## 15. Deixado para o builder

- Normalização exata da chave de loja e lista de prefixos.
- Nomes de endpoints e schemas.
- Ordem das fatias de entrega (sugestão: proveniência + memória + Revisar sem IA → Claude → telas de
  auditoria → organizador).
- Testes: funções puras sem API; Claude mockado; caminhos com FK testados em Postgres.
