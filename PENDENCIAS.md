# Status das pendências do briefing (atualizado em 17/08/2026)

## -17. "Internal Server Error" ao gerar rascunho de petição com instrução longa — gunicorn matava o worker no meio da geração

**O que foi reportado:** gerar um rascunho de petição com uma instrução bem
detalhada (9 itens, pedido de fundamentação jurídica completa) resultou em
"Internal Server Error" depois de um tempo de espera. O log do servidor
mostrou `WORKER TIMEOUT` no meio da geração do modelo de IA local
(`llama_decode`, dentro de `app/utils/ia_local.py`), seguido do gunicorn
matando e reiniciando o worker à força.

**Causa:** o modelo de IA local roda por CPU (sem GPU, servidor de produção
atual sem RAM/recurso sobrando — ver comentário no Dockerfile). Gerar até
1400 tokens de resposta (limite do rascunho de petição, ver
`app/utils/analise_processo_ia.py`) a partir de uma instrução longa é lento
nesse tipo de servidor — pode passar dos 2 minutos tranquilamente. O
gunicorn, porém, estava configurado com `--timeout 120` (2 minutos): quando
uma requisição passa desse tempo sem responder, o gunicorn assume que o
worker travou e o MATA à força no meio do processamento — não é um erro no
código da geração em si, é o próprio servidor interrompendo um trabalho que
ainda estava em andamento normalmente, só que demorado.

**Correção:** `--timeout` subiu de 120 para 300 segundos no `Dockerfile`
(linha do `CMD` do gunicorn) — dá folga confortável pro pior caso (prompt
grande + resposta no limite de tokens) num servidor sem GPU. Também
adicionei um aviso na tela (trava o botão "Gerar análise" e mostra "pode
levar alguns minutos, não feche nem recarregue a página" assim que envia o
formulário) — antes disso, não tinha nenhum retorno visual durante a espera
e dava a impressão de que a tela tinha travado, o que provavelmente levava a
recarregar/tentar de novo e piorar a experiência.

**Trade-off consciente, documentado no próprio Dockerfile:** com só 2
workers do gunicorn (número já enxuto de propósito, por causa do consumo de
RAM do modelo — ver comentário logo acima no arquivo), enquanto um worker
está ocupado gerando uma resposta longa de IA, só sobra 1 worker livre pra
atender TODO o resto do sistema (outros usuários, outras telas) — por até 5
minutos no pior caso agora. Acho aceitável hoje, porque é uma
funcionalidade sob demanda (só quando alguém pede um resumo/rascunho), não
o fluxo principal do sistema. Se isso passar a incomodar na prática (mais
gente usando o Agente de IA ao mesmo tempo, ou lentidão notada em outras
telas durante uma geração), a solução correta não é aumentar o timeout de
novo — é tirar a geração do ciclo de requisição/resposta (rodar em segundo
plano, com o usuário sendo avisado quando terminar, parecido com o cron de
captura periódica que já existe) — registro aqui como próximo passo se o
uso crescer.

Arquivos alterados: `Dockerfile` (`--timeout 120` → `300`) e
`app/templates/processos/detalhe.html` (aviso + trava do botão ao enviar o
formulário do Agente de IA). Esta mudança não passa pela sincronização de
schema nem precisa reiniciar nada além do próprio redeploy do container —
como sempre, ao fazer o próximo deploy do `Dockerfile` atualizado, o
EasyPanel reconstrói a imagem e já sobe com o novo timeout.

## -16. Comarca vinha em branco em alguns processos (TJSP) sem nenhuma explicação — agora mostra o motivo

**O que foi reportado:** testando um processo do TJSP (0043162-08.2001.8.26.0100),
a busca automática encontrou 347 movimentações e preencheu Área do direito,
Tipo de ação, Instância, Vara/Tribunal e Data de distribuição — mas Comarca e
Valor da causa ficaram em branco, diferente de um teste anterior (processo do
TJMS) em que a Comarca tinha vindo preenchida.

**Diagnóstico:** eu não tenho acesso ao JSON de resposta real que o DataJud
devolveu pra esse processo específico (ambiente onde eu trabalho não alcança
a API do DataJud nem os logs do servidor em produção), então não dá pra
cravar a causa exata sem isso. O que dá pra afirmar com o código: a Comarca
depende de DOIS passos em sequência — (1) o DataJud precisa devolver
`orgaoJulgador.codigoMunicipioIBGE` na resposta, e (2) o sistema consulta a
API pública do IBGE com esse código pra descobrir o nome da cidade. Cada
tribunal/vara do país preenche os campos da própria resposta de um jeito
levemente diferente — nem todos preenchem esse código sempre —, e antes
dessa correção, se qualquer um dos dois passos falhasse, o campo ficava em
branco em silêncio, sem indicar qual dos dois foi. Já o Valor da causa é mais
simples: o campo `valorCausa` frequentemente não vem na resposta do DataJud
mesmo (é opcional/tribunal-dependente) — não tem outro dado equivalente pra
tentar de novo, então quando falta, falta mesmo, sem jeito de "descobrir".

**O que foi corrigido:** a pré-visualização (tanto em "Novo processo"/"Editar
processo" quanto ao salvar) agora MOSTRA o motivo específico embaixo do
campo Comarca quando ele fica em branco, em vez de só deixar vazio sem
explicação: "o DataJud não informou o código do município" (passo 1 falhou —
provavelmente esse tribunal/registro específico não preenche esse dado) ou
"o DataJud indicou o código X, mas não consegui confirmar o nome agora"
(passo 2 falhou — vale tentar de novo, pode ter sido instabilidade pontual
da API do IBGE). Também adicionei uma notinha fixa explicando que "Valor da
causa" nem sempre vem do DataJud (não é bug, é limitação de dado na fonte) e
uma nota em "Advogado da parte contrária" avisando que o DataJud também não
expõe isso (mesma restrição de LGPD que já existia só na Parte contrária).

Se depois de testar mais processos a Comarca continuar vindo em branco com o
aviso "não informou o código do município" (passo 1), é sinal de que aquele
tribunal específico realmente não preenche esse dado no DataJud — nesse caso
não tem solução automática (o dado simplesmente não existe na fonte pública).
Se aparecer com frequência o aviso "não consegui confirmar o nome agora"
(passo 2), me avise — pode ser algo na consulta ao IBGE que vale eu olhar
com mais calma.

Arquivos alterados: `app/utils/conector_datajud.py` (guarda o código IBGE
cru mesmo quando não resolve o nome), `app/routes/governanca.py` (monta o
aviso na pré-visualização) e `app/templates/processos/form.html` (mostra o
aviso, e as duas notas fixas novas). Testado no sandbox local simulando os
três cenários (sem código nenhum / código presente mas IBGE fora do ar /
tudo funcionando) — cada um devolve o aviso certo.

## -15. Resumo do Agente de IA repetia a lista de prazos duas vezes e cortava no meio da frase — corrigido nesta rodada

**O que foi reportado:** no "Resumo dos autos" de um processo real (execução
fiscal aberta em 2002), a resposta trazia a lista de "Prazos pendentes"
completa dentro da seção SITUAÇÃO ATUAL, e depois a MESMA lista de novo na
seção PRAZOS PENDENTES — e ainda cortava no meio de uma frase.

**Causa:** duas coisas se somaram. (1) O contexto real injetado no prompt
(`montar_digest_processo`, em `app/utils/analise_processo_ia.py`) já trazia
um bloco rotulado "Prazos pendentes:" com a lista — como o system prompt
pede uma seção com o título quase idêntico ("PRAZOS PENDENTES"), o modelo
local (pequeno, roda em janela de contexto de só 4096 tokens — ver
`app/utils/ia_local.py`) tendia a "copiar de volta" esse bloco em mais de
um lugar da resposta, em vez de resumir. (2) O limite de tamanho da
resposta (`max_tokens=700`) era baixo demais pra caber as 4 seções pedidas
sem cortar no meio — havia folga de sobra na janela de contexto pra uma
resposta maior (prompt + digest ficam bem abaixo de 4096 tokens), o limite
de 700 era só um número escolhido sem testar com um caso de processo com
vários prazos.

**O que foi corrigido:** (1) o rótulo do bloco de prazos no digest foi
trocado por um que não colide com o título de seção pedido, com uma
instrução explícita dentro do próprio rótulo pra não repetir a lista fora
da seção própria; (2) o system prompt (`RESUMO_SYSTEM`) ganhou uma regra
explícita — cada informação só pode aparecer em UMA seção, SITUAÇÃO ATUAL
deve ser um parágrafo curto sem listas, a lista completa de prazos vai só
na seção PRAZOS PENDENTES; (3) `max_tokens` do resumo subiu de 700 para
1100 (ainda com folga segura dentro da janela de 4096 tokens do modelo
local). Isso também melhora indiretamente o rascunho de petição, que usa o
mesmo digest como base factual (ver pendência nº -14 abaixo — a lista de
prazos ficou bem mais enxuta depois daquela correção, o que por si só já
reduz o material repetitivo que o modelo tinha pra "copiar").

Não dá pra garantir 100% que um modelo local pequeno nunca mais vai repetir
nada — é uma limitação de tamanho do modelo, documentada desde o início em
`analise_processo_ia.py` — mas as duas causas concretas identificadas neste
caso real foram corrigidas. Se o escritório sentir que a qualidade ainda
incomoda na prática, o caminho é o modelo local maior (Qwen3-4B, já
preparado mas desligado por padrão — ver pendência de infraestrutura na
seção -6 mais abaixo) ou a API do Claude com chave própria (BYOK, já
suportada em "Minhas Integrações").

Arquivo alterado: `app/utils/analise_processo_ia.py`. Testado no sandbox
local checando o texto do digest gerado (rótulo não colide mais com o
título de seção).

## -14. Captura inicial de processo antigo gerava dezenas de "prazos" fantasmas de histórico + mapeamento de estado/próxima ação agora também casa por texto — implementado nesta rodada

**O que foi reportado:** depois de capturar pela primeira vez todo o
histórico de um processo real de 2002 (execução fiscal, ~10 movimentações
capturadas de uma vez, todas com o mesmo código TPU 11383 / texto "Ato
ordinatório"), a tela de Governança mostrava TODAS como "triagem pendente"
e a aba Prazos mostrava TODAS gerando o mesmo prazo genérico "Análise
necessária — ato sem regra de próxima ação cadastrada", com vencimentos já
vencidos há anos (25/08/2002, 11/08/2003, 06/08/2012, 08/06/2014 — inclusive
6 entradas praticamente iguais na mesma data). A pergunta foi se dava pra
"trazer dados mais reais".

**Causa raiz nº 1 (bug de verdade, não só falta de cadastro):** o motor de
próxima ação (`aplicar_regra_proxima_acao`, em `app/utils/prazos_engine.py`)
sempre foi pensado pra um ato NOVO chegando hoje — sem regra cadastrada,
cria uma tarefa de análise com prazo provisório de 5 dias a partir da data
do ato, pra nunca deixar um ato passar em silêncio (exigência do briefing).
Isso faz todo sentido em captura periódica (um ato realmente novo, de
verdade sem regra). Mas na captura INICIAL de um processo antigo, o DataJud
devolve o histórico inteiro de uma vez — e cada uma dessas dezenas de
movimentações antigas, sem regra cadastrada, virava um "prazo" pendente com
vencimento (data do ato + 5 dias) plantado lá atrás no passado. Um ato de
2002 obviamente não tem uma "tarefa pendente hoje" real — o próprio processo
já seguiu adiante depois dele — mas a tela de Prazos mostrava como se
tivesse, inundando a lista de alarmes falsos e "envelhecendo" o digest que
alimenta o Agente de IA (pendência nº -15 acima).

**Correção:** `registrar_movimentacoes_capturadas` (`app/utils/
captura_pipeline.py`) ganhou um parâmetro `captura_inicial` — quando `True`
(cadastro por CNJ, cadastro manual com busca automática, botão "Tentar
captura automática" — os três lugares que fazem a PRIMEIRA carga completa
de histórico de um processo), só a movimentação mais RECENTE do lote pode
gerar o prazo genérico de "análise necessária" quando não há regra
cadastrada; as mais antigas do mesmo lote não geram mais esse prazo
fantasma. Nada fica escondido: toda movimentação continua 100% visível na
aba Governança com o badge "triagem pendente", só deixa de virar uma tarefa
de prazo isolada. Captura periódica (`capturar_movimentacoes.py`, rodando
via cron) continua com o comportamento de sempre — todo ato novo de
verdade gera seu prazo genérico normalmente, sem essa restrição.

**Causa raiz nº 2 (limite de configuração, agora com uma saída melhor):**
"Ato ordinatório" (código TPU 11383) é, na prática, um código "guarda-chuva"
que vários tribunais usam pra QUALQUER expediente de mero impulso
processual — intimação, remessa dos autos, juntada de petição podem cair
todos no mesmo código. Cadastrar "11383 → um estado só" no Mapa de Estado
(tela Governança > Mapa de estado) mapearia tudo errado, porque o código
sozinho não diz o que realmente aconteceu. Agora `MapaEstadoTPU` (e a
mesma lógica que já existia em `RegraProximaAcao`) aceita mapear também
pelo TEXTO real do ato (`texto_contido`, um trecho a procurar no texto
integral, sem diferenciar maiúsculas/minúsculas) — inclusive SEM nenhum
código associado, pra poder cadastrar várias regras de texto diferentes
para o mesmo código genérico. Isso exigiu tornar `MapaEstadoTPU.codigo_tpu`
opcional (era obrigatório) — única alteração de coluna já existente feita
nesta rodada; `sincronizar_schema.py` foi ajustado para aplicar esse tipo
específico de mudança automaticamente (só AFROUXAR uma restrição existente,
nunca apertar — o que nunca corrompe/perde dado já cadastrado, ver
comentário no topo do próprio script).

**Atalho novo pra reduzir o trabalho manual de configurar isso:** a lista
de movimentações (aba Governança, na tela do processo) agora tem um link
"mapear agora" em cada linha com triagem pendente, que já abre o formulário
de novo mapeamento com o código e o começo do texto do ato preenchidos —
não precisa mais redigitar. O mesmo em Prazos: toda linha com o prazo
genérico tem um link "cadastrar regra para ..." que abre o formulário de
nova regra de próxima ação já com o texto do ato preenchido.

Isso continua sendo, no fundo, trabalho de CONFIGURAÇÃO que só o escritório
pode fazer com segurança (qual é o estado de negócio certo, qual o prazo
legal certo pra cada tipo de ato — errar aqui é grave, por isso o sistema
nunca sugere um valor sozinho) — o que mudou é: (1) o histórico antigo não
gera mais alarme falso de prazo vencido há anos, e (2) cadastrar as regras
de verdade ficou mais rápido e possível até pra um código tão genérico
quanto "Ato ordinatório".

Arquivos alterados: `app/models/estado_processual.py` (novo campo
`texto_contido`, `codigo_tpu` passou a opcional), `app/utils/
estado_processual_engine.py` (fallback por texto), `app/utils/
prazos_engine.py` (parâmetro `permitir_generico`), `app/utils/
captura_pipeline.py` (parâmetro `captura_inicial` + lógica do "só o mais
recente"), `app/routes/processos.py` e `app/routes/governanca.py` (os três
pontos de captura inicial passam `captura_inicial=True`; rotas de
mapeamento/regra aceitam prefill via querystring e validam "pelo menos
código ou texto"), `app/templates/governanca/mapa_estado_form.html`,
`app/templates/governanca/mapa_estado_lista.html`, `app/templates/
governanca/regra_proxima_acao_form.html` e `app/templates/processos/
detalhe.html` (links "mapear agora"/"cadastrar regra"), e
`sincronizar_schema.py` (nova etapa de afrouxamento de coluna). Testado no
sandbox local: captura inicial de um lote com 6 movimentações antigas do
mesmo código sem regra gerou só 1 prazo genérico (o mais recente), as 6
continuam visíveis com triagem pendente; captura periódica continua
gerando 1 prazo por movimentação nova, sem a restrição; mapeamento por
`texto_contido` resolvendo um código sem mapa exato; duas linhas de
`MapaEstadoTPU` com `codigo_tpu` nulo coexistindo sem violar a restrição de
único; telas de mapeamento/regra renderizando com o prefill e recusando
cadastro sem código nem texto — tudo via Flask test_client + SQLite
descartável.

## -13. Descrição/objeto e "Segredo de justiça" também autopreenchidos com o que sobrava do DataJud — implementado nesta rodada

**O que foi pedido:** depois dos campos da pendência nº -12, a pergunta foi
se dava pra verificar se tinha mais algum dado do processo que o DataJud
devolve mas não tem campo próprio no cadastro, e colocar isso na
"Descrição/objeto".

**O que eu encontrei que estava sendo capturado mas não ia pra lugar
nenhum**, olhando de novo a resposta real do DataJud:
- Quando o processo tem **mais de um assunto CNJ** (o campo "Área do
  direito" só guarda um texto corrido, então processo com 2+ assuntos
  perdia os outros).
- Se o processo é **eletrônico ou físico**, e por qual **sistema** (PJe,
  e-Proc, etc.) — informação que a resposta do DataJud traz mas o cadastro
  não tinha onde colocar.
- O campo **nível de sigilo** que o próprio DataJud sinaliza — isso é
  informação de verdade, não só complementar.

**O que foi feito:** quando algum desses três aparece, o sistema monta uma
notinha de texto (ex.: *"Dados do DataJud (captura automática): Assuntos
(CNJ): Dívida Ativa (Execução Fiscal); IPTU. Processo eletrônico, sistema
Pje."*) e preenche sozinho o campo **Descrição/objeto** — só quando esse
campo ainda está vazio, nunca sobrescreve o que foi escrito à mão. Quando
não há nada de extra pra mostrar (processo com 1 assunto só, sem essas
informações), não escreve nada — não cria descrição vazia ou genérica.

Além disso, quando o DataJud sinaliza nível de sigilo diferente de zero, o
sistema **marca sozinho a caixinha "Segredo de justiça"** (só liga, nunca
desliga uma marcação que você já tinha mudado) — tanto na pré-visualização
(dá pra desmarcar antes de salvar, com um aviso explicando por que foi
marcado) quanto ao salvar de fato.

Arquivos alterados: `app/utils/conector_datajud.py` (extrai `sistema`,
`formato`, `nivelSigilo` e a lista completa de assuntos da resposta),
`app/utils/captura_pipeline.py` (nova função `montar_nota_datajud` +
`aplicar_carga_inicial` passou a preencher `descricao` e marcar
`segredo_justica`), `app/routes/governanca.py` (preview devolve
`descricao_sugerida`/`sigilo_sugerido`) e os templates
`processos/form.html` e `governanca/novo_por_cnj.html` (JS preenche a
Descrição e marca a caixa de sigilo). Testado no sandbox local: nota
montada corretamente com múltiplos assuntos + sistema + sigilo, preview
devolvendo os dois campos novos, cadastro salvando a descrição e marcando
sigilo automaticamente mesmo sem o usuário marcar a caixa, e confirmando
que uma descrição já escrita pelo usuário nunca é sobrescrita (o sigilo
continua sendo marcado normalmente, por ser um campo separado).

## -12. Mais campos autopreenchidos a partir do DataJud (Tipo de ação, Instância, Comarca) — implementado nesta rodada

**O que foi pedido:** depois da busca automática já funcionando (pendências
nº -9 a -11), ainda sobravam vários campos vazios no cadastro — Tipo de
ação, Fase, Instância, Comarca, Parte contrária, Advogado da parte
contrária, Valor da causa, Data de distribuição — e a pergunta foi se dava
pra preencher mais alguns automaticamente também.

**O que passou a ser preenchido sozinho nesta rodada** (tanto na
pré-visualização ao apertar Enter quanto ao salvar de fato):
- **Tipo de ação** — a partir da "classe" que o DataJud devolve (ex.:
  "Execução Fiscal", "Procedimento Comum Cível"). O mesmo valor também
  continua sendo guardado à parte em `classe_processual` (campo interno já
  existente, sem campo próprio no formulário, usado em relatórios).
- **Instância** — a partir do campo "grau" que o DataJud devolve (G1/G2/G3),
  traduzido pra "1º grau"/"2º grau"/"3º grau". Nem todo processo tem esse
  campo preenchido pelo tribunal; quando não tem, fica em branco pra
  preencher à mão (não inventa).
- **Comarca** — o DataJud não devolve o nome da comarca diretamente, mas às
  vezes devolve o código IBGE do município dentro do órgão julgador; quando
  isso acontece, o sistema consulta a API pública e gratuita do IBGE (sem
  chave, sem custo) pra descobrir o nome da cidade (ex.: "Campo Grande -
  MS") e preencher sozinho. Se o tribunal não mandar esse código, ou a
  consulta ao IBGE falhar por qualquer motivo, o campo simplesmente fica em
  branco — nunca trava o resto do cadastro por causa disso.

**O que CONTINUA precisando ser preenchido à mão, e por quê** (a tela agora
mostra um aviso pequeno embaixo de cada um explicando):
- **Fase** (Conhecimento, Execução...) — o DataJud não tem esse conceito na
  resposta; dava pra tentar adivinhar a partir do texto da última
  movimentação, mas isso seria chute, não dado — preferi não inventar.
- **Parte contrária** e **Advogado da parte contrária** — a API pública do
  DataJud não expõe nome de partes nem de advogados (dado protegido por
  LGPD, nem chega a vir na resposta) — isso é o tipo de busca que só um
  provedor pago (Judit/Escavador/Digesto/Codilo) ofereceria, e mesmo assim
  não com certeza pra todo tribunal.
- **Valor da causa** e **Data de distribuição** — o sistema já tenta buscar
  os dois (mecanismo que já existia), mas nem todo tribunal preenche esses
  campos na base do DataJud — quando o tribunal não manda o dado, não tem
  como inventar um valor.
- **Responsável** e **Cliente** — são informações internas do seu
  escritório (quem é o advogado responsável, quem é o cliente no seu
  cadastro) — nenhuma fonte externa saberia disso, é sempre você quem
  escolhe.

Arquivos novos/alterados: `app/utils/ibge.py` (novo — consulta à API
pública do IBGE, com cache simples e nunca propaga erro),
`app/utils/conector_datajud.py` (extrai `grau` e o código IBGE do
município da resposta do DataJud; também corrigido pra não quebrar num
formato alternativo de "assuntos" — lista aninhada — documentado num
exemplo real da API), `app/utils/captura_pipeline.py` (`aplicar_carga_inicial`
passou a preencher também `tipo_acao`, `instancia` e `comarca`, sempre só
quando o campo já não tinha valor), `app/routes/governanca.py` (preview
devolve `instancia`/`comarca`) e `app/templates/processos/form.html`
(campos com `id` pra JS conseguir preencher, avisos nos campos que não têm
como vir automáticos). Testado no sandbox local simulando uma resposta real
do DataJud (com `grau` e `codigoMunicipioIBGE`, formato do exemplo oficial
documentado) e a API do IBGE respondendo o nome do município — confirmando
que Tipo de ação, Instância e Comarca são preenchidos tanto na
pré-visualização quanto no cadastro salvo; testado também que uma falha de
rede na consulta ao IBGE não trava o resto da captura (só deixa a Comarca
em branco).

## -11. Dígito verificador do CNJ deixou de travar a busca automática — corrigido nesta rodada

**O que aconteceu:** depois da correção da pendência nº -10 (abaixo), você
testou de novo com o número `0025567-55.2002.8.12.0001` — tanto em "Novo
processo" quanto em "Cadastrar por CNJ" — e as duas telas mostraram
"Número CNJ inválido: Dígito verificador inválido (informado 55, esperado
47)", em vez de encontrar o processo. Isso confirmava o achado da pendência
nº -9: matematicamente, esse número não bate com a fórmula oficial do CNJ
(módulo 97). Só que você continuava esperando que esse número específico
funcionasse — ou seja, pra você esse é o número certo do processo.

**O que eu tinha errado:** eu estava tratando o dígito verificador como um
portão travado — se não batesse com a fórmula, nem tentava buscar no
DataJud. Mas quem decide se um processo existe de verdade é o próprio
DataJud (o índice oficial do CNJ), não esse cálculo por aqui. Processos
mais antigos (esse é de 2002, bem antes da unificação de numeração pela
Resolução CNJ 65/2008 valer pra todos os tribunais) às vezes têm, no
próprio tribunal e no DataJud, um número registrado que não fecha pela
fórmula atual — e mesmo assim é o número real e é assim que está indexado.
Bloquear a busca por causa disso era eu impedindo a busca de algo que
podia estar certo.

**Corrigido nesta rodada:** o dígito verificador que não bate **não trava
mais** a busca em nenhuma das telas (Novo processo, Editar processo,
Cadastrar por CNJ, botão "Tentar captura automática") — o sistema busca no
DataJud com o número exatamente como foi digitado, dígito por dígito, sem
"corrigir" nada. Só mostra um aviso (amarelo, não mais vermelho travando
tudo): "Dígito verificador não confere pelo cálculo oficial... pode ser
numeração legada. Buscando mesmo assim." Se o DataJud encontrar o processo
com esse número, tudo funciona normalmente (preenche os campos, ativa
monitoramento automático) — o aviso fica só como uma nota de atenção. Um
número realmente com formato errado (menos de 20 dígitos, por exemplo)
continua barrado de cara, sem tentar — essa parte não mudou. A importação
em lote (CSV) também continua exigindo o dígito verificador correto, de
propósito: numa carga de muitas linhas sem revisão individual, é melhor
sinalizar a linha como erro do que gastar uma chamada de API por uma
possível linha digitada errada.

Arquivos alterados: `app/utils/cnj.py` (`validar_numero_cnj` ganhou o
parâmetro `exigir_dv=False`, usado nos fluxos que buscam no DataJud de
verdade), `app/utils/conector_datajud.py`, `app/routes/governanca.py`,
`app/routes/processos.py` e os templates `governanca/novo_por_cnj.html` e
`processos/form.html` (mostram o aviso em amarelo quando aplicável).
Testado no sandbox local simulando o DataJud com o número exatamente como
você digitou (dígito verificador "errado") indexado no tribunal —
confirmando que agora É encontrado, ativa monitoramento automático, e o
aviso aparece na tela; testado também que número com formato realmente
errado continua barrado sem tentar, e que um número com dígito que não
bate mas que o DataJud genuinamente não encontra vira "não monitorável"
com o motivo explicado (não mais um bloqueio silencioso).

## -10. "Novo processo" (tela manual) não buscava nada sozinho ao apertar Enter no CNJ — corrigido nesta rodada

**O problema relatado (com print):** na tela normal de cadastro de processo
("Novo processo" — os campos Nº do processo, Cliente, Área do direito, etc.,
não a tela separada "Cadastrar por CNJ"), digitar o número CNJ e apertar Enter
não buscava nem preenchia nada.

**Causa:** essa tela (`app/templates/processos/form.html`) nunca teve esse
recurso implementado — só a tela "Cadastrar por CNJ" (usada pela pendência
nº -9 abaixo) tinha a busca automática. Além disso, mesmo o cadastro em si
(ao clicar "Salvar processo") nunca chamava o DataJud nessa tela — o
processo ficava marcado como "monitorável"/"automático" por padrão do banco,
mas sem ter buscado nada de fato, dependendo só da rotina periódica (que roda
1x por dia, se estiver configurada, e nunca desmarca sozinha um processo que
falhou, só registra no Log de Captura) pra eventualmente tentar.

**Corrigido nesta rodada:**
- Digitar o CNJ e apertar Enter nessa tela agora busca no DataJud (mesma
  pré-visualização de "Cadastrar por CNJ") e preenche sozinho os campos que
  estiverem vazios: Área do direito, Vara/Tribunal, Valor da causa, Data de
  distribuição, e Tribunal (DataJud) — só pra conferência, nada é salvo até
  clicar em "Salvar processo".
- Ao **salvar** (tanto em "Novo processo" quanto ao editar um processo já
  existente, se o número for alterado), o sistema agora tenta a captura
  automática de verdade nesse momento — reaproveitando o mesmo motor de
  "Cadastrar por CNJ"/"Tentar captura automática" (busca por tentativa em
  todos os tribunais do segmento, ver pendência nº -9). Se achar, já grava
  classe/assunto/vara/data/valor e as movimentações, e marca
  monitorável/automático de verdade (não mais um "automático" de fachada sem
  ter buscado nada). Se não achar (número inválido, chave não configurada,
  processo não indexado), marca como "manual" (sem número) ou
  "não monitorável" com o motivo explicado — nunca mais um "automático"
  silencioso que na real nunca rodou.
- Editar um processo sem mudar o número não dispara nova busca (só quando o
  número muda de verdade) — pra não sair rebuscando/reclassificando o
  acompanhamento toda vez que alguém corrige só a Fase ou a Descrição, por
  exemplo.

Arquivos alterados: `app/routes/processos.py` (nova função
`_tentar_captura_automatica_no_cadastro`, usada em `novo()` e `editar()`),
`app/templates/processos/form.html` (JS de Enter-pra-buscar-e-preencher) e
`app/routes/governanca.py` (endpoint de pré-visualização passou a devolver
também a data em formato ISO, pro campo `<input type="date">` conseguir se
autopreencher). Testado no sandbox local (Flask test_client + DataJud
simulado) cobrindo: cadastro com CNJ válido e encontrado (captura tudo e já
salva monitorável/automático), cadastro sem número (vira "manual", zero
chamada de rede), cadastro com CNJ de dígito verificador inválido (vira
"manual" com o motivo explicado, zero chamada de rede), edição corrigindo um
número errado (dispara nova busca e encontra), edição sem mudar o número
(não dispara busca nenhuma), e as telas de cadastro/edição renderizando sem
erro com o JS novo.

**Isso também resolve, de um jeito mais direto, o mesmo achado da pendência
nº -9 abaixo sobre o processo #1**: o número `0025567-55.2002.8.12.0001` tem
dígito verificador inválido (o correto seria `47`, não `55`) — com a correção
desta rodada, digitar esse número errado nessa tela agora mostra a mensagem
"Número CNJ inválido" tanto no preview (Enter) quanto ao salvar, em vez de
simplesmente não fazer nada. Ainda assim, vale corrigir o número certo do
processo #1 (ver detalhes na pendência nº -9) pra a busca automática
funcionar nele.

## -9. Captura automática do DataJud não rodava sozinha em processo Estadual/Federal, e não tinha como tentar de novo — corrigido nesta rodada (+ achado importante sobre o número do processo #1)

**O problema relatado:** ao cadastrar o processo `0025567-55.2002.8.12.0001` pela
tela "Cadastrar por CNJ" e apertar Enter, nada preenchia sozinho — continuava
precisando digitar tudo manualmente.

**Causa nº 1 (já corrigida): tribunal não identificado.** Esse processo é da
Justiça Estadual (o "8" logo depois do ano, no número CNJ, é o código do
segmento de justiça). O sistema só conseguia descobrir o tribunal sozinho, a
partir do próprio número, para processos da Justiça do Trabalho (TRT) — para
Estadual/Federal/Superiores era preciso escolher manualmente no campo
"Tribunal (DataJud)" antes de apertar Enter, e esse aviso era só um texto
pequeno, fácil de passar batido.

**Corrigido nesta rodada:** em vez de exigir a escolha manual (ou tentar
adivinhar por uma tabela de código-de-tribunal, que é arriscado — ver
`app/utils/tribunais_datajud.py`), o sistema agora **testa automaticamente
cada tribunal candidato do segmento contra a API real do DataJud** até achar
o processo — para Estadual são até 27 tentativas (uma por TJ), bem dentro do
limite de 120 requisições/minuto que a própria API pública documenta. Escolher
o tribunal manualmente continua funcionando e deixa a busca mais rápida (1
chamada só), mas não é mais obrigatório — nem no cadastro por CNJ nem no botão
"Tentar captura automática" (ver abaixo). Segmentos sem nenhum tribunal
cadastrado ainda no catálogo (Eleitoral, Justiça Militar Estadual) continuam
precisando de seleção manual — mas para esses hoje não existe nem opção
correta pra escolher no campo, então a mensagem de erro já avisa disso.

**Causa nº 2 (achado nesta rodada, ainda pendente de confirmar com você): o
número do processo #1 tem o dígito verificador inválido.** Testei o número
exatamente como está cadastrado — `0025567-55.2002.8.12.0001` — pela fórmula
oficial de validação do CNJ (módulo 97, a mesma que qualquer sistema usa pra
conferir se um número de processo é válido) e ele **não passa**: o dígito
verificador certo pra esse sequencial/ano/tribunal/origem seria `47`, não
`55`. Ou seja, mesmo com o tribunal certo identificado (o que a correção acima
já resolve), a busca automática nunca vai encontrar esse processo especificamente,
porque o número em si está com um dígito trocado — não é algo que a busca por
tribunal resolva.

Isso não é bug do sistema: o campo "Nº do processo" no cadastro manual
("Novo processo") nunca validou o dígito verificador de propósito (processos
antigos/legados às vezes têm numeração fora do padrão atual, e bloquear
cadastro por causa disso seria pior do que aceitar). Já a tela "Cadastrar por
CNJ" valida sim — o que sugere que o processo #1 foi cadastrado pelo formulário
manual, não por ali. **Recomendo conferir o número desse processo direto no
site do TJMS ou no processo físico/PJe** — se todos os outros dígitos
estiverem certos, o valor correto provavelmente é `0025567-47.2002.8.12.0001`
(troca só os 2 dígitos depois do sequencial); aí é só corrigir no cadastro
("Editar" no processo) e tentar a captura automática de novo.

**O que também foi corrigido nesta rodada (independente do achado acima):**
antes, se a primeira tentativa de captura falhasse (tribunal não escolhido,
chave do DataJud configurada só depois, erro temporário de rede, etc.), não
existia nenhuma forma de tentar de novo pra um processo que já tinha sido
cadastrado — o processo ficava "não monitorável" para sempre, a não ser que
fosse excluído e recadastrado do zero. Agora, na tela de detalhe de qualquer
processo marcado como "não monitorável" que já tenha um número de processo
cadastrado, aparece um botão **"Tentar captura automática"**, com um campo
opcional pra escolher o tribunal (pré-preenchido se já tiver um salvo). Ele
reaproveita exatamente o mesmo motor de busca e de gravação usado no cadastro
por CNJ (`aplicar_carga_inicial` + `registrar_movimentacoes_capturadas`) — que
só preenche campo que ainda está vazio e nunca duplica movimentação repetida —
então pode ser clicado quantas vezes for preciso, sem risco de sobrescrever
algo digitado à mão ou duplicar andamento. Se der certo, o processo passa a
"monitoramento automático" e entra na rotina periódica normalmente; se der
errado, mostra o motivo (processo não encontrado no DataJud, DataJud fora do
ar, número com dígito inválido, etc.) e fica registrado em Log de Captura
(mesma aba "Governança" que já existia).

**Sobre a pergunta "o Agente de IA consegue buscar isso sozinho?": não.** O
Agente de IA do sistema (local ou Claude via chave própria, em "Minhas
Integrações") não tem acesso à internet nem a nenhuma ferramenta de busca
conectada — ele só analisa texto que já está no banco (resumo de andamentos,
rascunho de petição). Ele não consulta o DataJud nem nenhuma API externa, e
usar um modelo de IA pra "adivinhar" o tribunal certo a partir do número seria
exatamente o tipo de risco que a solução acima evita (chute com confiança,
buscando o processo errado sem avisar) — por isso a busca automática por
tribunal foi resolvida testando a API real (determinístico, sem IA, sem
chute), não conectando o Agente de IA a essa tarefa.

Arquivos alterados: `app/utils/tribunais_datajud.py` (lista de tribunais
candidatos por segmento), `app/utils/conector_datajud.py` (busca por
tentativa em vez de exigir tribunal), `app/routes/governanca.py` (rota nova
`governanca.tentar_captura`), `app/routes/processos.py` (passa a lista de
tribunais pro template de detalhe), `app/templates/processos/detalhe.html`
(botão novo) e `app/templates/governanca/novo_por_cnj.html` (texto de ajuda
atualizado). Testado no sandbox local (Flask test_client + respostas do
DataJud simuladas no nível HTTP) cobrindo: busca automática que acha o
processo testando vários tribunais até acertar (e para assim que acha, sem
gastar as tentativas restantes), erro real (ex.: chave inválida) interrompendo
na 1ª tentativa sem tentar os demais, esgotar todos os candidatos sem achar,
Justiça do Trabalho continuando direta (sem tentativa múltipla), segmento sem
tribunal no catálogo (Eleitoral) recusando sem nenhuma chamada de rede, e
tribunal escolhido manualmente pulando direto pra 1 chamada só — além dos
mesmos testes de ponta a ponta da rodada anterior (botão "Tentar captura
automática", preenchimento de campos vazios, log de captura, processo sem
número de CNJ).

**Se o botão "Tentar captura automática" continuar sem funcionar mesmo depois
de corrigir o número**, o próximo suspeito é a variável `DATAJUD_API_KEY` não
estar configurada no servidor (ou a chave própria da empresa, se estiver
usando BYOK, em "Minhas Integrações") — vale conferir isso no ambiente do
EasyPanel.

## -8. Lembretes de compromisso: mensagem completa, WhatsApp pro responsável, e-mail pro cliente, e cada empresa com o PRÓPRIO número de WhatsApp — implementado nesta rodada

Três pedidos em sequência, todos em `enviar_lembretes_compromissos.py` e
na integração de WhatsApp:

1. **Mensagem completa** — a mensagem enviada (in-app, e-mail e
   WhatsApp) só trazia o nome do compromisso. Agora traz nome, descrição
   completa (quando cadastrada) e data/hora, cada um em sua própria linha.
2. **WhatsApp também pro responsável, e-mail também pro cliente** —
   antes, o lembrete por WhatsApp só ia pro cliente, e o e-mail só pro
   responsável (usuário do escritório). Agora: o responsável também recebe
   o lembrete por WhatsApp (no número cadastrado no próprio perfil dele,
   Equipe → editar usuário → campo WhatsApp — campo que já existia no
   sistema, só não estava sendo usado ainda), e o cliente também recebe
   por e-mail (quando tiver e-mail cadastrado), além do WhatsApp.
3. **Um número de WhatsApp por empresa, não um só compartilhado** — você
   percebeu que, com todas as empresas clientes usando o mesmo número
   conectado no WAHA, os clientes de empresas diferentes recebem mensagem
   de um número que não é o "deles", e ninguém consegue responder dúvida
   por ali (o número é seu, não da empresa cliente). Antes de implementar,
   perguntei se "evolution" era uma ferramenta diferente do WAHA (não era,
   só um jeito de falar) e como cada empresa deveria conectar o próprio
   número — você escolheu: cada empresa escaneia o próprio QR code no
   MESMO servidor WAHA que você já hospeda (não precisa de servidor
   próprio por empresa). Confirmei antes de construir: desde a versão
   2026.6.1 o WAHA Core (grátis) suporta sessões ilimitadas — não tem
   custo extra de licença por empresa conectada.

   O que mudou: nova seção **"WhatsApp dos lembretes"** em "Minhas
   Integrações" (mesma tela do BYOK do Claude/DataJud, seção -7 abaixo).
   A empresa clica "Conectar WhatsApp", aparece um QR code (a página
   atualiza sozinha enquanto espera o escaneamento), escaneia com o
   WhatsApp que vai usar, e pronto — aquele número passa a ser o dela,
   isolado de todas as outras empresas. Pode desconectar a qualquer
   momento pelo botão "Desconectar".

   ⚠️ **Isso muda o comportamento de quem já usava o WhatsApp
   compartilhado**: depois deste deploy, NENHUMA empresa cliente volta a
   usar o número antigo automaticamente — cada uma precisa entrar em
   "Minhas Integrações" e conectar o próprio número, ou os lembretes por
   WhatsApp dela ficam pausados (os outros canais — notificação e e-mail —
   continuam normais). Isso foi proposital (é exatamente o problema que
   você queria resolver, um "meio-termo" que continuasse usando o número
   antigo como reserva reproduziria o mesmo problema), mas avise as
   empresas clientes que já usavam WhatsApp antes de fazer o deploy, senão
   os lembretes delas somem sem aviso. A própria plataforma (sua conta)
   continua na sessão "default" de sempre, sem precisar reconectar nada.

   Testado no sandbox local simulando as respostas do WAHA (criar sessão,
   pegar QR, virar "conectada", desconectar) e confirmando isolamento
   entre duas empresas diferentes (uma com número conectado, outra sem —
   a sem número nunca recebe/envia nada, nunca "vaza" pro número de
   outra). **Não consegui testar uma chamada real contra um servidor WAHA
   de verdade** (sem acesso de rede a partir daqui) — teste o fluxo
   completo (conectar uma empresa de teste, escanear o QR de verdade,
   confirmar que a mensagem sai do número certo) depois do deploy.

## -7. Cada empresa pode escolher usar a própria chave de API do Claude e/ou do DataJud (BYOK) — implementado nesta rodada

A pedido explícito ("coloque a opção do cliente escolher usar o nosso
agente local ou inserir uma chave API do claude [...] quero que ocorra o
mesmo com o DataJud"). Antes de implementar, chequei os Termos Comerciais
da Anthropic: cobrar um markup sobre o uso da API de clientes usando uma
chave só da JusControl por trás ("revenda"/repasse com lucro embutido) é
proibido sem acordo de revenda expresso com a Anthropic (Seção D.4 proíbe
revenda dos Serviços, e os termos também proíbem usar autenticação por
assinatura para dar acesso à API a terceiros). Por isso o que foi
construído é o modelo permitido: **BYOK — cada empresa cadastra e é
cobrada diretamente pela própria chave**, e a JusControl não vê, processa
ou intermedeia esse consumo/pagamento (nenhuma cobrança de markup foi
implementada — se você quiser monetizar isso no futuro, o caminho
compatível é uma taxa fixa de desbloqueio do recurso, independente de
uso, reaproveitando o Mercado Pago/Licenca que já existe; não construí
isso ainda, é uma decisão separada).

### O que existe agora
Nova tela **"Minhas Integrações"** (menu do admin de cada empresa cliente,
`/minhas-integracoes`), com duas escolhas independentes:

- **Agente de IA** (usado tanto no chat do Agente de IA de portfólio
  quanto na Análise de processo): "Modelo local (grátis)" (padrão, sem
  mudança de comportamento pra quem não mexer em nada) ou "API do Claude
  (chave própria)" — a empresa cola a própria chave (gerada em
  console.anthropic.com) e opcionalmente escolhe o modelo exato. A chave é
  validada com uma chamada mínima antes de ser salva (nunca salva uma
  chave que não funciona), fica cifrada no banco (mesmo cofre Fernet de
  `SenhaProcesso`, ver `app/utils/cofre.py`) e nunca é reexibida depois —
  só aparece "chave cadastrada: sim". Roteamento entre os dois provedores
  é centralizado em `app/utils/agente_ia_router.py`, então nem
  `app/routes/agente_ia.py` nem `app/utils/analise_processo_ia.py`
  precisam saber qual dos dois está sendo usado.
- **Captura processual (DataJud)**: "chave padrão da plataforma" (padrão)
  ou "minha própria chave DataJud" (também gratuita, cadastro individual
  em datajud-wiki.cnj.jus.br — útil pra empresa que quer sua própria cota
  de uso, sem depender/disputar a cota compartilhada da plataforma).
  `app/utils/captura_conectores.py::obter_conector` agora aceita um
  parâmetro `empresa` opcional pra decidir qual chave usar; os três
  lugares que chamam essa função (`app/routes/governanca.py`, duas vezes,
  e `capturar_movimentacoes.py`) foram atualizados pra passar a empresa
  certa — o cron de recaptura em particular varre processos de TODAS as
  empresas numa mesma execução, então resolve o conector POR EMPRESA
  dentro do laço (com cache simples pra não decifrar a mesma chave duas
  vezes), em vez de uma única vez no início como era antes.

### O que ficou de fora de propósito, e por quê
- **Provedores pagos de captura** (Judit/Escavador/Digesto/Codilo) — cada
  um tem contrato de API próprio e diferente do DataJud. Implementar
  contra um deles sem a documentação e credenciais reais do provedor
  contratado arriscaria uma integração que parece funcionar mas devolve
  dado errado/incompleto silenciosamente. O ponto de extensão já existe
  (`ConectorCaptura` em `app/utils/captura_conectores.py`) — se/quando
  contratar um desses, me chame com a documentação da API dele e eu
  implemento a subclasse específica.
- **Cobrança de markup sobre uso de API** — não implementado, de propósito
  (ver explicação acima sobre os Termos Comerciais da Anthropic). Se
  quiser uma taxa fixa de desbloqueio no futuro, é rápido de adicionar
  reaproveitando `Licenca`/Mercado Pago.
- Colunas novas em `Empresa` (`agente_ia_provedor`,
  `agente_ia_claude_chave_cifrada`, `agente_ia_claude_modelo`,
  `datajud_provedor`, `datajud_chave_propria_cifrada`) foram criadas
  NULLABLE de propósito, mesmo as que têm um "padrão" em código — o
  `sincronizar_schema.py` só sabe adicionar coluna sem `DEFAULT` no banco,
  então uma coluna `NOT NULL` quebraria a sincronização em bancos com
  empresas já cadastradas (que é exatamente o seu caso em produção).
  `None` é tratado como o valor padrão em todo o código (ver
  `Empresa.agente_ia_provedor_efetivo` / `datajud_provedor_efetivo`).

### Testado no sandbox local antes de entregar
Subi o app contra um banco sqlite descartável, criei uma empresa cliente
com licença ativa e simulei login, e testei: a tela de Integrações abre e
salva as duas configurações; salvar Claude com chave inválida é rejeitado
com mensagem amigável e NÃO salva nada (sem chave falsa gravada); salvar/
remover chave própria do DataJud funciona e `obter_conector` de fato passa
a usar a chave da empresa; as telas do Agente de IA, Análise de processo e
cadastro por CNJ continuam respondendo normalmente (sem erro 500) quando
nenhum provedor está configurado, com aviso amigável apontando pra
"Minhas Integrações"; e o script `capturar_movimentacoes.py` roda sem
travar e resolve o conector por empresa dentro do laço. Não testei uma
chamada real à API da Anthropic com uma chave verdadeira (sem acesso de
rede a partir do ambiente onde gerei o código) — teste isso depois do
deploy com uma chave real da sua conta Anthropic (ou de um cliente de
teste) antes de anunciar a funcionalidade pros clientes.

## -6. Modelo de IA local maior avaliado e revertido por falta de RAM — script de troca deixado pronto

A pedido explícito ("deixar o agente de IA local mais robusto, se não
ficar muito maior"), cheguei a trocar o modelo de
**Qwen2.5-1.5B-Instruct (~1,1 GB)** para **Qwen3-4B-Instruct-2507 (~2,5
GB)** — mesma filosofia (100% local, grátis, sem dado saindo do
servidor), só que numa geração mais nova e maior, com ganhos relatados de
raciocínio e cobertura multilíngue. Antes de você fazer o deploy, você
checou o painel de recursos do EasyPanel e o servidor já estava com
**74,2% de RAM em uso (5,8 GB de 7,8 GB) antes de qualquer coisa da IA**
— com os 2 workers do gunicorn, o modelo maior sozinho poderia chegar a
~5 GB, o que estouraria a RAM total e derrubaria o servidor (OOM). Por
isso, **revertido para o modelo pequeno como padrão de produção**, que é
o que está ativo agora.

O que ficou, mesmo com a reversão (grátis, sem custo de RAM/download):
- Os prompts mais estruturados (esqueleto fixo de petição, seção
  obrigatória de "pontos para revisão humana" — ver
  `app/utils/analise_processo_ia.py`), que ajudam mesmo o modelo pequeno a
  ficar mais confiável.
- `baixar_modelo_ia_local.py` agora sabe baixar os dois tamanhos (veja
  abaixo) — não precisa reescrever nada quando quiser trocar de verdade,
  só seguir o passo a passo.

### Como ativar o modelo "grande" no futuro (se/quando o servidor tiver mais RAM)

Confira antes no painel do EasyPanel se há folga real — o modelo grande
soma ~5-6 GB de RAM com os 2 workers atuais; recomendo um servidor com
8 GB+ de RAM **livre** (não total) antes de tentar de novo. Feito isso:

1. No `Dockerfile`, troque a linha `RUN python baixar_modelo_ia_local.py`
   por `RUN python baixar_modelo_ia_local.py grande`.
2. Nas variáveis de ambiente do serviço do app principal no EasyPanel,
   defina `IA_LOCAL_MODELO_PATH=/app/app/ia_local/modelos/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`.
3. (Opcional, mas recomendado) suba `IA_LOCAL_CONTEXT_SIZE` de `4096` para
   `8192` — reduz o risco de cortar processos com histórico longo na
   Análise de processo. Se fizer isso, também vale subir
   `LIMITE_PADRAO_CHARS` em `app/utils/analise_processo_ia.py` (comentário
   já deixado no código apontando pra isso).
4. Redeploy — o build vai demorar mais na primeira vez (baixa ~2,5 GB).
5. Depois do deploy, volte a checar o painel de recursos do EasyPanel com
   o Agente de IA em uso (mande uma mensagem de teste) para confirmar que
   a RAM não estourou antes de considerar o assunto resolvido.

Se quiser, me chame de novo quando for fazer essa troca — eu ajudo a
confirmar os números e reviso os arquivos antes do deploy.

## -5. Regras de próxima ação, mapa de estado (TPU) e Análise com IA por processo — implementado nesta rodada

### Regras de próxima ação e mapa de código TPU → estado (telas novas, só admin)
O motor que gera prazo automaticamente a partir de um ato capturado
(`app/utils/prazos_engine.py`) e o que traduz movimentação em estado de
negócio (`app/utils/estado_processual_engine.py`) já existiam no código,
mas não havia nenhuma tela para cadastrar as regras que eles usam — sem
regra cadastrada, todo ato caía sempre no caminho genérico ("análise
necessária", prazo provisório de 5 dias). Agora existem, no menu
Governança (visível só para admin): "Regras de próxima ação" e "Mapa de
estado (TPU)" — CRUD completo (criar, editar, ativar/desativar; nunca
exclusão física, seguindo o mesmo padrão de governança do resto do
sistema). De propósito **nenhum prazo legal vem pré-cadastrado** — cadastrar
um prazo errado é grave (risco real de perda de prazo), então cabe ao
advogado responsável validar e digitar o prazo de cada tipo de ato
conforme a legislação e o rito aplicável (ex.: prazos de execução fiscal,
CPC etc.).

### Análise de processo com Agente de IA (resumo dos autos / rascunho de petição)
Nova aba "Análise IA" na tela de cada processo. Duas opções:
- **Resumo dos autos**: lê os dados reais do processo (movimentações,
  decisões, andamentos, prazos pendentes) e devolve um resumo objetivo da
  situação atual.
- **Rascunho de petição**: você descreve o que a peça precisa fazer (ex.:
  "contestação alegando decadência") e o agente gera um rascunho inicial
  em formato de petição, usando os dados reais do processo como base.

Roda no mesmo modelo de IA local gratuito que já era usado no Agente de IA
de portfólio (decisão sua, ver pergunta que te fiz antes de implementar, e
ver seção -6 acima sobre a troca para um modelo maior/mais robusto) — sem
custo por uso, sem dado saindo do servidor, mas com qualidade ainda
limitada por ser um modelo pequeno (4B parâmetros). Por isso:
- O sistema NUNCA deixa o rascunho passar por pronto: toda resposta vem
  com aviso para revisão humana, e o próprio modelo é instruído a escrever
  `[REVISAR: ...]` em vez de inventar lei, jurisprudência ou fato que não
  esteja nos dados reais do processo.
- Cada análise gerada fica salva no histórico do processo (para auditoria
  e consulta posterior), com quem pediu e quando.
- Histórico muito longo do processo é cortado para caber na janela de
  contexto do modelo local — quando isso acontece, aparece um aviso
  "histórico truncado" na análise.
- **Protocolo automático no PJe não foi implementado** — não existe API
  pública unificada de peticionamento eletrônico entre os tribunais
  (autenticação normalmente exige certificado digital ICP-Brasil), e
  automatizar isso via robô de navegador seria frágil e arriscado (um erro
  aqui não é uma mensagem que não chega, é uma petição errada ou duplicada
  no processo do cliente). O rascunho sempre precisa ser revisado e
  protocolado manualmente por um advogado.
- Se um dia quiser trocar o modelo local pela API paga da Anthropic
  (Claude) para essa função específica, por qualidade de redação jurídica
  mais alta, o sistema já tem o caminho pronto no código
  (`ANTHROPIC_API_KEY` em config.py) — só não ativei porque tem custo por
  uso e você pediu para usar o modelo gratuito.

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

### WhatsApp do lembrete — implementado com WAHA (automação não-oficial, escolha sua)
Você optou pela automação não-oficial em vez da API paga da Meta (ver as
3 opções na seção 2.2 abaixo) — ciente do risco real de o número usado
ser banido pelo WhatsApp por comportamento automatizado, já que isso
viola os Termos de Serviço da plataforma. Esse risco continua valendo e
está documentado em vários lugares do código para nunca ficar escondido.

**Mudança em relação à primeira versão:** a primeira implementação usava
um serviço Node.js escrito do zero (pasta `whatsapp-bridge/`, hoje
**descontinuada** — ver `whatsapp-bridge/DEPRECATED.md`, pode apagar essa
pasta). Troquei pelo **WAHA** (https://waha.devlike.pro), um projeto
open-source mantido especificamente para isso: já vem com painel de
conexão via QR code, chave de API e reconexão automática prontos, e roda
a partir de uma imagem Docker pronta — nada de código pra escrever ou
build pra configurar, só subir o serviço no EasyPanel apontando pra uma
imagem pública. Mais simples de configurar e mais confiável do que manter
na mão a mesma coisa que esse projeto já resolve.

**Como funciona:** o app Flask principal chama o WAHA por HTTP
(`app/utils/whatsapp.py`) quando precisa mandar o lembrete de um
compromisso — o WAHA é quem mantém a sessão do WhatsApp Web logada (a
mesma coisa que abrir web.whatsapp.com e escanear o QR code, só que como
um serviço rodando o tempo todo). Nenhuma lógica de WhatsApp roda dentro
do container principal.

**Passo a passo para ativar no EasyPanel (nenhum destes eu consigo fazer
por você — depende do seu painel e do celular físico do escritório):**

1. No EasyPanel, dentro do mesmo projeto do app principal, crie um
   **segundo serviço do tipo App**, mas em vez de apontar para um
   repositório Git, escolha a opção de origem **"Docker Image"** e use a
   imagem `devlikeapro/waha` (se o servidor for ARM — Raspberry Pi, por
   exemplo — use `devlikeapro/waha:arm`). Porta do serviço: `3000`.
2. Na aba **Environment** desse serviço, defina:
   - `WAHA_API_KEY` — uma chave forte que você escolher (qualquer string
     aleatória longa serve).
   - `WAHA_DASHBOARD_USERNAME` e `WAHA_DASHBOARD_PASSWORD` — usuário e
     senha pra proteger o painel do WAHA (ele fica acessível pela
     internet se o serviço tiver domínio público, então não deixe sem
     senha).
   - `WHATSAPP_DEFAULT_ENGINE=GOWS` — usa o motor GOWS em vez do WEBJS
     (padrão do WAHA). Fizemos essa troca porque o WEBJS tem um bug
     conhecido e ainda sem correção do próprio projeto ("No LID for
     user"/500 ao enviar mensagem) causado por uma mudança recente do
     WhatsApp Web — GOWS não depende do navegador/JS interno do WhatsApp
     Web, então não sofre desse bug. Ver detalhe mais abaixo, depois do
     passo 8.
3. Na aba **Storage**, adicione um **Volume** montado em `/app/.sessions`
   — sem isso, todo redeploy derruba a sessão e pede escanear o QR code
   de novo. É o ponto mais fácil de esquecer.
4. Faça o deploy desse serviço.
5. Abra o painel do WAHA: `<URL do serviço>/dashboard`, entre com o
   `WAHA_DASHBOARD_USERNAME`/`WAHA_DASHBOARD_PASSWORD` do passo 2, inicie
   uma sessão chamada **"default"** e escaneie o QR code que aparecer com
   o WhatsApp do **número escolhido para isso** — use um número dedicado,
   nunca o WhatsApp pessoal de um advogado nem o número principal de
   atendimento do escritório, justamente por causa do risco de
   banimento. O status da sessão deve mudar para "WORKING" depois de
   escanear.
6. No `.env` do app **principal** (não do WAHA), defina:
   - `WHATSAPP_BRIDGE_URL` — a URL do serviço WAHA. Tente primeiro o
     endereço interno do EasyPanel (geralmente o nome do serviço, ex:
     `http://waha:3000`, se os dois serviços estiverem no mesmo projeto —
     não consegui confirmar com 100% de certeza a convenção exata de
     endereço interno do EasyPanel a partir daqui); se não funcionar, use
     a URL pública que o EasyPanel atribuiu ao serviço WAHA (com
     `https://`).
   - `WHATSAPP_BRIDGE_TOKEN` — o mesmo valor que você colocou em
     `WAHA_API_KEY` no passo 2.
7. Faça o redeploy do app principal (pra ele carregar as novas variáveis
   de ambiente).
8. Pronto — marque "Também enviar por WhatsApp" num compromisso vinculado
   a um cliente com número cadastrado, e o lembrete sai pelos 3 canais
   (sistema + e-mail + WhatsApp) na hora marcada.

**Se depois de tudo isso ainda não funcionar:** rode
`python enviar_lembretes_compromissos.py` manualmente no console do
servidor (ou espere o próximo ciclo do cron, a cada 5 minutos) e confira
o log — ele agora diz exatamente o motivo quando um envio por WhatsApp é
pulado ou falha (bridge não configurada, cliente sem número, ou erro
retornado pelo WAHA). Me mande essa linha de log que eu ajusto.

**Sobre o erro "no LID found" / "No LID for user" (se aparecer):** é um
problema conhecido de identificação interna de números pelo WhatsApp,
mais comum em números brasileiros cujo cadastro no WhatsApp é anterior a
2012 e ficou registrado sem o 9º dígito (fora de SP/RJ/ES) mesmo o
telefone atual tendo o 9. Por isso `app/utils/whatsapp.py` NÃO monta mais
o identificador do destinatário "no chute" — antes de cada envio, ele
consulta o próprio WAHA (`GET /api/contacts/check-exists`) pra descobrir
o identificador certo do número, e só tenta enviar se o WAHA confirmar
que o número existe no WhatsApp. Combinado com `WHATSAPP_DEFAULT_ENGINE=
GOWS` (passo 2 acima), isso resolve o erro nos casos que testamos. Se
mesmo assim continuar falhando pra um número específico, o mais provável
é que esse número realmente não tenha WhatsApp ativo, ou tenha alguma
configuração de privacidade que impede a consulta — não é algo que dê
pra contornar do nosso lado.

**Recomendações práticas pra reduzir (não eliminar) o risco de
banimento:**
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

> Nota: existe um modelo local maior/mais robusto pronto para ativar (ver
> seção -6 no topo deste arquivo) quando o servidor tiver mais RAM
> sobrando — hoje o modelo pequeno descrito nesta seção continua sendo o
> padrão de produção. O restante da decisão registrada aqui (local em vez
> da API paga da Anthropic) continua valendo.

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
não-oficial grátis mas arriscada / não usar por enquanto), agora via
**WAHA** (imagem Docker pronta, ver seção -4 acima para o passo a passo
completo de ativação no EasyPanel). Continua valendo o alerta: esse
caminho viola os Termos de Serviço do WhatsApp e corre risco real de o
número usado ser banido — se em algum momento quiser migrar para a API
oficial da Meta (mais caro, mas sem esse risco), me avise que eu troco o
canal sem mexer no resto do sistema (a chamada em `app/utils/whatsapp.py`
fica isolada disso).

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
