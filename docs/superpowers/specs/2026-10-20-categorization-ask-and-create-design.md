# Categorização — perguntar à IA, criar na hora e "aplicar às parecidas"

Status: aprovado em conversa (2026-10-20; limite de 30 mensagens por conversa definido pela pessoa). Complementa `2026-10-19-ai-categorization-design.md`.

## Por quê

Na aba Revisar, quando a IA erra ou fica em dúvida, a pessoa só pode escolher uma categoria que já
existe. A IA nunca fica sabendo o motivo, então lojas ambíguas (Pix para pessoas, nomes genéricos)
voltam sempre em dúvida, e categorias que ainda não existem ("Transporte › Carro", "Jardinagem",
"Nimbloo") exigem sair da tela.

## O que entra

### 1. Perguntar à IA — conversa em caixa de diálogo

- Botão **Perguntar à IA** na linha da Revisar, dentro do "Outra..." (Revisar e Aplicadas) e na linha
  da Aplicadas. Abre um diálogo grande (o popover é apertado demais) já com o contexto da loja: nome
  limpo, nº de transações, total, última data e até 3 descrições de exemplo.
- **Conversa** (chat): a pessoa explica; a IA responde em texto curto e pode perguntar de volta. Toda
  resposta que propõe categoria vem num **cartão** com a categoria (existente ou nova, com grupo
  existente ou novo), a confiança, "aplica nas N transações desta loja", **Aceitar** e
  **Outra categoria**.
- **Aceitar** faz tudo: cria grupo/categoria se novos, aplica nas transações pendentes da loja como
  escolha da pessoa (ensina a memória), salva a nota da loja, fecha o diálogo e devolve o foco à mesma
  linha — onde aparece a pergunta das parecidas (§3).
- **Nota da loja**: a IA devolve, junto com a sugestão, um resumo curto do contexto que a pessoa deu
  (≤ 200 caracteres, ex.: "Aurino: jardineiro semanal"). Ela é salva ao aceitar e vai junto nas
  próximas categorizações da loja (lote e conversa). Aparece e pode ser apagada no bloco "Como chegou
  aqui" e na linha da Revisar.
- Fechar sem aceitar não muda nada. A conversa vive só no estado da página (não é gravada no banco);
  reabrir a mesma loja na mesma sessão retoma a conversa.
- Escopo da IA: só a categoria daquela loja. Pedidos fora disso são recusados com educação, apontando
  onde fazer (ex.: Regras).
- Modelo/custo: Haiku 5.5 (thinking desligado, effort low, saída estruturada `{reply, suggestion?,
  note?}`), histórico curto da conversa reenviado a cada mensagem com cache do contexto fixo. Cada
  mensagem conta no teto mensal. **Limite: 30 mensagens por conversa.** Com IA desligada, sem conexão
  ou teto estourado, o botão aparece desabilitado com o motivo.
- Privacidade: o texto da pessoa passa pela mesma sanitização (CPF/CNPJ, conta/cartão) antes de ir à
  API; chaves de loja nunca vão (ids curtos, como no lote).
- Exceção consciente ao desenho original ("nenhuma rota chama o Claude no request"): aqui a pessoa
  pede e espera. Limites: timeout de 15 s por mensagem; 20 mensagens por minuto por workspace.

### 2. Criar categoria e grupo na hora, estilo Notion (todo "Outra..." da Revisar e da Aplicadas)

- Ao digitar um nome que não existe, aparecem: **Criar "X" em <grupo>** (escolhendo entre os grupos
  existentes; o grupo sugerido primeiro é o da sugestão atual, se houver) e **Criar "X" como grupo
  novo** (o grupo nasce com uma categoria de mesmo nome, para poder ser aplicada já).
- Atalho: `Grupo / Nome` cria direto no grupo (criando o grupo se ele não existir).
- Ícone e cor sugeridos automaticamente; editáveis depois em Categorias.
- Só para quem pode escrever no workspace (mesma regra do `creatable` atual).

### 3. Toda escolha manual pergunta pelas parecidas

- Depois de aceitar, escolher em "Outra...", aplicar a resposta da IA ou criar e aplicar, se a mesma
  loja tiver outras transações (fora das que acabaram de ser aplicadas) com **categoria diferente** e
  origem não protegida, aparece: "Aplicar também às N parecidas desta loja?" com **Aplicar às N**,
  **Ver quais** e **Não**. Nada muda sem o clique; cada "Aplicar às N" tem desfazer.
- Transações categorizadas pela pessoa nunca entram na conta. O toast de correção já existente nas
  outras telas continua como está.

### 4. Aplicadas mostra tudo o que tem categoria + placar

- Filtros novos na Aplicadas: **Confirmadas** (a IA/memória confirmou a categoria do banco; não
  entram na contagem do card) e **Suas** (decididas pela pessoa), ao lado de IA, Memória e Regra.
  Com isso, Revisar + Aplicadas cobrem toda transação categorizável (ficam fora só transferências
  entre contas, pendentes e saldo inicial).
- Placar no topo da seção: "N transações · X% decididas · Y% para revisar" (com o que fica de fora
  explicado em tooltip).
- O "Outra..." da Aplicadas ganha a criação na hora e o Perguntar à IA; corrigir uma linha dispara a
  pergunta das parecidas (§3).

### 5. Correção encontrada nos dados reais: prefixos de adquirente

- Descrições como `Mlp*Netshoes`, `Dl*Aliexpress`, `Ebn *Amazon`, `Ubr* Pending.Uber.Com`,
  `Pb*Ubiquiti`, `Zig*...`, `Tst*...`, `Vindi*...`, `Sq *...`, `Bkg*Hotel At Booking` hoje viram a
  loja do prefixo (ex.: "MLP" junta Netshoes e Kabum). Regra nova: um prefixo curto (2 a 5 letras)
  seguido de `*` é removido por padrão; exceções: quando o resto é genérico ("PENDING..."), a loja é a
  marca do prefixo (UBR/UBER → UBER); prefixos que são a própria marca continuam a marca.
- Deploy: regravar o `merchant_key` das sugestões pendentes e fundir as entradas da memória cujas
  chaves mudaram (a de "MLP" é descartada, pois mistura lojas).

## Fora do escopo

- Perguntar à IA fora da seção Categorização (diálogo da transação etc.).
- Gravar o histórico das conversas no banco.

## Decisões do construtor (registradas)

- A nota da loja é uma coluna nova na memória da loja (migração 099), não uma tabela.
- Sobras do worker (do lote B2): `committed_spend` numa consulta só (ou coleta pegando o lock de
  gasto), teste de envio concorrente, lote pago sem rastreio quando há exceção entre `create_batch` e
  o commit, import local de `or_` em `review.request_run`.
- A pergunta reutiliza prompts/sanitização do classificador; o resultado não grava sozinho a decisão
  em cache da IA — só a aplicação (que vira escolha da pessoa e ensina a memória).

## Decisões tomadas na leitura do construtor (2026-10-20, chamadas minhas — conferir)

1. **Escopo do Aceitar e das parecidas.** Aceitar (chat, "Outra...", sugestão) aplica às transações da
   loja que estão na tela de onde veio a ação: na Revisar, as pendentes daquela loja; na Aplicadas, a
   linha (ou a seleção) corrigida. A pergunta das parecidas cobre as demais transações da mesma loja
   com categoria diferente e origem `none`, `provider`, `memory`, `ai`, `import`, `recurring` ou
   `installment`. **Ficam fora** `user`, `legacy` e também `rule` (uma regra é decisão explícita da
   pessoa; se ela quiser mudar, muda a regra). "Ver quais" lista exatamente as N.
2. **Placar e filtros.** Base = transações categorizáveis (postadas, sem par de transferência, sem
   saldo inicial, não ignoradas). "Para revisar" = com sugestão pendente. "Decididas" = com categoria e
   sem sugestão pendente. Se sobrar "sem categoria e sem sugestão", aparece como terceiro número
   ("aguardando a IA"); assim os números sempre somam 100%. Filtro **Confirmadas** = evento ai/memória
   não desfeito com categoria anterior = nova. Filtro **Suas** = origem `user`/`legacy` (por origem,
   não por evento, para incluir edições anteriores à feature).
3. **Limite de 30 e ritmo.** A conversa é sem estado no servidor: o cliente envia o histórico, e o
   servidor recusa (409) quando o histórico já tem 30 mensagens da pessoa. Recarregar a página zera a
   conversa (e o contador); o custo real fica protegido pelo teto mensal e pelo limite de 20 mensagens
   por minuto por workspace, feito com o rate limiter que o projeto já usa (se não houver, contador em
   Redis com expiração de 60 s — o Redis já existe na stack).
4. **Nota da loja e memória.** Salvar a nota cria a linha de memória se ela não existir, com contagens
   vazias (a nota não conta para "≥ 2 escolhas"). Uma aceitação conta **uma** escolha por loja (como
   hoje, R16), não uma por transação. Editar ou apagar a nota esquece a decisão em cache da IA daquela
   loja (o contexto mudou).
5. **Regravação de chaves (prefixos).** Data migration no deploy, idempotente: recalcula a chave das
   sugestões pendentes (são por transação, sem conflito de unicidade); na memória, chaves que viram a
   mesma são fundidas somando contagens, mantendo a escolha mais recente, a nota mais recente e
   descartando o cache da IA; chaves que se dividem (como `MLP`) são descartadas e a memória das novas
   chaves é re-semeada a partir das transações de origem `user` dessas lojas.
6. **Criação na hora.** Nome comparado sem acento e sem diferença de maiúsculas: grupo dentro do
   workspace, categoria dentro do grupo; se já existir, reaproveita em vez de duplicar (inclusive no
   atalho `Grupo / Nome`). Categoria nova proposta pela IA é criada direto ao Aceitar — o Aceitar é a
   aprovação. Perguntar à IA, criar e aplicar exigem `canWrite`; quem só lê vê o botão desabilitado com
   o motivo.
