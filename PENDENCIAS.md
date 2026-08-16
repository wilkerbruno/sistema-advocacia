# Status das pendências do briefing (atualizado em 16/08/2026)

## -4. Autopreenchimento por CNJ/CEP e Agenda com lembrete (reunião) — implementado nesta rodada

### Buscar dados do processo ao digitar o número CNJ
Na tela "Governança > Cadastrar por CNJ", digitar o número e apertar
**Enter** agora já consulta o DataJud e mostra uma pré-visualização
(tribunal, classe, assunto, órgão julgador, data de ajuizamento, quantas
movimentações existem) **antes** de cadastrar — sem gravar nada no banco
ainda. Se o tribunal for identificado automaticamente (Justiça do
Trabalho), o campo "Tribunal" já é preenchido sozinho; o campo "Área do
direito" recebe uma sugestão (assunto/classe do DataJud) só se ainda
estiver vazio. O cadastro de fato continua só acontecendo ao clicar em
"Validar e cadastrar" (rota nova: `GET /governanca/processos/consultar-cnj`,
só leitura).

### Autopreenchimento de endereço por CEP
No cadastro/edição de cliente, digitar o CEP e sair do campo (ou apertar
Enter nele) busca automaticamente logradouro/bairro/cidade/UF via
**ViaCEP** (`viacep.com.br` — gratuito, sem chave). A consulta passa pelo
próprio backend (`GET /api/cep/<cep>`, ver `app/utils/cep.py`) em vez de
ser feita direto do navegador, porque o ViaCEP não documenta oficialmente
suporte a CORS — assim funciona de forma confiável em qualquer navegador.
Esse é hoje o único campo de CEP do sistema (a Unidade não tem CEP
cadastrado); se um dia um CEP for adicionado em outro formulário, o mesmo
endpoint pode ser reaproveitado.

### Agenda: agendar reunião/compromisso com lembrete
Nova entidade `Compromisso` (`app/models/compromisso.py`) — evento livre
da Agenda, sem precisar estar ligado a um processo. Em "Agenda > + Novo
compromisso" dá pra definir: nome, local, data/hora do compromisso, um
horário separado para o lembrete ("Notificar em" — precisa ser antes do
horário do compromisso), e opcionalmente vincular um cliente.

O lembrete dispara sozinho, sem precisar de nada manual: um novo job
(`enviar_lembretes_compromissos.py`) já vem agendado dentro do próprio
container via cron a cada 5 minutos (`docker/lembretes-compromissos.cron`,
mesmo esquema já usado pela recaptura do DataJud — não depende de
nenhuma configuração no painel do EasyPanel). Ele:
- Sempre manda uma notificação dentro do sistema para o responsável pelo
  compromisso (não depende de nenhuma credencial).
- Também manda um e-mail para o responsável, **se** `SMTP_HOST`/
  `SMTP_USER`/`SMTP_PASSWORD` estiverem configurados no `.env` (mesmas
  variáveis já usadas por `enviar_relatorio_semanal.py` — se você já usa
  o relatório semanal por e-mail, o lembrete de compromisso já sai por
  e-mail também, de graça, sem configurar nada a mais).
- Nunca manda o mesmo lembrete duas vezes, mesmo rodando várias vezes.

**Como ativar:** rodar `python sincronizar_schema.py` no servidor depois
do deploy, para criar a tabela `compromissos`. Nada mais — a Agenda já
aparece com o botão "+ Novo compromisso" e o lembrete já roda sozinho.

### WhatsApp do lembrete — implementado (automação não-oficial, escolha sua)
Você optou pela automação não-oficial em vez da API paga da Meta (ver as
3 opções na seção 2.2 abaixo) — ciente do risco real de o número usado
ser banido pelo WhatsApp por comportamento automatizado, já que isso
viola os Termos de Serviço da plataforma. Implementado como pediu, com
esse risco documentado em vários lugares do código para nunca ficar
escondido.

**Como funciona:** o formulário de compromisso já tem a opção "Também
enviar por WhatsApp" — mas o envio de verdade depende de um **serviço
separado**, na pasta `whatsapp-bridge/` (Node.js + `whatsapp-web.js`), que
mantém uma sessão comum de WhatsApp Web logada (a mesma coisa que abrir
web.whatsapp.com no navegador e escanear o QR code, só que automatizado
via Puppeteer/Chromium). O app Flask principal só chama esse serviço por
HTTP quando precisa mandar uma mensagem — nenhuma lógica de WhatsApp roda
dentro do container principal.

**Por que um serviço separado, e não dentro do mesmo container:** manter
uma sessão de WhatsApp Web exige um navegador Chromium rodando o tempo
todo (não é uma chamada de API simples) — bem diferente do resto do
sistema (Flask + MySQL), então isolar num serviço próprio evita inflar o
container principal e deixa mais fácil reiniciar/depurar um sem afetar o
outro.

**Passo a passo para ativar (nenhum destes eu consigo fazer por você —
depende do seu painel do EasyPanel e do celular físico do escritório):**
1. No EasyPanel, crie um **segundo serviço** dentro do mesmo projeto,
   apontando para a pasta `whatsapp-bridge/` deste repositório (tem
   `Dockerfile` próprio).
2. **Anexe um volume persistente** em `/data/sessao` desse serviço — sem
   isso, todo redeploy derruba a sessão e pede escanear o QR code de
   novo. É o ponto mais fácil de esquecer.
3. Defina `BRIDGE_TOKEN` no `.env` desse serviço (qualquer string
   aleatória) — é o segredo que impede qualquer um que descubra a URL
   interna de mandar mensagem pela sua conta.
4. Depois do deploy, abra a URL interna do serviço + `/qr` no navegador
   (ex: `https://whatsapp-bridge-xxxx.easypanel.host/qr`) e escaneie com
   o WhatsApp do **número escolhido para isso** — use um número dedicado,
   nunca o WhatsApp pessoal de um advogado nem o número principal de
   atendimento do escritório, justamente por causa do risco de banimento.
5. No `.env` do app **principal** (não do bridge), defina
   `WHATSAPP_BRIDGE_URL` (a URL interna do serviço, ex:
   `http://whatsapp-bridge:3000` se os dois estiverem no mesmo projeto) e
   `WHATSAPP_BRIDGE_TOKEN` (o mesmo valor do passo 3).
6. Pronto — marque "Também enviar por WhatsApp" num compromisso vinculado
   a um cliente com número cadastrado, e o lembrete sai pelos 3 canais
   (sistema + e-mail + WhatsApp) na hora marcada.

**Recomendações práticas pra reduzir (não eliminar) o risco de
banimento**, documentadas também em `whatsapp-bridge/server.js`:
- Número dedicado só pra isso, nunca o pessoal de ninguém.
- Deixe o número "esquentar" alguns dias com uso normal (conversas reais)
  antes de começar a automatizar.
- Não mande volume alto de mensagens de uma vez — um lembrete de reunião
  por compromisso já é um uso naturalmente baixo.

Sem `WHATSAPP_BRIDGE_URL` configurada, nada disso quebra — o lembrete
continua saindo normalmente por notificação no sistema e e-mail, só o
WhatsApp fica desligado até você configurar.

---

## -3. Captura automática de processos (PJe/TRT/TJ/etc) — parcialmente desbloqueado, de graça, via DataJud

O item "integração com dados judiciais" (categoria 8 do concorrente),
antes marcado como bloqueado por depender de provedor pago, tem uma parte
real que **é gratuita**: o **DataJud**, a base pública oficial do CNJ
(Resolução CNJ 331/2020) — diferente de Judit/Escavador/Digesto/Codilo,
não é um provedor comercial, é o próprio CNJ. Qualquer pessoa se cadastra
de graça em https://datajud-wiki.cnj.jus.br/ (sem precisar de OAB/CNPJ) e
gera uma chave de API própria.

**Importante — o que ficou de fato coberto e o que continua exigindo um
provedor pago, pra não vender pro cliente final do escritório algo que
este conector não faz:**

- **Cobre de graça:** acompanhar o andamento de um processo cujo número
  CNJ você já tem — carga inicial (classe, assunto, órgão julgador, data
  de ajuizamento) e captura de movimentações, alimentando a mesma máquina
  de estados e motor de próxima ação que já existiam (`app/utils/estado_processual_engine.py`,
  `app/utils/prazos_engine.py`). Cobre nacionalmente todos os 91
  tribunais — todo TJ estadual, todo TRT, todo TRF, tribunais superiores.
  Para processos da Justiça do Trabalho, o tribunal (TRT) é identificado
  automaticamente pelo próprio número do processo; para os demais
  segmentos, é escolhido manualmente uma vez no cadastro do processo
  (campo "Tribunal (DataJud)").
- **Continua exigindo provedor pago (Judit/Escavador/Digesto/Codilo):**
  "buscar todos os processos de uma pessoa/empresa pelo nome ou CPF/CNPJ"
  sem já ter o número — o DataJud não indexa CPF/CNPJ (LGPD) e busca por
  nome de parte não é confiável o bastante pra automatizar. Também não dá
  pra baixar o inteiro teor de petições/decisões (só metadado + texto
  curto de cada movimentação), nem monitorar publicação no Diário de
  Justiça Eletrônico por OAB.
- Defasagem: dados do DataJud não são em tempo real — a atualização de
  cada tribunal pra base nacional varia de horas a dias, segundo o
  próprio CNJ.

**O que foi implementado:**
- `app/utils/conector_datajud.py` — implementação real de `ConectorCaptura`
  (o "encaixe" que já existia em `app/utils/captura_conectores.py`, antes
  sempre bloqueado). `obter_conector("padrao")` agora devolve esse
  conector quando `DATAJUD_API_KEY` está configurada.
- `app/utils/tribunais_datajud.py` — catálogo de tribunais suportados
  (trt1–24, todos os TJs estaduais, trf1–6, tribunais superiores).
- Novo campo `Processo.tribunal_datajud`, exposto nos formulários de novo
  processo, edição de processo e cadastro por CNJ.
- `app/routes/governanca.py` (`novo_por_cnj`) agora usa de fato o retorno
  da captura (antes só testava sucesso/falha e descartava o resultado) —
  preenche os dados iniciais do processo e já registra as movimentações
  capturadas.
- `capturar_movimentacoes.py` — script de recaptura periódica pra todos os
  processos monitoráveis. **Já vem agendado sozinho**: o `Dockerfile` agora
  instala um `cron` dentro do próprio container (`docker/entrypoint.sh` +
  `docker/capturar-movimentacoes.cron`) que roda esse script todo dia às 3h
  da manhã — não depende de nenhuma configuração extra no painel do
  EasyPanel (o EasyPanel não tem um recurso de "Cron Job" dedicado pra isso
  documentado oficialmente; resolvi rodando o cron dentro da própria
  imagem). O resultado de cada execução aparece no log normal da aplicação
  no EasyPanel (junto com o log do gunicorn) e em "Governança > BI/Métricas"
  (indicador de saúde da captura, baseado em `LogCaptura`).

**Como ativar:**
1. Cadastro gratuito em https://datajud-wiki.cnj.jus.br/, gerar a chave de
   API, definir `DATAJUD_API_KEY` no `.env` do servidor.
2. `python sincronizar_schema.py` — cria a coluna nova `tribunal_datajud`.
3. Testar com um processo real (de preferência um TRT, que não exige
   escolher o tribunal manualmente) em "Governança > Cadastrar por CNJ".
4. Nada mais a configurar — a recaptura diária já roda sozinha depois do
   deploy (ver acima). Se quiser forçar uma recaptura na hora em vez de
   esperar o horário agendado, rode `python capturar_movimentacoes.py` no
   console do servidor.

⚠️ Os nomes exatos dos campos da resposta da API (`movimentos`, `codigo`,
`nome`, `dataHora`...) seguem a documentação pública do DataJud, mas não
puderam ser testados contra uma chamada real (o ambiente onde este código
foi gerado tem rede de saída restrita e não alcança `datajud.cnj.jus.br`).
Teste com um processo real e, se algum campo vier consistentemente vazio
(diferente de "processo não encontrado"), me avise com um exemplo do JSON
de resposta pra eu ajustar o mapeamento.

---

## -2. Agente de IA passou a rodar num modelo local (até 2B parâmetros), não mais Claude

A pedido explícito: o Agente de IA jurídica (`/agente-ia`) trocou a API da
Anthropic (Claude) por um modelo pequeno rodando **dentro do próprio
servidor** — Qwen2.5-1.5B-Instruct, quantizado em GGUF (~1,1 GB), via
`llama-cpp-python`. Sem chave de API, sem custo por mensagem, sem dado do
escritório saindo do servidor.

**Trade-off que foi avisado antes de implementar, e o cliente escolheu
mesmo assim — registrado aqui pra não virar surpresa depois:** um modelo
de até 2B parâmetros é bem mais fraco que uma API de ponta como a do
Claude. Ele alucina mais — principalmente em português e em raciocínio
jurídico mais elaborado — e é mais lento por rodar em CPU. Os system
prompts (`app/routes/agente_ia.py`) seguem instruindo o modelo a nunca
inventar número fora do contexto real injetado e a sinalizar quando é só
sugestão a validar, mas a revisão humana das respostas importa ainda mais
aqui do que já importava com o Claude. As telas do agente também foram
atualizadas com esse aviso.

**Peso em RAM — o ponto mais importante pra operação:** o modelo fica
carregado em memória por processo do gunicorn (carregamento tardio, só na
primeira mensagem que aquele worker atender). Por isso o `Dockerfile`
também mudou de `-w 4` para `-w 2` workers — com 4, o pior caso somaria
~4-6 GB de RAM só de modelo; com 2, fica em ~2-3 GB. Se o servidor tiver
bastante RAM sobrando (8 GB+), pode voltar pra 4 workers; se aparecer erro
de memória (worker killed) mesmo com 2, reduza pra 1 no `CMD` do
`Dockerfile`. Vale acompanhar o consumo de RAM nos primeiros dias de uso
real do agente.

**Como ativar:**
1. Nenhuma configuração de chave é necessária — é local por padrão.
2. O download dos pesos do modelo (~1,1 GB) roda sozinho durante o build
   da imagem Docker (`baixar_modelo_ia_local.py`, chamado pelo
   `Dockerfile`). Se o build falhar exatamente nessa etapa (ex: rede do
   servidor bloqueando `huggingface.co`), rode o script manualmente no
   console do servidor depois do deploy: `python baixar_modelo_ia_local.py`.
3. Depois do primeiro deploy, abra uma conversa de teste em `/agente-ia`
   pra confirmar que a resposta vem normal (não como "Agente indisponível").
   A primeira mensagem de cada worker demora um pouco mais (carregando o
   modelo em memória); as próximas são mais rápidas.

**Reversível:** a chave `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` continua
existindo em `config.py`/`.env.example` (não usada por padrão) — pra voltar
a usar Claude, é só restaurar a versão anterior de `_chamar_llm()` em
`app/routes/agente_ia.py` (ver histórico do git) e reinstalar `anthropic`
no `requirements.txt`.

---

## -1. Paridade com ForLegal — o que foi construído nesta rodada e o que continua bloqueado

Levantamento pedido: cobrir, para as empresas clientes, a lista de 8 categorias
de recursos de um concorrente (ERP jurídico, controladoria, automação,
BI, jurimetria, IA jurídica, financeiro, integração com dados judiciais).
Everything que dependia só de código foi implementado nesta rodada:

- **Agenda integrada** (`/agenda`) — calendário mensal combinando prazos,
  audiências e tarefas num só lugar (antes eram 3 listas separadas).
- **BI de desfecho** — novos campos `desfecho`, `data_encerramento`,
  `observacao_desfecho` em `Processo` (editáveis na tela de edição do
  processo, seção "Risco, contingenciamento e desfecho"). Alimentam, em
  `/governanca/metricas`, taxa de sucesso, contagem de ganhos/perdas/acordos
  e tempo médio de duração de processos encerrados.
- **Produtividade por advogado** (`/governanca/produtividade`) — ranking
  individual de cumprimento de prazo, tarefas concluídas/atrasadas e horas
  apontadas.
- **Timesheet** (`/timesheet`) — apontamento de horas trabalhadas, com ou
  sem vínculo a processo, marcação de faturável/não faturável. Novo model
  `Apontamento`.
- **Contingenciamento jurídico formal** (`/governanca/contingenciamento`) —
  provisão real (valor da causa × percentual da classificação
  provável/possível/remoto, com override manual por processo), não só a
  soma bruta por `classificacao_risco` que já existia. A tela de edição do
  processo agora também expõe `classificacao_risco`, que antes existia no
  banco mas não tinha campo de formulário nenhum — gap do sistema original,
  corrigido de passagem.
- **Agentes de IA jurídica** (`/agente-ia`) — três personas (Operação,
  Gestão, Negócios), cada uma com system prompt próprio e um "contexto atual
  do escritório" (números reais do banco, no escopo do usuário logado)
  injetado a cada mensagem, para a resposta ser embasada em dado real, não
  inventado. Motor atualizado depois: passou a rodar um modelo local de até
  2B parâmetros em vez do Claude — ver seção -2 acima para os detalhes e
  trade-offs dessa mudança.

**Atualizado depois — categoria 8 (captura de dados judiciais) parcialmente
desbloqueada de graça:** ver seção -3 acima. Acompanhamento automático de
processo pelo número (todos os tribunais do país) agora funciona via
DataJud (API pública do CNJ), sem custo.

**Continua bloqueado — não é falta de código, é contrato/credencial externa
que ninguém consegue simular:**
- Jurimetria/análise preditiva de verdade (categoria 5) e busca de
  processos por nome/CPF sem já ter o número (parte da categoria 8)
  continuam dependendo de um provedor de dados processuais contratado
  (Judit/Escavador/Digesto/Codilo) — ver seção -3 acima e
  `app/utils/captura_conectores.py`. Uma "probabilidade de êxito" calculada
  sem dado histórico real seria inventada — não foi construída.

**Passo a passo pra ativar o que foi implementado nesta rodada:**
1. `python sincronizar_schema.py` no servidor — ele introspecciona os models
   automaticamente e cria só o que faltar (tabelas `apontamentos_horas`,
   `conversas_agente_ia`, `mensagens_agente_ia`, e as colunas novas em
   `processos`). Sempre pergunta antes de aplicar, nunca apaga dado.
2. `pip install -r requirements.txt` — o agente de IA agora usa
   `llama-cpp-python` (ver seção -2 acima para o restante da ativação).
3. Classifique os processos ativos existentes em "Contingenciamento"
   (provável/possível/remoto) — sem isso, `/governanca/contingenciamento`
   fica com a provisão zerada mesmo tendo processos com valor de causa.

---

## 0. Multi-tenant / SaaS (implementado nesta rodada, precisa de teste real)

Sistema convertido para multi-tenant: `Empresa` → `Unidade` → `Usuario`,
admin desenvolvedor (empresa dona da plataforma, vê tudo) vs. admin de
empresa (vê só a própria empresa), licenciamento (mensal/trimestral/anual
com valor negociado por empresa, nunca exposto como tabela pública) e
cobrança real via Mercado Pago (Checkout Pro + webhook).

**O que foi validado aqui:** sintaxe de todo o código, boot completo do
app (63 rotas registrando sem erro, incluindo as novas de plataforma/
licenciamento), e o escopo de dado por empresa foi revisado tela por tela
(inclusive dois vazamentos reais que existiam antes — listas de "unidade"
e "responsável" em formulários que mostravam dado de todas as empresas
para um admin — corrigidos e centralizados em dois helpers únicos,
`unidades_do_escopo()`/`usuarios_do_escopo()`, pra não vazar de novo numa
tela futura).

**O que NÃO foi possível testar daqui** (sem acesso de rede a nenhum dos
dois): 
- Contra o MySQL real — rode `python migrar_multitenant.py` no seu
  servidor (ele pergunta antes de aplicar qualquer coisa, nunca mexe em
  dado existente sem confirmação).
- Contra a API real do Mercado Pago — o formato de request/response segue
  a documentação oficial da API de Preferências, mas teste a primeira
  cobrança com credencial de sandbox antes de usar em produção.

**Passo a passo pra ativar:**
1. `python migrar_multitenant.py` — cria a empresa dona da plataforma e
   vincula as unidades existentes a ela.
2. Adicione `MERCADOPAGO_ACCESS_TOKEN` no `.env` (você já tem o token).
3. Cadastre a primeira empresa cliente de teste em `/plataforma/empresas/nova`
   (isso já cria a unidade e o admin dela).
4. Faça login como esse admin, vá em "Minha licença" e teste o botão de
   pagamento (idealmente com uma preferência de valor baixo, em sandbox).
5. Confirme no painel do Mercado Pago que o webhook está configurado para
   `https://seu-dominio/webhooks/mercadopago` (a URL é gerada automaticamente
   ao criar a preferência, mas vale conferir nos logs se a notificação chegou).

---

# Status das pendências do briefing (atualizado em 13/08/2026)

Este arquivo substitui o relatório de pendências anterior. Organizado em
três grupos: **resolvido nesta rodada**, **bloqueado (com o motivo exato)**,
e **decisão sua antes de continuar**.

---

## 1. Resolvido nesta rodada

| Item do briefing | O que foi feito | Onde |
|---|---|---|
| Validação de número CNJ | Dígito verificador (módulo 97) implementado e testado (2.000 casos gerados/revalidados) | `app/utils/cnj.py` |
| Cadastro por CNJ | Tela dedicada, valida antes de cadastrar | `app/routes/governanca.py::novo_por_cnj` |
| Importação em lote | Upload de CSV, processamento síncrono, relatório linha a linha | `governanca.py::importar_lote` |
| Motor de prazos (dias úteis) | Calcula data fatal pulando fins de semana e feriados/recesso forense da tabela `Feriado`; suporta prazo em dobro | `app/utils/prazos_engine.py::calcular_data_fatal` |
| Fechamento de prazo por evidência | `cumprido` só é aceito com `evidencia_movimentacao_id` ou `evidencia_documento_id` — a rota antiga de "marcar cumprido" agora recusa | `app/routes/processos.py::cumprir_prazo_com_evidencia` |
| Máquina de estados (tradução TPU) | Lê `MapaEstadoTPU`, atualiza `estado_negocio_atual` do processo e grava `HistoricoEstadoProcesso`; código não mapeado cai em `triagem_pendente` | `app/utils/estado_processual_engine.py` |
| Motor de próxima ação | Casa por `codigo_tpu` ou, na ausência dele, pelo texto do ato; cria `Prazo` automaticamente; sem regra, cria tarefa genérica de análise | `app/utils/prazos_engine.py::aplicar_regra_proxima_acao` |
| Cofre de senha (criptografia real) | Fernet cifra/decifra de verdade; acesso restrito a quem cadastrou (ou admin); toda leitura registrada em log | `app/utils/cofre.py`, `governanca.py::cadastrar_senha_processo` / `ver_senha_processo` |
| Marcação "não monitorável" | Tela e motivo editável, aparece no painel | `governanca.py::marcar_nao_monitoravel` |
| Fila de intimações | Tela dedicada, ordenada por data fatal, com evidência visível | `governanca.py::fila_intimacoes` |
| Painel de governança | 7/15 dias, vencidos sem evidência, parados 30/60/90, exposição financeira por fase/risco, movimentações críticas 24h, não monitoráveis | `governanca.py::painel` |
| Métricas de governança | Taxa de cumprimento, tempo médio publicação→protocolo, idade média da carteira, cobertura de monitoramento automático, % captura saudável (fica em branco até existir captura real), ranking de prazos perdidos por processo | `governanca.py::metricas` |
| Auditoria com filtro | Por usuário e por período | `app/routes/admin.py::auditoria` |
| Export para Data Lake | CSV autenticado de processos/movimentações/decisões/prazos | `governanca.py::exportar_csv` |
| Preview do relatório semanal | Mostra exatamente o que seria enviado (envio real está bloqueado — ver seção 2) | `governanca.py::relatorio_semanal_preview` |

| Log de auditoria com MAC | Best-effort via tabela ARP do servidor — só resolve quando o dispositivo está na mesma rede local do servidor (funciona no seu teste atual; não funciona pela internet, é limitação de protocolo, não bug) | `app/utils/rede.py`, coluna `mac_address` em `logs_atividade` |
| API de leitura autenticada (Data Lake) | Token via `DATALAKE_API_TOKEN`, endpoints `/api/v1/*`, com sincronização incremental por `?desde=` | `app/routes/api_integracao.py` |
| Envio real do relatório semanal | `smtplib` puro, roda via cron; sem SMTP configurado, avisa e não envia | `enviar_relatorio_semanal.py` |
| Script de sincronização de schema | Cria só tabela/coluna faltando, nunca apaga nada | `sincronizar_schema.py` |

Nenhuma tabela nova precisou ser criada — o schema que já existia sustenta
tudo isso. `python criar_tabelas.py` não precisa rodar de novo por causa
desta entrega. A única coluna nova é `logs_atividade.mac_address` — rode
`python sincronizar_schema.py` para criá-la no seu banco.

---

## 2. Bloqueado — não é falta de código, é decisão/credencial externa

### 2.1 Ingestão automática real (Judit/Escavador/Digesto/Codilo, ou scraping de DJE/PJe)
**Por quê:** cada provedor tem contrato, autenticação e formato de resposta
próprios — só existem depois de uma conta contratada. Criei a interface
pronta para receber isso (`app/utils/captura_conectores.py`), mas nenhuma
implementação real pode ser escrita sem:
1. Você escolher o provedor (seção 5.2 do briefing sugere Judit, Escavador,
   Digesto ou Codilo).
2. Contratar e obter a chave de API.
3. Testar a partir do **servidor de produção** — este ambiente de geração de
   código só acessa uma lista restrita de domínios (PyPI, GitHub, npm etc.)
   e não alcança APIs de dados processuais nem, confirmei agora, o próprio
   MySQL do EasyPanel do projeto.

Consequência em cascata: sem isso, a tradução de estado e o motor de próxima
ação continuam rodando apenas a partir de movimentação registrada manualmente
(a aba "Governança" dentro do processo virou o ponto de entrada provisório).

### 2.2 Envio de e-mail (relatório semanal) e WhatsApp (Evolution API)
**Atualizado:** o envio por e-mail agora está implementado de verdade
(`enviar_relatorio_semanal.py`, usa `smtplib` puro — sem dependência nova).
Falta só você preencher `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD`/
`SMTP_REMETENTE`/`RELATORIO_SEMANAL_DESTINATARIOS` no `.env` e colocar o
script para rodar via cron (exemplo de crontab no topo do próprio script).
Sem essas variáveis, o script gera o relatório e avisa que não vai enviar
— nunca falha silenciosamente. O mesmo SMTP também já é reaproveitado
pelo lembrete de compromisso da Agenda (ver seção -4 acima).

**Atualizado:** WhatsApp para o lembrete de compromisso da Agenda foi
implementado — você escolheu o caminho da **automação não-oficial** entre
as três opções que apresentei (API oficial paga da Meta / automação
não-oficial grátis mas arriscada / não usar por enquanto). Ver seção -4
acima para os detalhes técnicos completos e o passo a passo de ativação
(`whatsapp-bridge/`). Continua valendo o alerta: esse caminho viola os
Termos de Serviço do WhatsApp e corre risco real de o número usado ser
banido — se em algum momento quiser migrar para a API oficial da Meta
(mais caro, mas sem esse risco), me avise que eu troco o canal sem mexer
no resto do sistema (a chamada em `app/utils/whatsapp.py` fica isolada
disso).

### 2.3 Interoperabilidade com o For Legal e com o Data Lake do escritório
**Atualizado:** criei uma API de leitura autenticada por token
(`/api/v1/processos`, `/movimentacoes`, `/decisoes`, `/prazos`), com
suporte a sincronização incremental (`?desde=AAAA-MM-DDTHH:MM:SS`) —
exatamente o que a seção 12 pede ("API de leitura autenticada"). Sem
`DATALAKE_API_TOKEN` configurado no `.env`, a API responde 503 (nunca abre
os dados sem token). O que continua faltando é o formato exato que o
Data Lake do escritório espera receber — não temos essa documentação do
lado deles; hoje a API devolve um JSON genérico (um objeto por registro,
todos os campos do modelo), fácil de adaptar depois que você tiver isso.
Integração direta com o For Legal continua bloqueada (sem documentação de
API/autenticação deles).

### 2.4 Google OAuth / export para Drive e Sheets
**Por quê:** precisa de um projeto no Google Cloud com client ID/secret seus.

### 2.5 Criptografia em repouso do banco, backup diário, retenção
**Por quê:** é configuração de infraestrutura (MySQL/EasyPanel), não linha de
código no repositório. Vale confirmar direto no painel do EasyPanel.

---

## 3. Decisão sua antes de continuar

### 3.1 Divergência de arquitetura (o ponto já sinalizado no relatório anterior)

O briefing descreve a fase 1 como um sistema **de usuário único, somente
leitura, hospedado fora da infraestrutura do escritório**. O que existe é o
JusControl: multiunidade, de uso interno, com escrita normal. Não mudei isso
nesta rodada porque é uma decisão de produto, não uma pendência técnica — dá
pra errar caro se eu simplesmente escolher por conta própria. Duas saídas
possíveis:

- (a) O objetivo mudou de fato para um sistema interno multiunidade — e aí
  o que existe hoje já está no caminho certo, só falta o resto desta lista.
- (b) É necessário um segundo deploy, separado, com um único login, somente
  leitura, fora da infraestrutura do escritório, como a fase 1 pede.

Só depois de decidir isso faz sentido eu continuar (por exemplo, faria pouco
sentido eu implementar métricas e alertas em cima de uma base cuja premissa
ainda pode mudar).

---

## Como testar o que foi implementado

Este ambiente não tem acesso de rede ao seu MySQL, então a validação aqui foi
por checagem de sintaxe (`py_compile`, sem erro em nenhum arquivo) e
inicialização do app sem conectar a nenhum banco (50 rotas registram sem
erro, incluindo as novas). A lógica de negócio (motor de prazos, máquina de
estados, cofre) foi testada antes com um banco local temporário, que já foi
removido — não ficou testada contra o MySQL real do projeto.

Roteiro sugerido no seu ambiente:
1. Suba a versão nova (mesmo `.env`, nenhuma migração necessária).
2. Cadastre um processo por CNJ (`/governanca/processos/novo-por-cnj`) — deve
   vir marcado "não monitorável" (esperado, sem provedor configurado).
3. Na aba "Governança" do processo, registre uma movimentação com o texto
   igual a um dos atos sugeridos no campo (ex: "Citação / intimação para
   contestar") — deve aparecer um prazo novo automaticamente, calculado.
4. Tente marcar esse prazo como cumprido sem evidência — deve ser recusado.
   Feche com evidência (selecionando a própria movimentação) — deve fechar.
5. Cadastre uma senha de processo no cofre e veja se consegue visualizar.
6. Confira `/governanca/painel` e `/governanca/metricas` com dados reais.
