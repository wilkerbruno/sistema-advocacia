# Status das pendências do briefing (atualizado em 11/09/2026)

## -94. Cadastro de processo novo já busca tudo, não só o DataJud

**Pedido:** "quero que busque todas as informações logo em adicionar um novo processo" — a seção -93
tinha deixado o botão "Buscar processo" completo pra um processo JÁ cadastrado, mas o momento de
CADASTRAR um processo novo (tela "Novo processo", "Editar processo" quando o número muda, e "Cadastrar
por CNJ") continuava tentando só o DataJud, deixando e-SAJ/PJe/PJe-JT de fora — obrigando a entrar no
processo depois e clicar em "Buscar processo" de novo pra completar.

**O que mudou:** os três pontos de cadastro/edição agora tentam o mesmo trio de fontes públicas que o
botão "Buscar processo" já usa — sempre DataJud primeiro e, na sequência, e-SAJ+PJe (número de segmento
estadual) ou PJe-JT (segmento trabalhista), conforme o segmento do CNJ:

- `processos.novo` ("Novo processo")
- `processos.editar` ("Editar processo"), só quando o número do processo muda de verdade (mesma regra
  de antes — editar outros campos não deve sair rebuscando um processo que o usuário já configurou)
- `governanca.novo_por_cnj` ("Cadastrar por CNJ")

**Como foi feito, sem duplicar código:** extraí as três funções que já existiam dentro de
`governanca.buscar_processo` (`_tentar_esaj`/`_tentar_pje`/`_tentar_pje_jt`) pra
`app/utils/captura_pipeline.py`, como `tentar_esaj_publico`/`tentar_pje_publico`/
`tentar_pje_jt_publico` + o orquestrador `tentar_fontes_publicas_complementares(processo,
segmento_codigo, sistema_escolhido="auto")` — agora é o mesmo código chamado tanto pelo botão "Buscar
processo" (que só ficou mais curto) quanto pelos três pontos de cadastro/edição acima. Também extraí
`mensagem_fontes_extra()` pra formatar o pedaço do flash que menciona o que essas fontes extras
encontraram, reaproveitado nos 3 lugares.

**Decisão importante — `monitoravel`/`forma_acompanhamento` continuam só do DataJud:** e-SAJ/PJe/PJe-JT
NUNCA mexem nesses três campos (nem aqui, nem no botão "Buscar processo" da seção -93) — só o DataJud
tem uma recaptura periódica de verdade (`capturar_movimentacoes.py`), então só ele pode honestamente
prometer "este processo fica em monitoramento automático". Um processo que o DataJud não achou, mas que
o e-SAJ encontrou e enriqueceu na hora do cadastro, continua marcado `nao_monitoravel` — os dados
aparecem (classe, partes, movimentações daquele momento), mas o flash deixa claro que atualizações
futuras não vêm sozinhas enquanto só essas fontes pontuais acharem o processo.

**Testes:** `tests/test_captura_completa_no_cadastro.py` (5 testes novos) — cobre cadastro via "Novo
processo" tentando DataJud+e-SAJ+PJe juntos (segmento estadual) e PJe-JT (segmento trabalhista), que
que processo sem número não tenta nenhuma fonte pública, edição com número mudado também tentando as
fontes extras, e "Cadastrar por CNJ" fazendo o mesmo. Suíte inteira: **261 testes passando** (era 256
antes desta seção).

## -93. Botão único "Buscar processo" — unifica DataJud/e-SAJ/PJe/PJe-JT/Agente Local

**Pedido:** hoje pra buscar um processo é preciso primeiro clicar em "Tentar captura automática"
(DataJud) e DEPOIS, separadamente, em "Buscar dados públicos do e-SAJ"/PJe/PJe-JT — um clique por
fonte. Pedido: um fluxo só, que (1) primeiro veja se o advogado tem o Agente Local pareado (token) e,
se tiver, já busque o processo COMPLETO por ele; (2) senão, deixe escolher o sistema do tribunal (ou
descubra sozinho) e já traga tudo automaticamente; (3) pros sistemas "link solto" (fora do nosso
alcance), busque no DataJud e dê um botão pra escolher entre o resultado do DataJud ou abrir o portal
do tribunal pra algo mais completo; (4) inclua o resumo em PDF.

**O que mudou:** um botão novo, **"Buscar processo"**, na tela do processo (card da esquerda),
substituindo ali os 4 botões separados (DataJud/e-SAJ/PJe/PJe-JT) e o formulário manual de conector do
Agente Local (que ficava na aba Documentos). Rota nova: `governanca.buscar_processo`
(`POST /governanca/processos/<id>/buscar-processo`), com esta ordem de decisão:

1. **Advogado tem Agente Local pareado?** Dispara os 3 conectores piloto (`pje_mni`, `projudi`,
   `esaj_sp` — ver seção -56 e `app/utils/tribunais_conectores.py`) **em paralelo** — cada um vira sua
   própria `SolicitacaoBuscaAutos`. Isso continua sendo **assíncrono**: quem responde de verdade é o
   agente instalado no computador do próprio advogado, na próxima vez que ele verificar por tarefas —
   não é uma resposta na hora do clique. Sem duplicar pedido (não cria de novo se já tem uma solicitação
   aberta pro mesmo conector+processo). Rotulado como piloto na tela (ainda não testado contra nenhum
   tribunal real — mesma ressalva de sempre).
   - **Fallback pra quem não quer esperar:** a tela sempre mostra um botão extra "buscar pelos sistemas
     públicos agora" (manda `forcar_publico=1` pra essa mesma rota), e esse botão fica destacado
     automaticamente quando as tentativas anteriores do agente já terminaram todas em erro (calculado
     direto do histórico de `SolicitacaoBuscaAutos` já carregado pra tela, sem precisar de fila/cron
     nenhum — este sistema não tem worker assíncrono provisionado, então "fallback automático depois de
     X minutos" não seria honesto de prometer; o botão manual sempre disponível é o que dá pra entregar
     de verdade agora).
2. **Sem agente pareado (ou `forcar_publico`):** caminho público — sempre tenta o DataJud primeiro
   (qualquer segmento), e na sequência o(s) conector(s) público(s) que atendem o segmento do número CNJ:
   e-SAJ e depois PJe pra estadual (segmento 8), PJe-JT pra trabalhista (segmento 5) — ou só o que o
   usuário escolheu no seletor "sistema" (auto/e-SAJ/PJe/PJe-JT), se preencheu. Roda tudo que se aplica
   NO MESMO clique — antes eram cliques separados; agora um só já traz o que der de mais completo, porque
   é seguro rodar várias fontes em sequência (`aplicar_carga_inicial` só preenche campo vazio,
   movimentação é deduplicada por hash).
3. **Segmento sem nenhum conector público** (só haveria os links soltos de eproc/Projudi/
   Creta/Tucujuris — seções -80/-81/-85/-87/-92): só o DataJud é tentado por esta rota; os botões de
   link solto, que já ficam na mesma tela (fora desta rota, inalterados), continuam sendo a forma do
   usuário escolher entre o que o DataJud achou ou abrir o portal oficial do tribunal pra uma busca
   manual mais completa — exatamente o "escolher DataJud ou algo mais completo" pedido.

**Resumo em PDF:** não precisou de nenhum código novo — o botão "PDF" que já existe no topo da tela
(`app/utils/pdf_processo.py`) monta o resumo na hora, direto do banco, então qualquer dado novo
capturado por "Buscar processo" já aparece nele automaticamente da próxima vez que for baixado (sem
cache, sem gatilho separado pra manter sincronizado).

**Decisão de risco (perguntei antes de programar, todas as 4 respostas vieram com a opção
recomendada):**
- Confirmado que "token do advogado" = o Agente Local já existente (certificado próprio, MNI/SOAP,
  nunca sai da máquina do advogado) — não um novo mecanismo.
- Construir já com os 3 conectores piloto, mesmo eles ainda não tendo sido testados contra nenhum
  tribunal real (rotulado como piloto na tela, com fallback gracioso pro público).
- Um botão só na tela, substituindo os outros — mas **as rotas antigas continuam existindo por baixo**
  (`tentar_captura`, `tentar_captura_esaj`, `tentar_captura_pje`, `tentar_captura_pje_jt`,
  `solicitar_busca_autos`), pra não quebrar nada que já dependa delas nem os testes que já cobriam cada
  uma — a rota nova (`buscar_processo`) tem sua própria lógica, sem reaproveitar as antigas por baixo dos
  panos, então dá pra aposentar as antigas mais tarde sem pressa, sem risco de regressão agora.
- Auto-detecção do Agente Local tenta os 3 conectores piloto em paralelo (mesma disciplina de nunca
  tentar adivinhar qual sistema o tribunal usa, já seguida pelos conectores públicos).

**Testes:** `tests/test_busca_processo_unificada.py` (9 testes novos) — cobre os 3 caminhos de decisão
(agente pareado cria as 3 solicitações; não duplica solicitação já aberta; `forcar_publico` pula pro
público), o caminho público automático por segmento (8 tenta e-SAJ+PJe, 5 tenta PJe-JT, outro segmento
só DataJud), o seletor manual de sistema restringindo a uma fonte só, processo sem número CNJ, e a
renderização da tela quando todas as tentativas do agente já terminaram em erro (o bloco de fallback
destacado). Suíte inteira: **256 testes passando** (era 247 antes desta seção).

## -92. Revisão dos "links soltos" — nenhum dá pra virar captura automática

**Pergunta:** com os links soltos (eproc, Projudi, Creta/Tucujuris) que só abrem o site do tribunal numa
aba nova, tem algum que já esteja sem essa exigência e desse pra transformar em captura de verdade (tipo
o que virou o PJe-JT)?

**Resposta curta: não, nenhum.** Fui reconferir um por um, com o mesmo cuidado que a experiência do TJMT
(seção -91) me ensinou — não basta ver se tem um script de captcha visível, tem que checar a chamada de
rede de verdade por trás.

- **eproc (TRF4/JFRS/JFSC/JFPR, TJSC, TJRJ)** — Cloudflare Turnstile confirmado ao vivo, com o desafio
  "confirme que é humano" realmente aparecendo na tela ao tentar enviar o formulário. Sem mudança.
- **eproc (TJRS)** — este era o único caso onde a documentação antiga só dizia "API própria protegida por
  uma credencial que não tentei replicar", sem detalhar o quê. Fui atrás: a API
  (`consulta-processual-service.tjrs.jus.br/api/consulta-service/v1/consultaProcesso`) exige um header
  `Authorization: Basic` cujo valor só existe depois de resolver um desafio de **Altcha** — um captcha
  "silencioso" de prova de trabalho (o navegador resolve um cálculo criptográfico contra
  `/auth/token` + `/auth/submit` antes de poder chamar a API) — confirmado lendo o JS do próprio app do
  TJRS. É a mesma família de proteção do Anubis (visto no TRT-23, seção -90): não tem tela vistosa de
  "clique aqui", mas é a mesma linha de captcha/anti-bot que eu não contorno. Documentação do
  `app/utils/eproc_links.py` atualizada com esse detalhe.
- **Projudi (TJPR, TJAM)** — TJPR: reCAPTCHA confirmado (inclusive um desafio de imagem real chegou a
  aparecer durante o teste ao vivo, seção -84). TJAM: reCAPTCHA + rejeição direta da requisição pelo WAF.
  Sem mudança.
- **Creta (JFPE) / Tucujuris (TJAP)** — Creta: captcha de imagem embutido direto no formulário, diferente
  a cada carregamento de página (não tem chamada de API pra pular isso — o próprio HTML já vem com a
  imagem). Tucujuris: Cloudflare Turnstile e reCAPTCHA do Google presentes ao mesmo tempo. Sem mudança.

**Conclusão:** o PJe (TJRJ/TJMG), o PJe-JT (22 TRTs) e o DataJud continuam sendo os únicos com captura
automática de verdade. Todo o resto que hoje é "link solto" tem uma camada de anti-bot confirmada e
ativa — visível (captcha de imagem, Cloudflare) ou silenciosa (fingerprint do TJMT, Altcha do TJRS,
Anubis do TRT-23) — e eu não construo nada que tente passar por nenhuma delas. Fica assim até algum
desses tribunais mudar de sistema/proteção — se isso acontecer, vale reconferir.

## -91. Conector novo pros 22 TRTs — e correção importante sobre o TJMT

**Pedido:** você aprovou os dois conectores da seção -90 ("sim, pode construir um conector para todos
os 22 TRTs e pode criar um conector para o TJMT também").

### Correção antes de tudo: o TJMT NÃO é livre, afinal

Antes de escrever qualquer código pro TJMT, fui confirmar a API de verdade por trás da busca (a seção -90
só tinha confirmado a AUSÊNCIA de script de captcha, não testei a chamada de rede real ainda). Achei: o
site do TJMT (`consultaprocessual.tjmt.jus.br`) chama uma API num domínio separado
(`hellsgate.tjmt.jus.br/consultaprocessual/ProcessosJudiciais/v2`) — e essa API **rejeita qualquer
chamada que não venha acompanhada de um "fingerprint"** gerado no navegador (testei direto, de dentro do
próprio site: `403 Fingerprint ausente`, mesmo mandando os headers certos de Referer/Origin).

Ou seja: o TJMT não usa um captcha visível (reCAPTCHA, hCaptcha, Cloudflare) — usa uma proteção
anti-bot silenciosa, baseada em "impressão digital" do navegador (esse tipo de token só um navegador de
verdade consegue gerar; um programa rodando no servidor, sem navegador de verdade, não gera). Na prática
é a mesma categoria de bloqueio que os outros ~30 tribunais com captcha visível — só que menos óbvio —, e
tentar contornar isso (fabricar um fingerprint falso) seria a mesma linha que eu já recusei cruzar antes
nesta conversa (seção -83, as ferramentas de bypass de Cloudflare do GitHub). **Não construí o conector do
TJMT.** Peço desculpa pela informação errada na seção -90 — devia ter testado a chamada de API de verdade
antes de dar o achado como confirmado, não só checado a ausência de script de captcha na página.

### O que foi construído: `ConectorPjeJtPublico`, pros 22 TRTs

Esse sim se confirmou de verdade: testei a API REST de trás do PJe-JT (`pje.trtN.jus.br/pje-consulta-api/
api/processos/dadosbasicos/{número}`, com um header `X-Grau-Instancia`) chamando direto, sem passar pelo
site — nenhuma exigência de fingerprint, token, cookie ou qualquer coisa parecida. Devolve JSON limpo
tanto pra número com dígito inválido (400) quanto pra processo inexistente (204) — dá pra confiar que
é mesmo uma API sem proteção, não só "não vi captcha na tela".

**Arquivos entregues:**
- `app/utils/conector_pje_jt_publico.py` (novo) — mesma arquitetura de tentar os 22 TRTs em paralelo
  (nunca decodifica o "TR" do número pra adivinhar qual), tenta 1º grau e cai pro 2º se não achar.
  Aviso importante no topo do arquivo: os nomes dos campos do retorno "encontrado" (`classe`,
  `orgaoJulgador`, `poloAtivo`/`poloPassivo`, `movimentos`) vieram de ler o JS compilado do próprio app
  do TRT-1 (fonte confiável — é o código-fonte de verdade do tribunal), mas **não foi confirmado contra
  uma resposta real de um processo existente** (não achei processo de teste disponível) — é esperado
  precisar de 1-2 ajustes na primeira captura real, mesma ressalva que já vale pro
  `conector_pje_publico.py` do TJRJ/TJMG. Os campos "assunto", "data de ajuizamento" e "valor da causa"
  não apareceram em lugar nenhum do código lido — ficam `None`/vazios até confirmar.
- `app/routes/governanca.py` — nova rota `tentar-captura-pje-jt` (mesmo padrão de
  `tentar-captura-pje`), e a pré-visualização por CNJ (`consultar_cnj_preview`) agora tenta o PJe-JT como
  segunda chance pra números da Justiça do Trabalho (segmento "5"), depois do DataJud.
- `app/__init__.py` — filtro de template novo `eh_cnj_pje_jt_candidato` (mesma ideia do
  `eh_cnj_esaj_candidato` já existente, só que pro segmento "5").
- `app/templates/processos/detalhe.html` — botão novo "Buscar dados públicos do PJe-JT" na tela do
  processo, aparece pra qualquer número trabalhista.
- `tests/test_conector_pje_jt_publico.py` (novo, 14 testes) — parsing, rejeição de segmento errado,
  fallback pro 2º grau, tribunal indisponível, resposta não-JSON, e a rota completa.

Suíte completa rodada: 247 testes passando (233 de antes + 14 novos).

**Fica registrado:** se em algum teste real de verdade os nomes de campo do payload "encontrado" não
baterem (bem possível, ver aviso acima), me manda a mensagem de erro exata que eu ajusto.

## -90. TJPA/TJMT a fundo + os 24 TRTs — achado grande: 22 dos 24 tribunais trabalhistas SEM captcha

**Pedido:** você pediu pra seguir os dois caminhos que ficaram em aberto na seção -89: investigar TJPA e
TJMT a fundo (testando uma busca de verdade), e testar os TRTs (Justiça do Trabalho).

### TJPA e TJMT — busca de verdade testada

- **TJPA** — cliquei em "Consulta Unificada de Processos" pra abrir o formulário de verdade. Ele **tem,
  sim, captcha** — uma imagem (`simpleCaptcha`) igual à do Creta, só que não apareceu na primeira olhada
  porque só carrega depois de abrir a aba certa. Então **TJPA fica bloqueado** também, como a maioria —
  minha suspeita inicial de que talvez fosse livre não se confirmou.
- **TJMT** — testei uma busca de verdade (número de processo fictício) e **não apareceu captcha em momento
  nenhum** — nem no carregamento da página, nem depois de clicar em "Buscar processo". A busca foi direto
  pra uma página de resultado (`/consulta-retorno?numeroUnico=...`) e voltou "Nenhum processo encontrado"
  (esperado, já que usei um número fictício só pra testar o fluxo — ainda não confirmei extração de dado
  real de um processo existente, só que o caminho até a busca é limpo). **TJMT parece genuinamente livre de
  captcha.**

### Os 24 TRTs — resultado bem diferente do que vinha acontecendo com TJs/TRFs

Achei a lista oficial de acesso no site do CSJT (Conselho Superior da Justiça do Trabalho) — todos os TRTs
rodam a mesma plataforma nacional padronizada (`pje.trtN.jus.br/consultaprocessual/`, "PJe-JT"), diferente
da bagunça de versões que vi nos TJs/TRFs. Testei os 24, um por um, ao vivo:

**22 de 24 SEM nenhum script de captcha (Google reCAPTCHA, hCaptcha, Turnstile ou Cloudflare) carregado —
nem no carregamento, nem depois de tentar uma busca:** TRT-1 (RJ), TRT-2 (SP), TRT-4 (RS), TRT-5 (BA),
TRT-6 (PE), TRT-7 (CE), TRT-8 (PA/AP), TRT-9 (PR), TRT-10 (DF/TO), TRT-11 (AM/RR), TRT-12 (SC), TRT-13
(PB), TRT-14 (RO/AC), TRT-15 (SP-Campinas), TRT-16 (MA — o link do CSJT tava quebrado, apontando pro TRT-15
por engano, mas o endereço certo `pje.trt16.jus.br` funciona normal), TRT-17 (ES), TRT-18 (GO), TRT-19
(AL), TRT-20 (SE), TRT-21 (RN), TRT-22 (PI) e TRT-24 (MS).

No TRT-1 e no TRT-2 cheguei a testar o fluxo de busca completo (preencher número, clicar em buscar) e os
dois voltaram erro de validação normal (número inválido/dígitos verificadores incorretos) sem nenhum
captcha aparecer — nos outros 20 confirmei pelo menos a ausência do script de captcha no carregamento da
página, que foi o sinal decisivo em todos os outros ~30 tribunais testados até agora nas seções -86 a -89
(todo bloqueio até agora veio acompanhado do script já carregado de cara).

**Só 2 de 24 bloqueados:**
- **TRT-3 (MG)** — bloqueio "Human Verification" antes até de mostrar a página (mesmo padrão do SEEU, seção
  -86).
- **TRT-23 (MT)** — proteção diferente de tudo que já vi nesse projeto: **Anubis**, um desafio de
  "prova de trabalho" (proof-of-work, tipo o que sites tipo Codeberg/kernel.org usam contra scraping em
  massa) — a página mostra "Certificando que você não é um bot" e fica calculando um hash antes de liberar
  o acesso.

### Conclusão — isso muda o quadro

Depois de ~30 tribunais testados nas últimas seções (quase todos bloqueados), a Justiça do Trabalho é
completamente diferente: por rodar uma plataforma nacional única e padronizada, **22 dos 24 TRTs estão
genuinamente abertos**, com a mesma estrutura de formulário em todos (campo `nrProcessoInput`, botão de
busca, rota `/detalhe-processo/{número}`) — dá pra escrever um conector só que atende os 22 de uma vez,
do jeito que o `ConectorPjePublico` já faz pro TJRJ/TJMG.

**Isso não virou código ainda — fica como pendência, porque duas decisões são suas antes de eu construir
algo:**

1. **O escritório atende causas trabalhistas?** Só faz sentido investir num conector pra Justiça do
   Trabalho se isso for relevante pro escritório — não presumi que sim.
2. **Quer que eu também monte um conector separado só pro TJMT** (que também confirmou sem captcha, mas
   roda numa plataforma própria — o portal unificado, diferente do PJe-JT dos TRTs — então não encaixa no
   mesmo conector)?

Se a resposta pras duas for sim, o próximo passo seria: construir um `ConectorPjeJtPublico` (ou estender o
existente com uma segunda classe) cobrindo os 22 TRTs livres, mais um `ConectorTjmtPublico` separado pro
TJMT — testar contra um número de processo real do escritório (se tiver algum na Justiça do Trabalho ou no
TJMT) antes de considerar pronto, e não incluir TJPA, TRT-3 nem TRT-23 (todos com captcha confirmado).

## -89. Continuação: os 8 tribunais do PJe que faltavam — checado ao vivo, mesmo padrão da seção -88

**Pedido:** você confirmou que o DataJud já está configurado no ambiente real, mas que ele traz bem menos
informação que o e-SAJ (por isso não é suficiente sozinho) — e pediu pra eu continuar testando os
tribunais do PJe que tinham ficado de fora da seção -88, já que precisa cobrir o máximo de tribunais
possível.

**Resultado — testei os 8 que faltavam (TJBA, TJPA, TJMT, TJRO, TJRR, TJAP, TJES, TJPB), um por um:**

- **TJBA** — hCaptcha carregado na página (`hcaptcha.com/1/api.js`). Bloqueado.
- **TJRO** — reCAPTCHA do Google. Bloqueado.
- **TJAP** — reCAPTCHA do Google, confirmado tanto no 1º grau (`/1g/`) quanto no 2º grau (`/2g/`).
  Bloqueado.
- **TJES** — a URL de consulta pública redireciona direto pro portal nacional **JUS.BR** (SSO do CNJ, o
  mesmo `sso.cloud.pje.jus.br` que já tinha aparecido no TJPI) — precisa de CPF/CNPJ+senha ou certificado
  digital. Não é mais pública.
- **TJPB** — atrás de Cloudflare (página "Um momento… Executando verificação de segurança", nunca chega a
  carregar o formulário).
- **TJRR** — a página trava carregando e nunca termina (fiquei mais de 15s esperando, em duas tentativas
  diferentes — o `<head>` carrega um único script e para aí, sem `<body>` nenhum). Não deu pra confirmar
  se é bloqueio ativo (tipo o travamento que vi no TJRN, seção -88) ou outra coisa — mas na prática é
  inutilizável do jeito que está.

Esses 6 seguem exatamente o padrão da seção -88: a estrutura de campos do formulário (quando cheguei a ver
o formulário) bate certinho com o que o `ConectorPjePublico` já sabe ler — o bloqueio nunca é a estrutura
da página, é sempre captcha, login obrigatório ou um travamento que impede até de chegar no formulário.

**Os outros 2 (TJPA e TJMT) foram diferentes — nem chegaram a carregar o PJe:**

- **TJPA** — `pje.tjpa.jus.br` redireciona pra `consultas.tjpa.jus.br/consultaunificada`, um portal
  próprio do tribunal ("Consulta Unificada de Processos") que parece juntar vários sistemas processuais
  num site só (tem inclusive um link de lá pro Projudi do TJPA). Não é mais o PJe direto.
- **TJMT** — `pje-intranet.tjmt.jus.br` (o nome "intranet" já era suspeito) redireciona pra
  `consultaprocessual.tjmt.jus.br`, outro portal próprio ("Esta consulta processual engloba todos os
  sistemas processuais judiciais do TJMT") — também não é mais o PJe direto.

Nos dois casos o tribunal aparentemente descontinuou o acesso direto ao PJe público e substituiu por um
portal unificado próprio. Olhei rapidinho a página inicial de cada um e **não vi script de captcha
carregado de cara** — mas isso não quer dizer muita coisa ainda: não cheguei a testar uma busca de verdade
(os dois parecem ser SPA, então o formulário de busca provavelmente só aparece depois de clicar em algo), não
sei se o captcha aparece só no envio, e não confirmei se o resultado retorna dado de verdade. Não dá pra
tratar isso como "achei dois tribunais livres" — é só uma pista de que existe um tipo de sistema diferente
aí (nem PJe, nem eproc, nem Projudi — um portal de busca unificado próprio de cada tribunal) que merece uma
investigação própria, separada do trabalho de estender o `ConectorPjePublico` (que é especificamente pro
PJe).

**Conclusão:** com essa rodada, já são 18 tribunais/varas testados ao vivo pro PJe entre as seções -88 e
-89, e nenhum novo entrou pro `ConectorPjePublico` (continua só TJRJ e TJMG). A hipótese inicial (TJRJ sem
captcha = provavelmente outros tribunais também) não se sustentou — parece que TJRJ e TJMG são raridade, não
regra.

**Fica como pendência, dois caminhos possíveis pra continuar buscando cobertura:**

1. **Investigar TJPA e TJMT a fundo** — testar a busca de verdade nos dois portais unificados (clicar,
   preencher um número de processo, ver se pede captcha só no envio, confirmar se retorna dado real). Se
   algum dos dois se confirmar livre de captcha, dá pra pensar num conector novo pra esse tipo de portal
   (não encaixa no `ConectorPjePublico` porque não é PJe) — mas só depois de confirmar ao vivo, não antes.
2. **Testar os TRTs (Justiça do Trabalho)** — ainda não testei nenhum dos 24 TRTs. Só faz sentido se o
   escritório atende causas trabalhistas — não vou testar 24 tribunais sem saber se isso é relevante pro
   escritório primeiro.

Qual desses (ou os dois) você quer que eu siga?

## -88. Tentativa de estender o PJe público a mais tribunais — checado ao vivo, quase tudo bloqueado

**Pedido:** você perguntou se tinha mais algum sistema de busca gratuito interessante pra incluir. Sugeri
estender o `ConectorPjePublico` (hoje só TJRJ/TJMG) pra outros tribunais que também rodam PJe, já que é
captura automática de verdade (não link) e o TJRJ não tinha CAPTCHA quando testado. Você topou.

**Resultado, bem diferente do que eu esperava — testei 10 tribunais ao vivo, praticamente todos
bloqueados ou indisponíveis:**

- **TJMA** — reCAPTCHA do Google carregado na página.
- **TJDFT** — inconclusivo: a URL de consulta redireciona pra uma página que carrega em branco (sem
  formulário, sem erro visível) — pode ser SPA que não terminei de esperar carregar, não confirmei se é
  bloqueio ou só um problema de timing.
- **TJPI** — a consulta "pública" de verdade sumiu: agora redireciona pro portal nacional **JUS.BR**
  (SSO do CNJ), que exige CPF/CNPJ+senha ou certificado digital pra entrar. Não é mais pública.
- **TJRN** — atrás de Cloudflare (a página nem termina de carregar, fica no desafio).
- **TJPE** — reCAPTCHA do Google.
- **TJCE** — reCAPTCHA do Google.
- **TRF1** — hCaptcha (um provedor de captcha diferente dos outros vistos até agora).
- **TRF3** — hCaptcha.
- **TRF5** — reCAPTCHA do Google.
- **TRF6** — hCaptcha.

A estrutura do formulário (os campos que o `ConectorPjePublico` já sabe ler) bateu certinho em quase
todos — o problema nunca foi a estrutura da página, foi captcha (ou login obrigatório, no caso do TJPI).

**Conclusão — corrigindo o que eu disse antes:** minha aposta inicial (de que o PJe do TJRJ não ter
CAPTCHA era sinal de que outros tribunais também não teriam) não se confirmou. Pelo visto, cada tribunal
decide isso por conta própria, e a maioria já colocou alguma proteção — TJRJ e TJMG parecem ser a exceção,
não a regra. Não estendi o conector pra nenhum desses 10: um `ConectorPjePublico` apontado pra um
formulário com captcha simplesmente nunca vai achar nada (erro silencioso ou falso "não encontrado"), o
que seria pior do que não ter a integração. Não testei os outros tribunais que rodam PJe (TJBA, TJPA,
TJMT, TJRO, TJRR, TJAP, TJES, TJPB — mais os TRTs, se o escritório atender trabalhista) — dado o padrão de
10 em 10, a expectativa realista é a mesma coisa, mas não vou generalizar sem confirmar cada um.

**Fica como pendência:** se quiser, posso testar os tribunais que faltam (ou algum específico que
interesse mais pro escritório) antes de desistir de vez — mas não prometo achar nenhum sem captcha. Outra
opção mais garantida: confirmar se o `DATAJUD_API_TOKEN` já está configurado no ambiente real — é de graça,
cobre os 91 tribunais pra acompanhar processo pelo número, e não depende de nenhum tribunal individual não
ter proteção.

## -87. Links soltos pro Creta (JFPE) e Tucujuris (TJAP)

**Pedido:** depois da pesquisa da seção -86, você confirmou que queria os botões de link solto pro Creta e
pro Tucujuris (os dois únicos dos seis pesquisados que tinham alguma coisa pra construir).

**O que foi implementado:** `app/utils/creta_tucujuris_links.py` — mais simples que os módulos anteriores
(eproc_links.py, projudi_links.py): aqui só existe `tipo: "link"`, nunca `"bridge"` — nenhum dos dois tenta
pré-preencher o número do processo, porque confirmei ao vivo que os dois pedem algum tipo de captcha em
toda busca (imagem no Creta, Cloudflare+reCAPTCHA no Tucujuris) e não faria diferença tentar. Por isso não
precisou de rota nova nem de template novo — é só mais um bloco de botões `<a href>` direto, igual ao
padrão já usado pros links soltos (TJRS no eproc, TJAM no Projudi).

Um recorte importante: do Creta, só entrou a seção da **Justiça Federal em Pernambuco (JFPE)** — a única
que testei e confirmei ao vivo. O Creta também aparece em Sergipe, Ceará, Paraíba e Alagoas, mas cada seção
parece ter endereço/estrutura próprios (a de Sergipe, por exemplo, nem parecia ser página de consulta
pública — parecia tela de login) — não incluí nenhuma sem confirmar ao vivo primeiro. Se quiser cobertura
das outras seções do Creta, me avisa que eu testo cada uma antes de adicionar.

Na tela do processo, um bloco novo "Outras consultas públicas (Creta, Tucujuris)" aparece ao lado dos
outros dois. 4 testes novos em `tests/test_creta_tucujuris_links.py` (URL certa pro segmento certo, lista
vazia pro segmento errado) — suíte inteira: **233 passando** (229 antes + 4 novos).

## -86. SEEU, Themis, Creta, Apolo, e-JUD, Tucujuris — checados ao vivo, um por um

**Pedido:** você pediu pra dar sequência à mesma pesquisa (eproc, Projudi) pra mais seis sistemas: SEEU,
Themis, Creta, Apolo, e-JUD e Tucujuris.

**Testei ao vivo cada um — resultado bem misto, nenhum dá pra automatizar, e dois nem são sistema de busca
de processo:**

- **SEEU** (Sistema Eletrônico de Execução Unificado) — é um sistema NACIONAL do CNJ (mesma "família" de
  software do Projudi/PJe — os endereços internos são idênticos, `consultaPublica.do`, etc.), mas só cobre
  **execução penal** (cumprimento de pena, progressão de regime — não processo cível/geral comum). Achei
  a consulta pública (`seeu.pje.jus.br/seeu/processo/consultaPublica.do`), mas ela é bloqueada ANTES de
  mostrar qualquer formulário — aparece uma tela "Vamos confirmar que você é humano" (verificação de bot
  mais agressiva que as outras, nem chega a mostrar os campos). Bloqueado, e de relevância baixa pro
  escritório mesmo se não fosse (é só pra execução penal).
- **Themis / E-Themis** (TJRS, também usado em variantes no TJMG e TJPE) — **não é um sistema de consulta
  pública**. Testei o `e-themis1g` do TJRS e caí direto numa tela de login ("Autenticação") — não existe
  busca pública por trás disso; é um sistema interno de tramitação.
- **Creta** (usado por Justiça Federal em Sergipe, Pernambuco, Ceará, Paraíba, Alagoas — JEF, Juizados
  Especiais Federais) — a página de busca carrega normal, mas tem um **captcha de imagem clássico**
  ("Informe o que está escrito na imagem ao lado") **embutido direto no formulário**, obrigatório em toda
  pesquisa, sem exceção — não é um desafio que aparece só às vezes, tem que ler e digitar sempre. Não dá
  pra pré-preencher e mandar direto porque o campo do captcha muda a cada carregamento da página.
- **Apolo** (usado no TRF2/JFRJ até 2021, e no TJMT até alguns anos atrás) — **está sendo desativado**: o
  TRF2 aprovou em 2024 a substituição pelo eproc (o mesmo eproc do TRF4, já coberto — e já bloqueado, seção
  -80), e a página de dúvidas frequentes do TRF2 confirma que não existe mais URL pública de consulta do
  Apolo — só contato via Balcão Virtual pra processos antigos. Nada pra construir aqui.
- **e-JUD** — esse nome não é um sistema único: no TJRJ é uma ferramenta INTERNA de acompanhamento de
  processos de 2ª instância (as páginas encontradas são manuais de intranet, sem nada público); no TJAM e
  no TJPI, "EJUD"/"e-Jud" é sigla da **Escola Judicial** (instituição de ensino/capacitação dos
  tribunais) — não tem relação nenhuma com consulta de processo. Não existe nada pra construir aqui, em
  nenhum dos dois sentidos.
- **Tucujuris** (TJAP, sistema próprio) — a página de busca (`tucujuris.tjap.jus.br`) carrega normal, mas
  tem **Cloudflare Turnstile E reCAPTCHA do Google ao mesmo tempo** (as duas proteções presentes no HTML)
  — o mais protegido de todos que já vi até agora.

**Conclusão:** nenhum desses seis dá pra automatizar (mesma resposta de sempre). De diferente dos casos
anteriores: dois (Themis, e-JUD) simplesmente não são sistemas de busca pública — não tem "link de
conveniência" possível porque não existe página de consulta por trás; Apolo está sendo desativado, então
também não vale a pena; SEEU é bloqueado antes até de mostrar o formulário. Sobram só **Creta** e
**Tucujuris** como candidatos a link de conveniência solto (sem pré-preencher, dado que os dois têm
captcha obrigatório em toda busca e/ou token de sessão que eu não confiaria em prever antes da hora) — se
quiser, dá pra adicionar os dois como botões desse tipo. Me avisa se quer que eu faça isso ou se prefere
deixar tudo isso como pendência mesmo.

## -85. Links de conveniência pro Projudi — TJPR (auto-envio) e TJAM (link solto)

**Pedido:** depois da pesquisa da seção -84, você confirmou que queria a mesma solução do eproc (seção
-81) aplicada ao Projudi.

**O que foi implementado:** `app/utils/projudi_links.py` — mesmo padrão de `app/utils/eproc_links.py`,
mas com dois tribunais tratados de forma diferente por causa do que foi confirmado ao vivo na seção -84:

- **TJPR** — vira uma ponte de auto-envio (`tipo: "bridge"`), igual ao TJSC/TJRJ no eproc: uma página nova
  (`governanca.abrir_projudi_auto_envio`) renderiza um `<form>` oculto que se auto-envia por **POST** pro
  formulário real (`consulta.tjpr.jus.br/projudi_consulta/processo/consultaPublica.do?actionType=pesquisar`),
  com os mesmos nomes de campo confirmados ao vivo no HTML real (nunca inventados) e o número do processo já
  preenchido. Como confirmei que Comarca/Juízo não são obrigatórios pra busca por número, não precisa
  adivinhar nada disso. **Ressalva importante, diferente do eproc:** o captcha do TJPR é um reCAPTCHA do
  Google que só aparece via JavaScript da própria página ao clicar "Pesquisar" — não confirmei se o
  formulário aceita a busca vindo da nossa ponte (sem esse JavaScript carregado) ou se o servidor recusa por
  faltar o token do captcha. É uma tentativa razoável e de baixo risco (mesmo espírito da seção -82): se
  não funcionar, o pior caso é uma tela de erro, aí a pessoa refaz a busca direto no site — não fica pior
  do que não ter o botão. Testa quando puder e me avisa se der problema.
- **TJAM** — entra só como link solto (`tipo: "link"`) pra página de busca, sem tentar pré-preencher o
  número. Motivo: numa tentativa de busca de teste ao vivo nesse tribunal, a requisição foi rejeitada na
  hora por um firewall de aplicação — sinal de que ali vale mais cautela (mesmo tratamento dado ao TJRS no
  eproc).
- **TJGO não entra** — bloqueou já no carregamento simples da página nos testes da seção -84, não dá pra
  confiar nem num link solto.

Reaproveitei o template de auto-envio do eproc em vez de duplicar: `app/templates/processos/_eproc_auto_envio.html`
foi generalizado (texto não fala mais só de "eproc"/"Cloudflare", e o `<form>` agora lê o método —
GET ou POST — de `formulario.metodo`, já que o TJPR precisa de POST e o eproc de GET). Isso também mudou o
`id` do formulário e do script de auto-envio (`formEprocAutoEnvio` → `formConsultaPublicaAutoEnvio`) — ajustei
a asserção correspondente em `tests/test_eproc_links.py`.

Na tela do processo, uma nova seção "Consulta pública do Projudi" aparece ao lado da do eproc (mesmo aviso
de que abre o site oficial fora do JusControl e que é a pessoa quem resolve o captcha). 7 testes novos em
`tests/test_projudi_links.py` (montagem dos links, conteúdo do formulário de auto-envio do TJPR, 404 pra
slug desconhecido e pra processo sem número) — suíte inteira: **229 passando** (222 antes + 7 novos).

## -84. Projudi — checado ao vivo (TJPR, TJAM, TJGO): mesma situação do eproc, proteção anti-bot em todos

**Pedido:** depois de decidir deixar de lado as ferramentas de bypass de Cloudflare (seção -83), você pediu
pra passar pro Projudi — a mesma pergunta de antes ("dá pra estender a busca pública pra esses tribunais
também?"), agora pro sistema que TJPR/TJAM/TJGO/TJBA usam.

**Mapeamento:** usei a tabela "Cobertura dos Tribunais" da JUDIT (ao vivo, no seu navegador, a mesma fonte
usada na seção -80) pra levantar quais tribunais rodam Projudi como sistema — **TJAM, TJBA** (ao lado do
PJe), **TJGO** e **TJPR**.

**Testei ao vivo os 3 primeiros (achei bloqueio em todos, de tipos diferentes):**

- **TJPR** (`projudi.tjpr.jus.br` → `consulta.tjpr.jus.br/projudi_consulta/`): o formulário público carrega
  normal, sem CAPTCHA na tela inicial — mas ao clicar "Pesquisar" aparece um reCAPTCHA de imagem do Google
  ("selecione todos os quadrados com bicicletas"). Só dispara na hora de pesquisar, igual ao padrão já visto
  no TRF4/eproc (seção -80). Um detalhe bom: confirmei no HTML do formulário que **Comarca e Juízo não são
  obrigatórios quando a busca é por número do processo, CPF ou CNPJ** (só são obrigatórios pra busca sem
  esses dados) — então dá pra montar um link de conveniência só com o número, sem ter que adivinhar a
  comarca.
- **TJAM** (`projudi-consulta.tjam.jus.br/publica`): a página já carrega com o script do Google reCAPTCHA
  embutido (campo oculto `g-recaptcha-response`). Ao tentar pesquisar com um número de teste, a requisição
  foi rejeitada direto por um firewall de aplicação ("The requested URL was rejected") — nem chegou a
  mostrar o desafio, foi barrada antes.
- **TJGO** (`projudi.tjgo.jus.br/BuscaProcesso`): bloqueado já no **carregamento da página** — apareceu um
  aviso "o sistema de proteção identificou um comportamento incomum e bloqueou a solicitação" com um desafio
  Cloudflare "Verificando..." embaixo. (TJGO também tem um segundo sistema, `pjd.tjgo.jus.br`, que exige
  login/certificado direto — sem consulta pública nenhuma.)

Não testei o TJBA porque lá o Projudi é secundário (o principal é PJe, que já está coberto pelo
`ConectorPjePublico`) — mas dado o padrão de 3 em 3 até aqui, não há razão pra esperar diferente.

**Conclusão:** mesma situação nacional do eproc (seção -80) — todo Projudi testado tem alguma camada de
proteção anti-bot (reCAPTCHA de imagem, reCAPTCHA + firewall de aplicação, ou Cloudflare), nenhum viável
pra captura automática sem login. Parei de testar assim que confirmei o bloqueio em cada um — não tentei
nenhum jeito de contornar (mesma disciplina das seções -80/-81/-83).

**Fica pendente pra você decidir:** dá pra construir a mesma solução que já existe pro eproc (seção -81) —
links de conveniência que abrem a página oficial do tribunal, com o número do processo já preenchido
quando o formulário permitir, pra alguém do escritório resolver o captcha manualmente. Funcionaria bem pro
**TJPR** (confirmei que não precisa de Comarca pra busca por número) e provavelmente pro **TJAM**; o
**TJGO** é mais incerto, porque bloqueou até o carregamento simples da página nesse teste — um link de
conveniência pode não carregar de forma confiável nesse caso. Me avisa se quer que eu construa isso.

## -83. Ferramentas de "bypass" de Cloudflare (GitHub) — avaliadas, recusadas, fica como pendência

**Pedido:** você mandou dois links do GitHub perguntando se resolviam o problema do Cloudflare no eproc —
a página de topics `github.com/topics/cloudflare-bypass` e o projeto
`github.com/sarperavci/CloudflareBypassForScraping` — e depois insistiu perguntando se pelo menos
"ajudariam um pouco".

**O que são, de fato:** ferramentas de automação com navegador "stealth" (Chromium com patch de
fingerprint) que resolvem o desafio Cloudflare/Turnstile sozinhas — inclusive clicando no captcha via
manipulação do shadow DOM — e devolvem um cookie de sessão (`cf_clearance`) pra reuso em requisições
automatizadas depois. Tecnicamente, funcionariam pro problema do eproc (é exatamente pra isso que
existem). A página de topics lista várias ferramentas do mesmo tipo (`undetected-chromedriver`,
`cloudscraper`, `patchright`, `botasaurus` etc.) — todas na mesma linha: navegador automatizado disfarçado
de humano ou manipulação de fingerprint TLS/JA3.

**Por que não foram usadas:** é bypass automatizado de CAPTCHA/anti-bot por definição — a linha que eu não
cruzo, sem exceção, independente da escala (testar com 1 processo ou automatizar tudo dá no mesmo: é o
mesmo tipo de ação). Além do princípio, tem um risco prático real pro escritório: esse tipo de ferramenta
é alvo constante de detecção, e tribunais que percebem o padrão de tráfego costumam banir o IP/rede de
origem inteira — arriscando até o acesso normal do escritório aos sites dos tribunais pelo navegador comum,
o que seria pior que a situação atual.

**Decisão sua:** deixar de lado por enquanto, seguir com o eproc bloqueado (seções -80/-81/-82) e passar
pro Projudi.

## -82. Achado em uso real: o Cloudflare descartava o POST — trocado pra GET

**O que aconteceu:** você testou o botão do TJSC (seção -81) e mandou um print: o Cloudflare passou
sozinho (desafio "invisível" — "Sucesso!"), mas o número do processo não apareceu no formulário — voltou
tudo em branco. Você perguntou se, já que o Cloudflare passou sozinho, dava pra fazer direto sem precisar
abrir essa página.

**Duas coisas separadas nessa pergunta:**

1. **O Cloudflare passar "sozinho" não abre porta pra automação.** Ele passou porque era o SEU navegador
   de verdade, no seu computador — com todos os sinais que convencem o Cloudflare de que é um humano
   (JS rodando, fingerprint, histórico). Se essa mesma chamada viesse do nosso servidor (um robô Python,
   sem navegador), o Cloudflare não deixaria passar do mesmo jeito — é literalmente pra isso que ele
   existe. Não muda a resposta da seção -80.

2. **O número sumir era um bug real, não intencional.** O formulário de verdade do TJSC/TJRJ é POST, e é
   um problema conhecido do Cloudflare: quando ele intercepta um envio POST pra validar o desafio, não
   consegue "repetir" esse POST depois — devolve a pessoa pra uma tela em branco, mesmo com o desafio
   passando. Troquei o envio de POST pra GET (`app/utils/eproc_links.py` e
   `app/templates/processos/_eproc_auto_envio.html`) — mesmos nomes de campo reais de sempre, só muda o
   método. GET sobrevive ao redirecionamento do Cloudflare porque o estado todo já vai embutido na
   própria URL (é por isso que o TRF4, que já era GET desde o início, tem mais chance de funcionar
   direto). **Não tenho 100% de certeza que o backend do TJSC/TJRJ aceita a busca via GET** (só confirmei
   que o formulário real declarado é POST) — é uma tentativa razoável e de baixo risco: se não funcionar,
   o pior caso é cair na mesma tela em branco de antes, não piora nada. Se continuar em branco depois
   desse ajuste, me avisa com um print que eu reviso a abordagem (aí sim precisaria aceitar que o número
   pré-preenchido não é viável pro formato POST desses dois, e deixar só o link solto tipo o do TJRS).

Ajustei os 9 testes de `tests/test_eproc_links.py` pra refletir GET em vez de POST — suíte inteira
continua 222/222 passando.

## -81. Links de conveniência pro eproc — não é captura automática, é o humano resolvendo o CAPTCHA

**Pedido:** depois da seção -80 (eproc inteiro bloqueado por CAPTCHA), você perguntou se dava pra
"burlar esse captcha de alguma forma" ou "passar pro cliente fazer sem sair do nosso sistema". Respondi
que contornar CAPTCHA não é algo que eu faço, sem exceção — mas que a segunda ideia (a PESSOA resolve o
desafio ela mesma, como qualquer cidadão faria, só que com o número do processo já pronto) é legítima e
diferente. Você confirmou que queria isso.

**O que foi implementado:** `app/utils/eproc_links.py` — módulo que monta, pro número CNJ de um
processo, uma lista de links pro site OFICIAL do tribunal, sem capturar nada de volta:

- **TRF4/JFRS/JFSC/JFPR** (segmento "4" — Justiça Federal): o portal `consulta.trf4.jus.br` é um
  formulário GET de verdade (confirmado ao vivo na seção -80) — dá pra montar a URL direto, com o número
  já preenchido, pros 4 (TRF4 2º grau, e a Justiça Federal de 1º grau no RS/SC/PR, que passam pelo mesmo
  portal). É um `<a href>` puro, abre em aba nova.
- **TJSC e TJRJ** (segmento "8" — Justiça Estadual, formulário eproc "clássico" `externo_controlador.php`,
  que é POST): não dá pra montar como link — criei uma rota nova,
  `governanca.abrir_eproc_auto_envio` (`GET /processos/<id>/abrir-eproc/<slug>`), que renderiza uma
  página (`processos/_eproc_auto_envio.html`) com um `<form>` OCULTO que se auto-envia via JS pro site do
  tribunal, com os MESMOS nomes de campo confirmados ao vivo no HTML real de cada um (nunca inventados).
  Isso é exatamente o que o navegador da pessoa faria se ela preenchesse e clicasse "Consultar" à mão —
  não é um jeito de pular o CAPTCHA: o Cloudflare intercepta a requisição do mesmo jeito (confirmei isso
  testando o TRF4 ao vivo — a mesma tela "confirme que é humano" aparece nos dois casos).
- **TJRS**: como é uma SPA em Angular (não um formulário HTML comum), entra como link solto pra página de
  busca, sem o número pré-preenchido — não tentei adivinhar um jeito de pré-preencher via parâmetro de URL
  sem confirmar que o app aceita isso (mesma disciplina de nunca inventar comportamento não confirmado
  que vale pros nomes de campo do TJSC/TJRJ; aliás, uma tentativa de simplificar isso inventando um
  parâmetro de URL pro TJSC foi bloqueada por uma proteção de segurança automática por parecer um teste
  de invasão contra o site do tribunal — ajustei a abordagem em vez de insistir).
- **TJTO e TJPR ficam de fora** (mesmo motivo da seção -80): TJTO não tem mais essa consulta pública (dá
  404, desligada desde 2020); TJPR ainda não tem consulta pública do eproc pra abrir.

Na tela do processo, apareceu uma nova seção "Consulta pública do eproc" (visível só quando o número CNJ
é de segmento Federal ou Estadual — nunca tenta adivinhar qual tribunal específico é o certo; a pessoa
escolhe clicando), com um aviso explícito de que os links abrem o site do tribunal FORA do JusControl e
que é a pessoa logada quem resolve o desafio do outro lado — pra ninguém achar, na hora de usar, que
virou captura automática. 9 testes novos em `tests/test_eproc_links.py` (montagem dos links pros dois
segmentos, conteúdo do formulário de auto-envio, 404 pra slug desconhecido e pra processo sem número) —
suíte inteira: 222 passando (213 antes + 9 novos).

## -80. "Todos os tribunais possíveis" no eproc — checado ao vivo, TODOS bloqueados por CAPTCHA (conclusão nacional)

**Pedido:** depois da seção -79 (TJRJ bloqueado por CAPTCHA, TJRS deixado de lado por sua escolha, TJPR
sem eproc público ainda), você perguntou se o resto dos tribunais do eproc estava funcionando, e depois
pediu pra cobrir **"todos os tribunais possíveis que rodam no eproc"**.

**Primeiro, o mapa completo:** usei o navegador do seu computador pra abrir a página "Cobertura dos
Tribunais" da JUDIT (empresa de dados jurídicos, tabela atualizada em setembro/2026) e consegui a lista
de verdade de quem roda eproc no Brasil hoje: **estaduais** TJRS, TJSC, TJTO (fora o TJMG, que já cobrimos
via PJe, e tem eproc como sistema secundário); **Justiça Federal 1ª instância** JFES, JFPR, JFRJ, JFRS,
JFSC; **2ª instância** TRF2 e TRF4 (TRF4 é a origem do eproc, atende PR/RS/SC). TJRJ tem uma instância de
eproc própria fora dessa lista oficial (achado da seção -79), provavelmente um sistema legado/paralelo de
baixo volume.

**Testei ao vivo, um por um, com o navegador do seu computador (não dá pra fazer isso do sandbox —
domínio `.jus.br` bloqueado tanto por `requests` quanto por `WebFetch`):**

- **TJSC** (eproc "clássico", mesmo tipo de tela do TJRJ): tem o mesmo campo `cf-turnstile-response` —
  **Cloudflare Turnstile ativo.** Bloqueado.
- **TJTO**: a consulta pública por número de processo nem existe mais — dá **404**. Achei a explicação:
  o TJTO suspendeu essa consulta em 2020 por causa de **sobrecarga de robôs/scrapers** e, pelo visto,
  nunca reativou (só sobrou consulta de pauta de julgamento, não de processo por número). Bloqueado —
  não por CAPTCHA, mas porque o recurso foi desligado.
- **TRF4** (a "Consulta Processual Unificada", que cobre o próprio TRF4 + Justiça Federal do RS, SC e PR
  de uma vez só — ou seja, testar esse único ponto já cobre TRF4, JFRS, JFSC e JFPR): o formulário em si
  não tem CAPTCHA visível, mas ao **executar uma busca de verdade**, aparece uma tela do Cloudflare
  "Confirme que é humano" antes de mostrar qualquer resultado. **Bloqueado** — e isso derruba de uma vez
  TRF4, JFRS, JFSC e JFPR, já que os quatro passam pelo mesmo portal.
- **TJRS**: já registrado na seção -79 (API própria, exige credencial que não tentei replicar).
- **TJRJ** (instância eproc própria): já registrado na seção -79 (Cloudflare Turnstile).

Não cheguei a testar a JFRJ isoladamente (ela é atendida pelo TRF2, não pelo portal do TRF4) nem o TRF2
em si — mas dado que **5 de 5 pontos de consulta testados até agora bateram em proteção anti-bot ou
recurso desligado**, e que isso bate com uma causa raiz real e documentada (o episódio de sobrecarga por
robôs do TJTO em 2020), a leitura mais honesta é que o consórcio nacional do eproc (CJF + tribunais
conveniados) reagiu a esse tipo de abuso adotando Cloudflare de forma ampla — não é azar de amostragem,
é política de proteção coordenada. Não testei TRF2/JFRJ isoladamente porque a evidência acumulada já é
forte o suficiente pra não justificar mais rodadas de teste ao vivo sem um motivo concreto pra achar que
seria diferente.

**Conclusão:** nenhum tribunal que roda eproc, dos que consegui verificar, tem hoje um caminho aberto pra
um conector automático sem certificado/login que não passe por contornar CAPTCHA — o que não faço, sem
exceção. Isso é diferente do e-SAJ (Softplan) e do PJe (CNJ, mas sem essa proteção nos tribunais
testados), que continuam funcionando normalmente. Por ora, **eproc fica de fora da busca pública como um
todo**, não por falta de tentativa, mas porque o próprio ecossistema do eproc parece ter fechado essa
porta nacionalmente. Se algum tribunal específico abrir uma consulta pública sem essa proteção no futuro,
ou se você tiver uma forma legítima (documentada oficialmente) de autenticar contra a API do TJRS, dá pra
retomar.

## -79. Pedido de estender a busca pública para o eproc — pesquisa feita, NADA implementado ainda (dois bloqueios reais)

**Pedido:** depois de ajustar o conector do PJe (seção -78) contra um caso real que falhou (TJRJ deu
timeout, TJMG respondeu "não encontrado" — corrigido: mensagem no plural errada e timeout 15s→30s), você
pediu "vou agora precisar que o mesmo ocorra com o eproc" — ou seja, o mesmo tipo de busca pública (sem
certificado, sem login) só que para os tribunais que rodam eproc, principalmente TJRS e TJPR (os dois que
ficaram de fora na seção -78 por causa disso).

**O que fiz:** como o sandbox não alcança nenhum domínio `.jus.br` (nem por `requests`, nem por
`WebFetch` — confirmado de novo), usei o navegador Chrome do seu computador (via ponte remota) pra abrir
de verdade as páginas de consulta pública do eproc do TJRJ e do TJRS e inspecionar o HTML/JS real — mesmo
tipo de verificação que fiz contra os logs de produção do e-SAJ, só que ao vivo em vez de por log colado.

**Achado 1 — TJRJ (a instância eproc dele, separada do PJe já coberto): bloqueado por CAPTCHA.**
Descobri que o TJRJ, além do PJe (já coberto), também tem uma instância própria de eproc em
`eproc1g-cp.tjrj.jus.br` (provavelmente pra um recorte de processos mais antigos/específicos). O
formulário de consulta pública de lá tem um campo `cf-turnstile-response` — **Cloudflare Turnstile**, uma
proteção anti-bot ativa. Um conector por HTTP puro (como o do e-SAJ e do PJe) não consegue gerar esse
token, e contornar CAPTCHA/anti-bot é algo que não faço, não importa o pedido — é uma das minhas regras
de segurança, sem exceção. **Bloqueio definitivo pra essa instância específica** (não afeta o PJe do TJRJ,
que continua funcionando normalmente).

**Achado 2 — TJRS: sem CAPTCHA, mas a API pública exige uma credencial que não consegui replicar (e não
tentei forçar).** O "Sistema Eproc" do TJRS (que É o sistema principal do tribunal, ao contrário do TJRJ)
usa uma consulta processual moderna, em `consulta.tjrs.jus.br/consulta-processual/` — um app Angular que
chama uma API JSON de verdade em `consulta-processual-service.tjrs.jus.br/api/consulta-service/v1/
consultaProcesso?numeroProcesso=...&codComarca=...` (bem mais limpo que o HTML do PJe/e-SAJ: resposta
404 clara pra "não encontrado", sem scraping de tela). Também consegui a lista completa das 165 comarcas
do RS com seus códigos internos (`codComarca`) direto do app. **Mas**: quando tentei chamar essa API
diretamente (fora do app), recebi 401 (não autorizado) — o app usa algum mecanismo de sessão/token pra
autorizar as chamadas que não consegui identificar de forma limpa, e a única pista que encontrei era um
valor guardado no `sessionStorage` do navegador que parecia ser esse token. Não tentei usar esse valor
pra montar as chamadas — extrair e reutilizar um token de sessão desse jeito seria explorar uma
credencial fora do fluxo pretendido pelo tribunal, o que não faço mesmo sem exigir login/senha de verdade.
**Bloqueio por ora, não necessariamente definitivo** — pode ser algo simples (um header público que não
tentei, tipo `Referer`/`Origin`) ou pode ser proposital. Precisaria investigar mais pra ter certeza, sem
cruzar essa linha.

**TJPR:** não cheguei a testar ao vivo ainda (a pesquisa por texto já tinha mostrado que a migração pro
eproc ainda está em fases — a mais recente prevista pra 01/10/2026 —, o que já tornava esse alvo instável
por conta própria).

**Conclusão:** diferente do PJe e do e-SAJ, nenhum dos alvos de eproc testados até agora tem um caminho
limpo pra um conector automático sem contornar proteção nenhuma. Nada foi implementado — nem escrito
"no escuro" (a mesma disciplina de não adivinhar campo de formulário sem confirmação real, elevada aqui
pra também não adivinhar/forçar autenticação).

**Decisão sua:** perguntei como prefere seguir com o TJRS — você escolheu **deixar de lado por enquanto**
em vez de investigar mais a fundo o mecanismo de autorização da API. Ou seja: eproc fica sem cobertura
nenhuma por ora (nem TJRJ, bloqueado por CAPTCHA; nem TJRS, deixado de lado; nem TJPR, ainda migrando).
Só retoma isso se aparecer uma forma legítima de autorizar as chamadas ao TJRS (ex: API pública
documentada oficialmente pelo tribunal) ou se você trouxer alguma informação nova.

## -78. Consulta pública "igual o e-SAJ" para TJRJ e TJMG (PJe) — TJRS e TJPR ficaram de fora, e um alerta sobre o TJSP

**Pedido:** depois do PDF/audiências (seção -77), você pediu "quero que funcione igual o esaj para todos os
outros tribunais também" — ou seja, estender a busca pública (sem certificado, sem token, sem login) pra
tribunais além dos 6 que já usam e-SAJ.

**Perguntei antes de construir** (dois cliques seus): por quais tribunais começar → você escolheu
**"TJMG, TJRJ, TJRS, TJPR"**; o que fazer quando um tribunal não der pra fazer sem senha/certificado →
você escolheu **"pular esse tribunal e avisar"**. Pesquisei os 4 antes de escrever qualquer código (mesma
disciplina de nunca adivinhar já usada em app/utils/tribunais_datajud.py) e o resultado foi bem diferente
do e-SAJ: lá, os 6 tribunais rodam a MESMA plataforma (Softplan); aqui, cada tribunal roda um sistema
diferente, e o Brasil inteiro está no meio de uma migração nacional (coordenada pelo CNJ) de PJe/SAJ para
um sistema mais novo chamado **eproc**, ao longo de 2026 — isso muda o que dá pra fazer com segurança
tribunal por tribunal:

- **TJRJ — implementado.** Roda PJe como sistema principal hoje (migração pro eproc em andamento desde
  jan/2025, mas só parcial/por competência). A consulta pública é real: sem login, sem CAPTCHA aparente.
- **TJMG — implementado, com aviso.** Também tem consulta pública PJe, mas está NO MEIO da migração pro
  eproc (iniciada set/2025, 5 fases, previsão de concluir maio/2026 — ou seja, já deve estar bem avançada
  hoje). Processos mais recentes do TJMG podem já ter migrado e não aparecer mais aqui; processos mais
  antigos/parados devem continuar aparecendo. Não tem como saber de antemão qual é qual — só testando.
- **TJRS — pulado, com aviso (conforme sua escolha).** O Rio Grande do Sul já roda "eproc" como sistema
  PRINCIPAL (não é migração em andamento, já é assim hoje) — o endereço PJe que existe é só pra processos
  antigos. Não existe (que eu tenha encontrado) nenhuma implementação de referência aberta e testada de
  consulta pública do eproc pra tribunal ESTADUAL sem login — implementar às cegas arriscaria um conector
  que parece funcionar mas nunca acha nada. Fica pra quando houver uma fonte confiável pra confirmar contra.
- **TJPR — pulado, com aviso (conforme sua escolha).** Roda Projudi (sistema próprio do Paraná) hoje;
  só começa a migrar pro eproc em abril/2026. A página pública dele é renderizada via JavaScript pesado —
  não consegui inspecionar o formulário real a partir daqui, nem achei uma implementação de referência
  aberta de consulta de PROCESSO (só de jurisprudência) no Projudi. Mesmo motivo do TJRS: sem uma forma
  confiável de confirmar os campos, implementar seria adivinhar.

**O que foi implementado:** um novo conector `app/utils/conector_pje_publico.py` (`ConectorPjePublico`),
espelhando `conector_esaj_publico.py` — mesmo padrão de busca em paralelo pelos candidatos (TJRJ, TJMG),
mesmo log do motivo técnico quando um tribunal não responde, mesmo reaproveitamento do pipeline de carga
inicial. Ver o aviso completo no topo do arquivo pra duas diferenças importantes em relação ao e-SAJ:
(1) a consulta pública do PJe não distingue processo em segredo de justiça de "não encontrado" (o e-SAJ
mostra uma tela pedindo senha; o PJe simplesmente não devolve nada nos dois casos); (2) a tabela de partes
do PJe não expõe advogado, só nome + situação processual — `partes_texto` (seção -77) vem sem advogado
pra processos capturados por aqui. Também: como não consegui acessar `tjrj.pje.jus.br` nem
`pje-consulta-publica.tjmg.jus.br` a partir daqui pra testar de verdade (diferente do e-SAJ, testado e
ajustado contra processos reais nas seções -71 a -76), o formulário/fluxo foi replicado da ÚNICA
implementação de referência aberta e testada que encontramos pra uma consulta pública de PJe sem login
(o scraper que o pacote `juscraper` mantém pros Tribunais Regionais Federais) — é esperado que a PRIMEIRA
tentativa real contra um processo de TJRJ/TJMG precise de 1-2 ajustes, do jeito que aconteceu com o e-SAJ
do TJCE/TJMS. Quando testar, cole aqui a mensagem de erro exata (e o log do servidor, se houver).

Na tela do processo, apareceu um botão novo "Buscar dados públicos do PJe", ao lado do botão do e-SAJ —
mesma regra de visibilidade (qualquer processo da Justiça Estadual). A pré-visualização "Novo processo"
(que já tentava DataJud → e-SAJ público) agora tenta também PJe público como terceira chance, sem custo de
espera perceptível a mais (o usuário já ia esperar as outras responderem). Não precisa rodar
`sincronizar_schema.py` desta vez — nenhuma coluna/tabela nova, só um conector e duas rotas novas. 10
testes novos (`tests/test_conector_pje_publico.py`) + 1 teste existente atualizado (a pré-visualização
"não encontrado em lugar nenhum" agora precisa mockar as três fontes) — suíte completa em 213 testes, sem
nenhuma regressão.

**⚠️ Achado à parte, sobre um recurso JÁ EXISTENTE (não corrigido agora, só um alerta):** durante esta
pesquisa descobrimos que o próprio **TJSP** — coberto pelo conector e-SAJ já em produção — está migrando
processos do e-SAJ pro eproc desde 2025, por área (Cível já migrou; Fazenda Pública a partir de
agosto/2026). Não é um bug pra corrigir agora, mas é esperado que, com o tempo, uma fatia crescente de
processos do TJSP pare de ser encontrada pelo botão "Buscar dados públicos do e-SAJ" — não porque quebrou,
mas porque o processo literalmente mudou de sistema. Se isso começar a aparecer nos seus testes ("processo
não encontrado" num processo do TJSP que antes funcionava), é esse o motivo mais provável — vamos precisar
de um conector pro eproc do TJSP em algum momento, não uma correção no e-SAJ.

## -77. PDF do processo + detecção automática de audiências + lista de partes capturada

**Pedido:** depois de a captura pelo e-SAJ público funcionar de verdade num processo real ("Processo
encontrado no e-SAJ (TJSP) — 0 movimentação(ões) nova(s) e 2 parte(s) identificada(s)"), você pediu três
coisas: (1) um PDF do processo; (2) buscar também documentos; (3) audiências e prazos "com status reais".

Perguntei antes de construir o que deveria entrar no PDF (escolheu **resumo completo** — capa, partes,
movimentações, prazos e audiências, com o timbrado do escritório) e se deveria construir a detecção
automática de audiências agora (escolheu **sim**).

**Sobre "buscar documentos" — não é possível hoje, e por quê:** nem o DataJud nem o e-SAJ público (a
mesma limitação já registrada desde a seção -71) devolvem o CONTEÚDO real de petições/decisões — só
metadados e o texto das movimentações. Buscar o inteiro teor de documentos exigiria contratar um provedor
pago (Judit, Escavador, Digesto ou Codilo — ver app/utils/captura_conectores.py, onde essa limitação já
estava documentada antes desta rodada). Não implementei nada que fingisse cobrir isso.

**Sobre "prazos com status reais" — já estava funcionando, conferi e não precisou de nada novo:** toda
movimentação capturada (De qualquer fonte — DataJud ou e-SAJ público) já passa pelo motor de próxima ação
(app/utils/prazos_engine.py) desde antes desta rodada, criando o `Prazo` com status real
(pendente/cumprido/perdido/etc.) — por regra cadastrada quando existe, ou como tarefa genérica "Análise
necessária" quando não existe nenhuma regra pro texto daquela movimentação (nunca ignora o ato). Isso já
vale igual pra movimentação vinda do e-SAJ público, então não havia nada faltando aqui.

**Audiências com status reais — isso sim era uma lacuna real, e construí:** não existia NENHUMA detecção
automática de audiência a partir do texto capturado — só cadastro manual (formulário na aba Audiências).
Criei `app/utils/audiencias_engine.py`: reconhece por padrão de texto em português quando uma movimentação
fala de audiência **designada**, **redesignada/remarcada**, **realizada** ou **cancelada**, extrai
data/hora e o tipo (conciliação, instrução, julgamento etc.) quando aparecem no texto, e cria ou atualiza o
registro de `Audiencia` correspondente sozinho. Roda tanto na captura automática (DataJud/e-SAJ público)
quanto no registro manual de movimentação (mesmo pipeline).

Isto é heurístico por natureza — nenhuma das duas fontes devolve hoje um campo estruturado "isto é uma
audiência" (o DataJud até tem código TPU por movimentação, mas não existe uma tabela confiável de "quais
códigos são de audiência", mesma cautela já registrada para outro problema em
`app/utils/tribunais_datajud.py`) — por isso reconhece por texto, cobrindo as fórmulas mais comuns, mas
qualquer tribunal que fugir muito disso simplesmente não é detectado (a movimentação continua registrada
normalmente, só não vira Audiencia sozinha — nunca finge ter reconhecido algo incerto). Toda audiência
criada ou atualizada por aqui fica marcada com um ícone 🤖 na aba Audiências (com a movimentação de origem
nas observações) — nunca se disfarça de cadastro manual, sempre dá pra conferir e corrigir. Uma
movimentação de status (realizada/cancelada/remarcada) só atualiza uma audiência JÁ agendada — nunca
inventa uma audiência do nada sem pelo menos uma data ou uma agendada prévia pra associar.

**PDF do processo:** botão "PDF" ao lado de "Editar" na tela do processo, abre numa aba nova. Reaproveita
o mesmo timbrado do escritório já usado no recibo financeiro (PENDENCIAS.md, seção -69) e traz capa
completa, a lista de partes (ver abaixo), todas as movimentações, todos os prazos (com status) e todas as
audiências (com status, e marcando quais foram detectadas automaticamente). Não é o PDF real do processo
no tribunal — é um resumo gerado pelo próprio JusControl com o que já está cadastrado (mesma limitação de
"buscar documentos" acima).

**De brinde, uma lacuna que apareceu ao planejar o PDF — lista de partes:** reparei que a lista de partes
que o e-SAJ público devolve (nomes + advogados) nunca era guardada em lugar nenhum — só contada pra
mensagem de sucesso da busca ("2 parte(s) identificada(s)") e descartada em seguida. Sem isso, o PDF não
teria nada de útil pra mostrar na seção "Partes". Agora fica guardada em `Processo.partes_texto` (texto
simples, uma parte por linha) e também aparece na tela do processo, embaixo de "Parte contrária". O
DataJud não devolve partes hoje (a API pública dele não expõe isso), então esse campo só preenche de
verdade a partir do e-SAJ público, por enquanto.

**Testado:** suíte inteira passando — 203 testes (182 de antes + 21 novos: 13 do motor de detecção de
audiência — designação com/sem hora, sem data extraível, deduplicação, realizada, cancelada, "não
realizada" não confundida com "realizada", redesignação com e sem nova data, sem audiência prévia pra
associar, e a integração via captura automática — e 8 do PDF/lista de partes — formatação da lista,
sobrescrita a cada captura, PDF vazio e completo gerando um arquivo válido, e a rota).

**Arquivos tocados:** `app/models/processo.py` (`Processo.partes_texto`; `Audiencia.deteccao_automatica` e
`Audiencia.movimentacao_id`), `app/utils/audiencias_engine.py` (novo), `app/utils/captura_pipeline.py`
(hook da detecção de audiência + `formatar_partes_texto` + preenchimento de `partes_texto`),
`app/routes/governanca.py` (mesmo hook no registro manual de movimentação), `app/utils/pdf_processo.py`
(novo), `app/routes/processos.py` (rota `/processos/<id>/pdf`), `app/templates/processos/detalhe.html`
(botão PDF, lista de partes, ícone 🤖 nas audiências detectadas), `tests/test_audiencias_engine.py`
(novo), `tests/test_pdf_processo.py` (novo).

**Depois de subir esta versão:** ⚠️ **desta vez precisa rodar `python sincronizar_schema.py`** no
terminal do EasyPanel depois do deploy — são 3 colunas novas (`Processo.partes_texto`,
`Audiencia.deteccao_automatica`, `Audiencia.movimentacao_id`), todas opcionais (nullable), sem risco pros
dados já cadastrados. Sem isso, tentar salvar um processo/audiência pode dar o mesmo tipo de erro "Unknown
column" já visto antes (seção -69).

## -76. Corrigido o erro 500 que a correção do TJCE (seção -75) causou

**Meu erro:** a correção da seção -75 (desligar a verificação de certificado só pro TJCE) tinha um
defeito que só aparece na hora de conectar de verdade — passou pelos meus testes automatizados porque,
neste ambiente onde eu trabalho, existe uma variável de configuração de rede própria dele que mascarava
exatamente esse problema (fazia esta consulta ignorar silenciosamente o "desligar verificação" e usar um
certificado de outro tipo por baixo dos panos, sem erro nenhum). Só depois de forçar esse mesmo ambiente a
se comportar como o seu servidor de produção (sem essa variável) foi que consegui reproduzir o 500 exato
que você recebeu, usando o traceback que você mandou.

**A causa técnica, pro registro:** ao criar o contexto de TLS mais permissivo que o TJCE precisa
(SECLEVEL=1, seção -72), o Python cria esse contexto já com "verificar o nome do servidor no certificado"
ligado por padrão. A correção da seção -75 tentava desligar a verificação de certificado só na hora de
usar a sessão — mas o Python não permite desligar "verificar certificado" enquanto "verificar nome do
servidor" continuar ligado no MESMO objeto: dá erro (`ValueError: Cannot set verify_mode to CERT_NONE when
check_hostname is enabled`), e como ninguém tratava esse erro específico, ele subia até virar a página de
erro 500 que você viu. A correção agora desliga as duas coisas juntas, na ordem certa, direto na criação do
contexto do TJCE — sem essa inconsistência.

**Testado:** reproduzi o crash exato de produção neste ambiente (forçando a mesma condição de rede
"limpa", sem a variável que mascarava o problema) antes de corrigir, confirmei que a correção resolve, e
rodei a suíte inteira depois — 182 testes (181 de antes + 1 novo, que trava exatamente essa combinação de
configurações de TLS pra nunca mais regredir sem que um teste quebre primeiro).

**Isso não muda nada do que foi dito na seção -75** sobre TJMS (continua parecendo bloqueio de rede, sem
correção possível em código) nem sobre o TJCE em si (a verificação de certificado continua desligada só
pra ele, pelo mesmo motivo já explicado — nenhum dado sensível é enviado nessa consulta).

**Arquivos tocados:** `app/utils/conector_esaj_publico.py` (contexto de TLS do TJCE corrigido),
`tests/test_conector_esaj_publico.py` (1 teste novo).

**Depois de subir esta versão:** só `git add`/`commit`/`push` normal — nenhuma coluna nova no banco, não
precisa rodar `sincronizar_schema.py`. Peço desculpa pelo transtorno — essa dessa vez era um erro meu
mesmo, não uma limitação de fora do meu controle como o caso do TJMS.

## -75. e-SAJ público: causa real do TJCE corrigida; TJMS parece bloqueio de rede (sem correção possível em código)

**O log que você mandou (seção -74) trouxe a resposta.** Com o motivo técnico de cada tribunal indo pro
log, apareceram dois problemas BEM diferentes por trás da mesma mensagem genérica "não responderam a
tempo":

- **TJCE:** `SSLCertVerificationError: ... self-signed certificate in certificate chain`. Isso não tem
  nada a ver com timeout — é o servidor do TJCE apresentando um certificado com um certificado
  autoassinado na cadeia, que a verificação padrão do Python rejeita antes mesmo de tentar buscar
  qualquer dado. É um problema comum em sites `.jus.br` que usam certificado ICP-Brasil (a raiz dessa
  cadeia não faz parte do repositório de certificados confiáveis que o Python usa por padrão) — **isso é
  corrigível em código**, e já corrigi: a verificação de certificado agora é desligada especificamente
  para o domínio do TJCE (nenhum outro tribunal é afetado). Nenhum dado sensível é enviado nessa consulta
  (é busca pública, sem certificado digital nem login), então o risco aceito dessa escolha se limita a um
  eventual ataque man-in-the-middle conseguir forjar uma resposta falsa dessa consulta específica — não
  há vazamento de credencial nenhuma, porque nenhuma é enviada.

- **TJMS:** `ConnectTimeoutError: ... Connection to esaj.tjms.jus.br timed out (connect timeout=10)`. Isso
  é diferente: a conexão nem chegou a ser estabelecida (timeout já na hora de conectar, não de esperar
  resposta depois de conectado). Esse é o padrão típico de bloqueio de IP em nível de rede/firewall — o
  tribunal (ou a rede dele) parece estar descartando silenciosamente os pacotes vindos do IP do seu
  servidor, em vez de recusar a conexão ou de simplesmente estar lento. **Isso eu não consigo corrigir em
  código**: nenhum ajuste de timeout, retry ou lógica resolve um bloqueio de rede — é exatamente o risco
  que já estava avisado desde a primeira versão deste conector ("risco real de bloqueio de IP pelo
  tribunal, rodando de datacenter").

**O que isso significa na prática:** a busca deve ficar mais rápida e mais certeira a partir de agora
pro TJCE (deixou de falhar por completo). Pro TJMS, a situação provavelmente continua a mesma — não
porque o código esteja errado, mas porque o bloqueio (se for isso mesmo) está fora do alcance do código.

**Se quiser confirmar a hipótese do bloqueio de rede no TJMS:** o teste mais simples e rápido que você
mesmo pode fazer é abrir `https://esaj.tjms.jus.br/cpopg/search.do` num navegador comum, de casa ou do
escritório (fora do servidor/datacenter). Se abrir normalmente daí, é forte indício de que é mesmo um
bloqueio específico do IP do servidor, não uma instabilidade geral do site do TJMS. Não existe hoje,
neste projeto, nenhuma forma de contornar isso (precisaria de algo como um proxy/VPN com IP residencial
brasileiro pra sair do servidor — isso teria custo e outras implicações, e não é algo que eu implementaria
sem sua decisão explícita, pelo mesmo motivo já registrado noutras pendências deste tipo).

**Testado:** suíte inteira passando — 181 testes (180 de antes + 1 novo, confirmando que a verificação de
certificado desligada vale só pra sessão do TJCE, tanto no caminho direto quanto no caminho em paralelo —
os outros 4 tribunais continuam com verificação normal).

**Arquivos tocados:** `app/utils/conector_esaj_publico.py` (verificação de certificado desligada só para
o domínio do TJCE, aviso do urllib3 correspondente suprimido, docstring atualizada explicando os dois
diagnósticos), `tests/test_conector_esaj_publico.py` (1 teste novo).

**Depois de subir esta versão:** só `git add`/`commit`/`push` normal — nenhuma coluna nova no banco, não
precisa rodar `sincronizar_schema.py`.

## -74. e-SAJ público: motivo técnico de cada tribunal indisponível agora vai pro log do servidor

**Relato:** logo depois de a versão da seção -73 subir, você mandou a mensagem de erro nova (já
nomeando os tribunais) que apareceu num teste real: "Processo não encontrado em nenhum dos tribunais
e-SAJ testados (TJAC, TJAL, TJAM, TJCE, TJMS) (**TJCE, TJMS** não responderam a tempo...)".

**O que isso me mostrou, e por que não bastava só aumentar o timeout de novo:** reparei que TJCE e TJMS
já tinham aparecido como os "tribunais que não responderam" **antes** da seção -73 também — só que
naquela versão cada tribunal tinha 20s pra responder (sequencial), e mesmo assim esses dois falharam. Ou
seja, o mesmo par de tribunais falhou tanto com 20s quanto com 10s (paralelo). Isso é um indício de que
pode NÃO ser só "faltou tempo" (o que aumentar o timeout resolveria) — pode ser que esses dois tribunais
específicos estejam bloqueando ou de fato fora do ar a partir do seu servidor (o mesmo risco de bloqueio
de IP por datacenter já avisado desde a seção -71). Só que a mensagem que existia até agora não permitia
distinguir as duas situações: tanto um timeout de verdade quanto uma recusa de conexão, falha de TLS ou
falha de DNS geravam exatamente a mesma frase genérica "não respondeu a tempo".

**Tentei confirmar eu mesmo primeiro, mas não deu:** tentei testar a conectividade com os domínios do
TJCE e do TJMS diretamente daqui, mas o ambiente onde eu trabalho bloqueia esses dois domínios
especificamente por política de rede própria dele (não tem nada a ver com o seu servidor) — não consegui
usar isso pra tirar a dúvida.

**O que corrigi:** o motivo técnico REAL de cada tribunal que falhar (timeout puro, conexão recusada,
falha de handshake TLS, falha de DNS — hoje tudo isso cai na mesma exceção de rede e virava a mesma
frase genérica) agora é gravado no log do servidor (mesmo mecanismo já usado em `app/utils/email.py` e
`app/utils/whatsapp.py` pra esse tipo de falha esperada/degradação) toda vez que um tribunal fica
indisponível. A mensagem que aparece pra você na tela continua simples, sem termo técnico — só o log do
servidor (o mesmo tipo de log que você já colou aqui outras vezes) passa a trazer o detalhe.

**Próximo passo, preciso da sua ajuda:** da próxima vez que isso acontecer, me manda o log do servidor de
perto do horário do erro (mesmo formato que você já colou antes) — com essa mudança, ele vai trazer uma
linha tipo "e-SAJ público: TJCE não respondeu à consulta do processo ... — motivo técnico: ...", e aí
dá pra saber se é: (a) `Read timed out` de verdade (só lentidão — aí sim vale aumentar o timeout), ou
(b) `Connection refused`/erro de TLS/falha de DNS (bloqueio real — timeout maior não resolveria, precisaria
de outra abordagem, tipo verificar se o IP do seu servidor está bloqueado por esses dois tribunais
específicos). Também ajudaria eu saber se foi o MESMO número de processo das duas vezes (a de antes da
seção -73 e essa) — se sim, é ainda mais forte a hipótese de bloqueio permanente desses dois tribunais
específicos.

**Testado:** suíte inteira passando — 180 testes (178 de antes + 2 novos: o log realmente grava o motivo
técnico de cada tribunal separadamente — inclusive testando que "Connection refused" e "timeout" não se
confundem —, e a tentativa de log nunca quebra a consulta quando chamada fora de uma request Flask, como
acontece em todo o resto dos testes deste arquivo).

**Arquivos tocados:** `app/utils/conector_esaj_publico.py` (log do motivo técnico por tribunal
indisponível), `tests/test_conector_esaj_publico.py` (2 testes novos).

**Depois de subir esta versão:** só `git add`/`commit`/`push` normal — nenhuma coluna nova no banco, não
precisa rodar `sincronizar_schema.py`.

## -73. e-SAJ público (seção -72) mais rápido + mensagem de erro diz qual tribunal falhou

**Pedido:** depois de a versão da seção -72 já estar no ar, você recebeu (usando um processo real) a
mensagem "Processo não encontrado em nenhum dos tribunais e-SAJ testados (TJAC, TJAL, TJAM, TJCE, TJMS)
(2 tribunal(is) não respondeu/responderam e não pôde/puderam ser conferido(s) agora — pode estar lá mesmo
assim)" e perguntou, além da demora, se eu poderia conferir e corrigir.

Eram dois problemas reais nessa mensagem: (1) a busca é lenta porque os 5 tribunais eram consultados **um
de cada vez** (sequencial), então no pior caso (nenhum responde rápido) a espera é a soma dos 5 tempos; e
(2) a mensagem só dizia "2 tribunais não responderam", sem dizer QUAIS — impossível saber se é um problema
passageiro (rede lenta num instante) ou permanente (ex: aquele tribunal específico bloqueando o IP do
servidor, o mesmo risco que já estava documentado desde a seção -71).

**O que mudei:**

Os 5 tribunais (TJAC, TJAL, TJAM, TJCE, TJMS) agora são consultados **em paralelo** (uma tarefa por
tribunal, ao mesmo tempo), em vez de um de cada vez — a espera passa a ser limitada pelo tribunal mais
lento entre os 5, não pela soma de todos. Também reduzi o tempo limite de cada um desses 5 de 20s pra 10s
(o TJSP, que é 1 requisição só e direta, continua com 20s — não mudou). Na prática isso deve deixar a
busca sensivelmente mais rápida no caso comum.

Efeito colateral, aceito de propósito: antes, se a busca encontrasse uma tela de "protegido por senha" no
2º tribunal tentado, ela parava ali e nunca chegava a consultar o 3º, 4º e 5º (economia de tempo). Como
agora as 5 saem praticamente ao mesmo tempo, essa economia deixa de existir — mas isso não muda o
resultado que você vê, só o número de requisições feitas nos bastidores, e o ganho de velocidade do
paralelismo compensa de sobra.

A mensagem de erro final agora **nomeia** qual(is) tribunal(is) especificamente não respondeu(ram) a
tempo — por exemplo "(TJCE, TJMS não responderam a tempo e não puderam ser conferidos agora — pode estar
lá mesmo assim)" em vez de só "(2 tribunal(is)...)". Da próxima vez que isso acontecer, me manda a
mensagem nova: se for sempre o(s) mesmo(s) tribunal(is) toda vez, é sinal de bloqueio permanente naquele
tribunal específico (aí dá pra investigar uma solução dedicada pra ele); se forem tribunais diferentes a
cada tentativa, é só lentidão passageira de rede mesmo.

**Testado:** suíte inteira passando — 178 testes (176 de antes, menos 1 teste antigo que checava a
*quantidade* de tribunais consultados antes de parar numa tela de senha — deixou de fazer sentido sob
paralelismo, já que as 5 chamadas saem juntas — mais 3 novos: a tela de senha ainda é detectada e para a
busca corretamente independente da ordem de chegada das respostas; a mensagem final realmente nomeia os
tribunais certos quando dois deles não respondem a tempo; e o tempo limite dos 5 candidatos é mesmo menor
que o do TJSP).

**Arquivos tocados:** `app/utils/conector_esaj_publico.py` (busca paralela com `ThreadPoolExecutor`,
sessão HTTP nova por tribunal em cada chamada paralela — `requests.Session` não é garantidamente segura
entre threads —, tempo limite reduzido pros 5 candidatos, mensagem de erro nomeando os tribunais que não
responderam), `tests/test_conector_esaj_publico.py` (teste antigo de contagem de chamadas trocado por um
que testa só o resultado; 2 testes novos).

**Depois de subir esta versão:** só `git add`/`commit`/`push` normal — nenhuma coluna nova no banco, não
precisa rodar `sincronizar_schema.py`.

## -72. e-SAJ público estendido pra mais tribunais + busca automática antes de cadastrar

**Pedido:** depois de testar a seção -71 (você tentou com um número fictício e recebeu, corretamente,
"processo não encontrado"), você pediu duas coisas: (1) *"preciso que isso funcione com todos os outros
processos também e não somente o e-SAJ"* — ou seja, cobrir mais tribunais, não só TJSP; e (2)
*"quero que busque ainda antes de adicionar um novo processo"*.

Perguntei antes de construir (duas escolhas suas): tribunais → **"outros tribunais que usam e-SAJ"**
(TJMS, TJAC etc., em vez de tentar cobrir os 27 estaduais com qualquer sistema, ou só os tribunais do
seu escritório); busca antes de cadastrar → **"automática assim que o número for digitado"**, sem
precisar clicar em nada.

**Tribunais — o que descobri antes de construir, porque mudou o desenho:** fui conferir no código-fonte
do juscraper (mesma fonte da seção -71) quais tribunais além do TJSP realmente rodam a plataforma e-SAJ
— confirmei **TJAC, TJAL, TJAM, TJCE e TJMS** (o juscraper faz scraping desses 6 no total, incluindo
TJSP). Mas achei um problema ao tentar identificar automaticamente QUAL desses tribunais um número CNJ
pertence: não existe uma tabela confiável do "código de tribunal (2 dígitos) → qual estado" que eu
conseguisse confirmar com segurança — o próprio sistema já tinha essa mesma preocupação registrada em
`app/utils/tribunais_datajud.py` (decisão de não adivinhar isso, pelo risco de atribuir dados de um
processo ao tribunal errado) e uma biblioteca de terceiros que conferi
(`joaotextor/busca-processos-judiciais`) também exige que o tribunal seja informado explicitamente,
nunca adivinha só pelo número.

Por isso, a única exceção é o TJSP (código "26" já confirmado e usado desde a seção -71 — 1 requisição
direta). Para os outros 5, o conector agora **tenta cada um em sequência até achar** — a mesma estratégia
que `app/utils/conector_datajud.py` já usa pra tentar os 27 tribunais estaduais no DataJud. É mais lento
no pior caso (até 5 requisições reais a 5 tribunais diferentes), mas nunca arrisca atribuir o processo
errado a um tribunal errado — cada tribunal só "acha" um processo que realmente exista lá; encontrar uma
tela de "protegido por senha" interrompe a busca na hora (é sinal de que o processo está justamente
naquele tribunal). O TJCE precisou de um ajuste extra de TLS (o servidor dele exige uma configuração mais
permissiva, "SECLEVEL=1") — o mesmo ajuste que o próprio juscraper já documentava ter sido necessário.

Na tela do processo, o botão "Buscar dados públicos do e-SAJ" agora aparece pra qualquer número de
Justiça Estadual (antes só aparecia pra TJSP) — inclusive pra tribunais que este conector ainda não
cobre (TJMG, TJRS etc.), caso em que o clique só resulta na mensagem honesta "não encontrado em nenhum
dos tribunais e-SAJ testados", nunca um erro confuso.

**Busca automática antes de cadastrar — descoberta importante:** ao investigar onde encaixar isso, achei
que a tela "Novo processo" **já tinha** uma busca de pré-visualização (via DataJud) que roda antes de
salvar — botão "Buscar" + tecla Enter no campo do número, preenchendo os campos vazios do formulário sem
gravar nada no banco ainda (rota `governanca.consultar_cnj_preview`). Ou seja, a base do que você pediu
já existia; faltavam duas coisas: (1) ela só tentava o DataJud, nunca o e-SAJ público; (2) exigia clicar
em "Buscar" ou apertar Enter — não disparava sozinha.

Resolvi as duas: a rota de pré-visualização agora tenta o DataJud primeiro e, se não achar (ou não
estiver configurado), tenta o e-SAJ público como segunda chance — sem custo de espera perceptível, já
que o usuário ia esperar o DataJud responder de qualquer jeito. E o campo do número agora dispara a busca
sozinho, automaticamente, assim que você termina de digitar os 20 dígitos e para por um instante (~0,7s)
— o botão "Buscar" e a tecla Enter continuam funcionando do mesmo jeito, pra quem quiser forçar antes
disso. Isso vale tanto em "Novo processo" (o pedido original) quanto em "Editar processo" (já tinha o
mesmo botão/Enter ali, então ganhou a busca automática de brinde, por consistência).

**O que fica de fora, por ainda não ter uma fonte confiável:** tribunais estaduais que não usam e-SAJ
(a maioria — TJMG, TJRJ, TJRS, TJPR e outros), e qualquer segmento fora da Justiça Estadual (Federal,
Trabalhista etc.) — esses continuam cobertos só pelo DataJud, como já era.

**Testado:** suíte inteira passando — 176 testes (173 de antes + 3 novos direto na rota de
pré-visualização, cobrindo os três cenários: e-SAJ público encontra quando o DataJud falha, nenhum dos
dois encontra (mensagem combinada, citando as duas fontes tentadas), e um número fora da Justiça Estadual
nem tenta o e-SAJ). Além disso, 5 testes novos no conector em si: tenta o próximo tribunal até achar,
não encontrado em nenhum dos 5, tela de senha interrompe a busca sem tentar os demais, todos os 5
indisponíveis vira erro claro, e o TJCE usa mesmo a sessão com o ajuste de TLS.

**Arquivos tocados:** `app/utils/conector_esaj_publico.py` (catálogo dos 6 tribunais, lógica de
"tenta até achar", adaptador de TLS do TJCE), `app/routes/governanca.py` (rota de pré-visualização agora
tenta os dois conectores, função nova `_preview_json_encontrado` compartilhada), `app/__init__.py`
(filtro `eh_cnj_tjsp` renomeado pra `eh_cnj_esaj_candidato`, agora vale pra qualquer Justiça Estadual),
`app/templates/processos/detalhe.html` (texto do botão atualizado), `app/templates/processos/form.html`
(busca automática ao digitar + mensagens mencionando as duas fontes), `tests/test_conector_esaj_publico.py`
(14 testes novos).

**Depois de subir esta versão:** só `git add`/`commit`/`push` normal — nenhuma coluna nova no banco,
não precisa rodar `sincronizar_schema.py`.

## -71. Conector "e-SAJ público" (TJSP) — busca dados sem certificado, sem token, sem login

**Pedido:** você mandou o repositório `github.com/jtrecenti/juscraper` perguntando se dava pra usar
algo de lá pra buscar o processo completo (ou mais completo) e um PDF, sem precisar do certificado ou
token do advogado. Analisei o código-fonte do juscraper (não só a documentação) antes de responder,
porque a resposta é diferente pra cada parte do processo:

- **TJSP (e-SAJ), consulta de andamentos/partes em 1º grau:** o juscraper faz isso sem NENHUMA
  credencial — confirmei lendo o código, não tem login, cookie nem certificado em lugar nenhum.
  **Isso deu pra aproveitar de verdade** — é o que foi construído nesta rodada.
- **PDF dos autos de verdade (JusBR/PDPJ):** ao contrário do que parecia à primeira vista, essa parte
  do juscraper EXIGE um token JWT obtido via login SSO do próprio advogado (gov.br/certificado) — é
  exatamente o token que você queria evitar. Não é uma limitação do juscraper, é uma exigência da
  Justiça (sigilo processual/LGPD) — nenhuma ferramenta legítima contorna isso. **Isso NÃO deu pra
  aproveitar.**

Depois você perguntou especificamente se dava pra pegar os cookies do navegador do advogado (ex: se
ele logar no TJSP, buscar esse cookie e usar pra puxar tudo daquele tribunal) — expliquei que o Chrome
tem uma proteção (desde meados de 2024, "App-Bound Encryption") feita justamente pra impedir que um
programa de fora do navegador leia esses cookies, e que o e-SAJ/PJe agora exige verificação por e-mail
no login (desde março/abril de 2025), então nem o login em si dá pra automatizar silenciosamente. Você
então sugeriu usar uma extensão de navegador (tipo "Cookie-Editor") — **e essa ideia está certa**: uma
extensão lê os cookies por dentro do próprio navegador (API `chrome.cookies`), então quem descriptografa
é o Chrome mesmo antes de entregar pra extensão autorizada — isso contorna o problema do App-Bound
Encryption de um jeito diferente do que o juscraper faz (ele só suporta Firefox, que não tem essa
proteção). Essa parte (extensão + ponte com o Agente Local) ainda **não foi construída** — é coisa
maior, envolve decidir entre Native Messaging (mais "oficial", mas precisa mexer no registro do
Windows) ou um servidor local HTTP dentro do Agente Local (mais simples de testar, mas precisa validar
a origem da requisição pra não virar porta aberta pra qualquer site) — fica como próximo passo.

**O que foi implementado nesta rodada (só a parte pública, sem login):** um novo conector,
`app/utils/conector_esaj_publico.py`, que busca a página pública do processo diretamente no site do
TJSP (`esaj.tjsp.jus.br`) — sem enviar nenhum header de autorização, cookie ou certificado (testado
explicitamente: `test_consulta_publica_sem_nenhuma_credencial`). Ele extrai classe, assunto, foro,
vara, valor da causa, partes (com os advogados listados) e o histórico de movimentações, usando os
mesmos nomes de campo/IDs de HTML confirmados no código do juscraper (que é MIT, então pude conferir a
lógica exata deles). Reaproveita o MESMO pipeline de carga inicial e deduplicação por hash que o
DataJud já usa (`aplicar_carga_inicial` e `registrar_movimentacoes_capturadas`, em
`app/utils/captura_pipeline.py` — só ganharam dois parâmetros novos, `fonte_rotulo` e `origem_captura`,
com valor padrão igual ao de sempre, então nada que já existe muda de comportamento), então é seguro
rodar quantas vezes quiser: nunca duplica movimentação nem sobrescreve campo já preenchido à mão.

Na tela do processo, aparece um botão novo "Buscar dados públicos do e-SAJ (TJSP)" — só quando o número
CNJ do processo é de fato do TJSP (segmento "8", código de tribunal "26"; ver filtro `eh_cnj_tjsp` em
`app/__init__.py`) — ao lado do botão de nova tentativa via DataJud, com um aviso explicando que é uma
fonte complementar (scraping de página pública, sem contrato oficial — pode parar de funcionar se o
TJSP mudar a página, e não alcança processos com senha/segredo de justiça, que o conector detecta e
sinaliza como erro específico em vez de travar).

**Limitações importantes, pra não vender como mais do que é:**
1. Só cobre TJSP (código de tribunal 26) — outros tribunais no e-SAJ (TJMS, por exemplo, da seção -64)
   não são atendidos por este conector.
2. Só 1º grau, e só o que a página pública mostra — não é o PDF dos autos completos (isso continua
   exigindo login, como explicado acima).
3. Processo com senha/segredo de justiça é detectado e rejeitado com uma mensagem clara (não trava o
   sistema, mas também não consegue os dados — é o próprio TJSP bloqueando quem não tem acesso).
4. É scraping de página HTML pública, sem contrato/SLA da Justiça — se o TJSP mudar o layout da página,
   o conector para de funcionar até eu ajustar o parsing.

**Testado:** suíte inteira passando — 168 testes (159 de antes + 9 novos: parsing completo de uma
página de processo real, rejeição de número CNJ de outro tribunal, confirmação de que NENHUMA
credencial é enviada, detecção de processo protegido por senha, processo não encontrado, erro de rede
virando mensagem clara, os dois métodos da interface `ConectorCaptura` que este conector não implementa
(monitoramento por OAB e due diligence — fora de escopo aqui) levantando erro explícito em vez de falha
silenciosa, e os dois testes de ponta a ponta da rota nova, incluindo o caso de falha registrando log).

**Arquivos tocados:** `app/utils/conector_esaj_publico.py` (novo), `app/utils/captura_pipeline.py`
(dois parâmetros novos, retrocompatíveis), `app/routes/governanca.py` (rota nova
`tentar_captura_esaj`), `app/__init__.py` (filtro `eh_cnj_tjsp`),
`app/templates/processos/detalhe.html` (botão novo), `requirements.txt` (adicionado
`beautifulsoup4`, usado pro parsing do HTML), `tests/test_conector_esaj_publico.py` (novo).

**Depois de subir esta versão:** só `git add`/`commit`/`push` normal — não tem coluna nova no banco
(`origem_captura` já existe, é só um texto livre), então **não precisa** rodar
`sincronizar_schema.py` desta vez.

## -70. Geração do .msix (Microsoft Store) — construído, falta você cadastrar 3 valores no GitHub

**Pedido:** *"já fiz o cadastro [de desenvolvedor], agora conseguiria gerar o .msix do aplicativo para
que eu possa colocar na loja da Microsoft?"* — sequência da pesquisa da seção -67 (que confirmou que dá
pra fazer isso de graça, e que a própria Microsoft reassina o pacote depois da certificação, resolvendo
o bloqueio do SmartScreen/Smart App Control sem comprar certificado nenhum).

**O que eu consegui construir sozinho (sem precisar de nada seu):**

- `agente_local_jc/build/gerar_assets_msix.py` (novo): gera as 3 imagens que o pacote MSIX exige
  (`Square44x44Logo.png`, `Square150x150Logo.png`, `StoreLogo.png`), reaproveitando o mesmo desenho do
  ícone de sempre (círculo azul-marinho com "J" branco) — testei aqui e as 3 saem nítidas mesmo no
  tamanho pequeno (44×44). Roda automaticamente no GitHub Actions, igual o `gerar_icone.py` já faz hoje.
- `agente_local_jc/build/AppxManifest.xml.template` (novo): o "manifesto" que todo pacote MSIX precisa
  ter — mas com 4 marcadores (`__IDENTITY_NAME__`, `__PUBLISHER__`, `__PUBLISHER_DISPLAY_NAME__`,
  `__VERSION__`) que o workflow do GitHub Actions preenche automaticamente antes de empacotar.

**O que eu NÃO tenho e não posso inventar — precisa vir do SEU cadastro no Partner Center:** o "Nome do
identity" e o "Publisher" (uma string tipo `CN=XXXXXXXX-XXXX-...`) só existem depois que você reserva o
nome do app lá dentro do Partner Center — são identificadores únicos ligados à SUA conta de
desenvolvedor, não algo que eu possa gerar ou adivinhar. Errar esse valor faz o pacote ser recusado na
hora de submeter à Store.

**Como resolver isso sem precisar me mandar esses valores no chat:** em vez de eu colocar esses 3
valores direto no código (o que exigiria eu mexer de novo no arquivo do workflow toda vez que algo
mudasse), configurei o workflow pra ler 3 "repository variables" do próprio GitHub — você mesmo cadastra
isso direto na tela do GitHub, sem precisar me colar nada aqui:

1. No repositório, vá em **Settings → Secrets and variables → Actions → aba "Variables"** (não é a aba
   "Secrets") → **"New repository variable"**.
2. Cadastre 3 variáveis, com os valores exatos que aparecem na reserva do app no Partner Center:
   - `MSIX_IDENTITY_NAME` → Partner Center → seu app → "Configuração de pacote de aplicativo" → campo
     "Nome do identity" (algo como `12345SeuNome.AgenteJusControl`).
   - `MSIX_PUBLISHER` → mesma tela, campo "Publisher" (uma string `CN=XXXXXXXX-XXXX-...`).
   - `MSIX_PUBLISHER_DISPLAY_NAME` → Partner Center → Configurações da conta → Perfil da conta → "Nome
     do publicador".

Sem essas 3 variáveis cadastradas, o workflow continua funcionando exatamente como hoje — o passo do
MSIX é **pulado automaticamente**, e o instalador `.exe` de sempre continua sendo gerado normalmente.
Depois de cadastrar as 3 variáveis, a PRÓXIMA vez que o workflow rodar (nova tag `agente-vX.Y.Z`, ou
manualmente pela aba "Actions") já vai gerar também um `JusControlAgente.msix`, publicado junto do
instalador de sempre (como artefato do workflow e, se veio de uma tag, também anexado à Release).

**Mudança no workflow (`.github/workflows/build-agente-local.yml`):** esse arquivo é um dos que não
consigo escrever direto na sua pasta (junto com qualquer coisa em `.github/**`, e arquivos `.env`) — a
ferramenta que uso pra sincronizar arquivos recusa esse caminho de propósito. Por isso, o conteúdo NOVO
COMPLETO desse arquivo vem no final desta mensagem, pra você colar substituindo o arquivo atual (o
`agente_local.spec` e o `instalador.iss` não mudaram, só o `.yml` do workflow).

**Não testado de ponta a ponta:** este ambiente não tem Windows nem o Windows SDK/`makeappx.exe`
disponíveis, então não consigo rodar o empacotamento MSIX aqui de verdade — testei só a geração das
imagens (script rodou e as 3 saíram corretas) e validei a sintaxe do `.yml` novo. A primeira execução
real no GitHub Actions, depois de você cadastrar as 3 variáveis, é a validação de fato; se der erro, o
log do passo "Empacotar o MSIX (makeappx.exe)" mostra o motivo exato.

**Depois de gerar o `.msix` com sucesso, os próximos passos (fora do meu alcance, do lado da Microsoft)
são:** enviar esse arquivo pro Partner Center como um envio novo do seu app, esperar a certificação
(pode levar de horas a poucos dias), e então baixar o link de instalação — nesse momento é que a
Microsoft reassina o pacote e o bloqueio do SmartScreen/Smart App Control desaparece de vez, sem custo.

**Arquivos tocados:** `agente_local_jc/build/gerar_assets_msix.py` (novo),
`agente_local_jc/build/AppxManifest.xml.template` (novo), `.github/workflows/build-agente-local.yml`
(conteúdo completo entregue como texto, pra você colar).

## -69. Timbrado do escritório (logo) nos PDFs gerados — implementado nesta rodada

**Pedido:** *"quero ter uma opção do advogado colocar o timbrado do escritório dele no sistema, isso
seria possível?"*

**Perguntei três coisas antes de construir, pra não ter que refazer depois:**

1. O timbrado é uma logo pequena somada ao texto que já existe (nome/CNPJ/endereço), ou uma imagem
   única substituindo o cabeçalho inteiro? → **Você escolheu: logo + texto atual.**
2. Vale pro escritório inteiro (Empresa) ou pode variar por unidade/filial? → **Você escolheu: por
   escritório inteiro** (todas as unidades da mesma empresa usam a mesma logo).
3. Onde deve valer, além do recibo em PDF que já existe hoje? → **Você escolheu: recibo + preparar
   para futuros documentos** (ou seja, construir de um jeito reaproveitável, não só colado no recibo).

**O que foi implementado:** em "Minha empresa → Integrações" (`/minhas-integracoes`), qualquer usuário
com papel "admin" da empresa agora tem um terceiro cartão, "Timbrado do escritório", com upload de
PNG/JPG (até 3 MB), pré-visualização da logo cadastrada e botão para remover. A lógica de
validar/salvar/desenhar ficou num módulo novo e isolado (`app/utils/timbrado.py`) — não depende de
nada específico do recibo, exatamente para poder ser reaproveitado por qualquer gerador de PDF futuro
do sistema, como você pediu no item 3. Hoje só o recibo em PDF do Financeiro chama isso
(`app/routes/financeiro.py::recibo`), trocando o bloco que desenhava só o texto do cabeçalho por uma
chamada a `timbrado.desenhar_cabecalho(...)`, que desenha a logo à esquerda (altura fixa, largura
proporcional à imagem original) e o texto ao lado dela — sem nenhuma logo cadastrada, o PDF sai
IDÊNTICO a como sempre foi (nenhuma mudança visual pra quem não configurar nada).

A logo é guardada como arquivo em disco (mesmo padrão já usado para documentos de processo — ver
`app/routes/processos.py::add_documento`), nunca no banco; só o nome do arquivo salvo fica numa coluna
nova, `Empresa.logo_arquivo` (nullable, não exige migração — basta rodar `sincronizar_schema.py` depois
do deploy). A validação rejeita extensão fora de PNG/JPG, arquivo maior que 3 MB, e também um arquivo
com extensão certa mas que não é uma imagem de verdade por dentro (usando `Image.open(...).verify()`
do Pillow) — sem deixar arquivo temporário esfarrapado pra trás em nenhum desses casos de rejeição.

**Testado:** suíte inteira (`pytest -q`) passando — 159 testes (154 de antes + 5 novos cobrindo upload
válido, extensão inválida, conteúdo falso, remoção, e que só admin da própria empresa consegue
configurar isso). Além disso, gerei e RENDERIZEI (não só validei os bytes) três recibos de teste em PNG
via `pdftoppm`, pra confirmar visualmente o layout: (1) caso normal, logo + nome/CNPJ/endereço lado a
lado, sem sobreposição; (2) caso sem logo, confirmando que fica byte-a-byte no mesmo estilo de sempre
(regressão zero); (3) caso extremo, logo bem larga e baixa com pouquíssimo texto ao lado, confirmando
que o cursor vertical (`y`) do resto do PDF nunca fica sobreposto pela logo mesmo quando ela ocupa mais
altura que o texto.

**Arquivos tocados:** `app/models/empresa.py` (coluna nova `logo_arquivo`), `app/utils/timbrado.py`
(novo), `app/routes/integracoes.py` (3 rotas novas: salvar/remover/servir a imagem do timbrado),
`app/routes/financeiro.py` (recibo passa a chamar `timbrado.desenhar_cabecalho`),
`app/templates/integracoes/minhas_integracoes.html` (cartão novo), `requirements.txt` (adicionado
`Pillow`, necessário pro reportlab conseguir desenhar imagens PNG/JPEG no PDF), `tests/test_timbrado.py`
(novo).

**Depois de subir esta versão:** (1) `git add`/`commit`/`push` normal (isto é do lado do servidor, não
do instalador — não precisa de tag nem de rebuild do instalador do agente local); (2) rodar
`python sincronizar_schema.py` dentro do container do EasyPanel, pra criar a coluna nova
`Empresa.logo_arquivo`.

## -68. Aba do processo voltando pra "Andamentos" DE NOVO — a correção da seção -60 nunca tinha funcionado de verdade

**Pedido:** você reportou de novo que qualquer ação dentro de um processo redireciona pra aba
"Andamentos", mesmo depois da correção que eu tinha entregue lá na seção -60.

**Causa raiz — a correção anterior nunca funcionou, e eu não tinha percebido:** o script que restaura
a aba salva fica dentro do bloco principal de conteúdo da página (`detalhe.html`), que o `base.html`
renderiza ANTES do `<script src=".../bootstrap.bundle.min.js">` (lá embaixo, perto do fechamento do
`<body>`). Como esse script roda IMEDIATAMENTE, no momento em que o navegador lê aquela linha, o
`window.bootstrap` ainda não existe — o Bootstrap só é carregado mais adiante no HTML. A condição
`if (botaoAlvo && window.bootstrap)` falhava sempre, silenciosamente (sem erro nenhum no console), e a
aba nunca era restaurada de verdade — só a PARTE de guardar no `sessionStorage` funcionava, guardando o
valor certo, mas sem nunca chegar a usá-lo. Ou seja, o bug nunca tinha sido corrigido, apesar de eu ter
achado que sim.

**Correção:** a restauração agora roda dentro de um listener de `DOMContentLoaded` — esse evento só
dispara depois que TODOS os scripts síncronos anteriores (inclusive o do Bootstrap) já terminaram de
rodar, não importa a posição deste `<script>` no HTML.

**Testado de um jeito que realmente prova o bug e a correção, não só leitura de código:** montei uma
reprodução mínima (HTML com as mesmas duas abas + um dublê do Bootstrap que expõe a mesma API usada
pelo código real — `window.bootstrap.Tab` e o evento `shown.bs.tab`) e rodei num Chromium de verdade
via Playwright: clicar na aba "Documentos" e recarregar a página. Com o código ANTIGO (seção -60), a
aba sempre voltava pra "Andamentos" depois de recarregar — reproduzindo exatamente o bug relatado. Com
o código NOVO (`DOMContentLoaded`), a aba "Documentos" continuava ativa depois de recarregar. Também
rodei a suíte inteira (`pytest -q`, 154 passando) — e ela pegou um erro REAL que eu tinha introduzido
sem querer nesta mesma correção: o comentário explicativo citava literalmente `{% block conteudo %}`
como exemplo de texto, e o Jinja não entende comentário de JavaScript — ele processa esse texto como se
fosse uma tag de verdade, quebrando a renderização da página inteira com `TemplateSyntaxError`. Troquei
a citação por texto sem `{%`/`%}` antes de mandar pra você; a suíte confirma que a página volta a
renderizar normalmente.

**Arquivo tocado:** `app/templates/processos/detalhe.html` (só este).

## -67. Pesquisa: dá pra resolver o bloqueio (seção -61) via MSIX + Microsoft Store, de graça?

**Pergunta:** você perguntou se "MSIX + Microsoft Store" resolveria o bloqueio do instalador de forma
gratuita, em vez de comprar um certificado de assinatura de código.

**Resposta: sim, é o caminho mais barato que existe pra resolver isso de vez** (não só um contorno
temporário como a seção -66) — e também corrige uma informação que eu tinha te dado errada na seção
-61: eu tinha recomendado certificado **EV** como o único jeito de ter confiança IMEDIATA no
SmartScreen/Smart App Control. Isso deixou de ser verdade — confirmei agora que, desde 2024, a
Microsoft removeu o tratamento especial que certificados EV tinham; hoje EV e OV se comportam do
mesmo jeito (constroem reputação aos poucos, sem confiança imediata). Ou seja, nenhum dos dois
certificados pagos resolve o problema de forma imediata — só o caminho da Store resolve isso de
graça e na hora.

**Como funciona:** ao publicar o app empacotado como **MSIX** na Microsoft Store, a própria Microsoft
**reassina o pacote depois da certificação** — o app passa a herdar a reputação da Microsoft
automaticamente, sem SmartScreen nem Smart App Control bloquearem nada, sem precisar comprar
certificado nenhum. Isso só vale pro formato MSIX — se fosse só submeter o `.exe`/instalador atual
(Inno Setup) pra Store sem empacotar como MSIX, a Microsoft NÃO reassina, e o bloqueio continua.

**Custo confirmado:** desde setembro/2025, a Microsoft **zerou a taxa de cadastro de desenvolvedor
individual** (antes custava US$19 único) — cadastro em storedeveloper.microsoft.com, verificação de
identidade com documento oficial + selfie (não tem custo, é só um processo de verificação), e depois
acesso ao Partner Center pra publicar. Nenhum custo recorrente depois disso.

**Não precisa deixar público:** dá pra publicar como "não detectável" (o app não aparece em busca nem
navegação da Store, só quem tiver o link direto consegue instalar) — perfeito pro piloto de agora, só
você e o advogado colega. Tem uma opção ainda mais restrita ("Público-alvo privado", com grupo de
usuários específico exigindo conta pessoal da Microsoft) pra quando quiser controlar exatamente quem
instala, mas essa é mais burocrática — não recomendo pro piloto atual.

**O que precisa acontecer pra usar esse caminho (trabalho real, não é só configuração):**
1. **Você** precisa criar a conta gratuita de desenvolvedor (isso exige SEU documento oficial + selfie
   — não é algo que eu consigo fazer por você).
2. **Eu preciso mudar o pipeline de build** (`.github/workflows/build-agente-local.yml`) pra também
   gerar um pacote `.msix` (hoje só gera o `.exe` via PyInstaller + Inno Setup) — isso envolve criar um
   manifesto do app (`AppxManifest.xml`) e empacotar com as ferramentas do Windows SDK
   (`MakeAppx.exe`), é trabalho de verdade, não é imediato.
3. **Submeter pro Partner Center** e esperar a certificação da Microsoft (normalmente leva alguns dias
   — não é instantâneo como o `git push` do instalador atual).

**Minha recomendação:** vale a pena investir nisso pra resolver de vez (é literalmente grátis e
melhor que qualquer certificado pago), mas não é uma correção de 5 minutos — enquanto isso, a
seção -66 (desligar o Smart App Control temporariamente) continua sendo o jeito mais rápido de destravar
o teste com o advogado colega HOJE. Me avise se quer que eu comece a preparar o empacotamento MSIX no
pipeline enquanto você cria a conta de desenvolvedor.

Fontes consultadas: [Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options), [Free developer registration for individual developers](https://learn.microsoft.com/en-us/windows/apps/publish/whats-new-individual-developer), [Choose visibility options for MSIX app](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/visibility-options), [Windows Apps PSA: EV Certs do not grant immediate reputation anymore](https://www.todesktop.com/blog/posts/windows-apps-psa-ev-certs-do-not-grant-immediate-reputation-anymore)

## -66. Contorno temporário pro bloqueio do Smart App Control (pendência -61) — pra você e o advogado colega testarem o instalador

**Pedido:** o instalador voltou a ser bloqueado ("CreateProcess falhou; código 4551 — Uma política de
Controle de Aplicativo bloqueou este arquivo") ao tentar instalar a versão nova, e o advogado colega
provavelmente vai bater no mesmo bloqueio na máquina dele. Você pediu um jeito de corrigir isso, pelo
menos por um tempo.

**Boa notícia que muda o que eu tinha escrito antes (seção -61):** eu tinha anotado que desligar o
Smart App Control só voltava a ligar com reinstalação limpa do Windows — isso era verdade, mas
**mudou**. Pesquisei agora e confirmei: desde a atualização cumulativa **KB5083769** (14/abril/2026),
já disponível pra qualquer Windows 11 atualizado (não é mais só canal Insider), desligar e religar o
Smart App Control voltou a ser **reversível**, sem precisar reinstalar nada. Isso muda o cálculo: dá
pra desligar só pra instalar, e religar depois — genuinamente temporário, exatamente o que você pediu.

**Passo a passo (pra você e pra passar pro advogado colega):**
1. Abra **Segurança do Windows** (pesquise no menu Iniciar, ou Configurações → Privacidade e segurança
   → Segurança do Windows → Abrir Segurança do Windows).
2. Vá em **Controle de aplicativos e navegador** ("App & browser control").
3. Role até a seção **Smart App Control** e clique em **Configurações do Smart App Control**.
4. Mude de "Ativado"/"Avaliação" para **"Desativado"** — pode pedir confirmação de administrador (UAC)
   e, às vezes, reiniciar o computador pra valer de fato.
5. Rode o instalador (`JusControlAgente-Setup.exe`) de novo — agora deve instalar sem o erro 4551.
6. **Depois de instalar** (recomendado, já que agora é reversível): volte na mesma tela e religue o
   Smart App Control, pra não deixar a proteção desligada por mais tempo que o necessário.

**Se o botão estiver acinzentado/travado:** normalmente é porque a conta não é administradora da
máquina, ou porque uma política de empresa (GPO/Intune) está fixando essa configuração — nesses casos
não dá pra contornar por aqui, e a saída continua sendo rodar direto da fonte (`python tray_app.py`,
já documentado na seção -61) ou avançar com o certificado de assinatura de código.

**Isso NÃO substitui o certificado de assinatura de código** (ver opções na seção -61) — continua
sendo a única solução que funciona pra distribuir o instalador em escala, sem pedir pra cada advogado
mexer numa configuração de segurança do Windows. Serve bem pro momento atual (só você + 1 colega
testando), mas não escala pro escritório inteiro.

Fontes consultadas: [Microsoft confirms you can soon disable Smart App Control without reinstalling Windows 11](https://www.windowslatest.com/2025/12/16/microsoft-confirms-you-can-soon-disable-smart-app-control-without-reinstalling-windows-11/), [Smart App Control in Windows 11 can now be re-enabled without reinstalling](https://blog-en.topedia.com/2026/04/smart-app-control-in-windows-11-can-now-be-re-enabled-without-reinstalling/)

## -65. Janela "Avançado" virou botões separados (Certificado, PJe, Projudi, e-SAJ) em vez de uma lista comprida

**Pedido:** depois de ver a seção "Avançado" com PJe, Projudi e e-SAJ todos empilhados numa lista só
(print anexado), você pediu pra cada um virar uma janela separada, aberta por um botão — pra não ficar
uma tela gigante de rolar.

**O que mudou em `config_gui.py`:** a seção "Avançado" (ainda recolhida por padrão, continua atrás do
mesmo botão "▸ Mostrar configurações avançadas") agora mostra só 4 botões — **Certificado digital...**,
**PJe...**, **Projudi...** e **e-SAJ...** — cada um com um texto ao lado ("configurado" / "não
configurado") indicando se aquele tribunal já tem algo preenchido. Clicar em qualquer um abre uma
janela pequena só com os campos daquele assunto, com os próprios botões "OK" (aplica o que foi digitado
naquela janela) e "Cancelar" (fecha sem aplicar). Os valores só são gravados em disco de verdade quando
você clica em "Salvar" na janela principal — igual sempre foi, isso não mudou.

**Bug real encontrado e corrigido ANTES de te mandar isto:** a primeira versão que escrevi usava
`sub.eval("tk::PlaceWindow . center")` pra centralizar cada janelinha nova — só que esse método
(`.eval`) só existe na janela raiz (`Tk`), não nas janelas secundárias (`Toplevel`) que uso aqui; isso
ia travar o programa com um erro (`AttributeError`) assim que você clicasse em qualquer um dos 4
botões. Consegui reproduzir isso de verdade neste ambiente (instalei o `tkinter` com suporte a tela
virtual, coisa que não tinha antes) e só entreguei depois de corrigir e confirmar que voltou a
funcionar.

**Testado:** desta vez de um jeito bem mais completo que o normal, porque consegui rodar o `tkinter`
DE VERDADE aqui (com uma tela virtual, `Xvfb`) em vez de só simular com dublês como nos ajustes
anteriores do agente — um script abre a janela principal de verdade, clica em "Mostrar configurações
avançadas", confirma que os 4 botões existem (e que a lista antiga de campos soltos sumiu), abre a
janela do e-SAJ, confirma que ela já vem com o valor previamente preenchido, fecha com "OK", e depois
clica em "Salvar" e confere que tudo foi gravado certo no arquivo de configuração — inclusive esse
mesmo bug do `.eval()` foi pego por este teste, não por inspeção manual do código. `py_compile` e a
suíte inteira (`pytest -q`, 154 passando) também conferidos, sem regressão.

**Arquivo tocado:** `agente_local_jc/config_gui.py` (só este).

## -64. Conector do e-SAJ (TJMS, TJSP e outros) — para testar com um processo real do TJMS

**Pedido:** depois de confirmar que o Agente Local ficou mais rápido, você pediu pra habilitar um
teste com um processo real do TJMS, com um advogado colega testando.

**Pesquisa feita antes de construir:** o TJMS não usa PJe nem Projudi — usa **e-SAJ** (sistema da
Softplan, o mesmo usado pelo TJSP), confirmado pelo domínio `esaj.tjms.jus.br` que hospeda a consulta
processual pública do tribunal. Isso é diferente do PJe (protocolo MNI documentado nacionalmente pelo
CNJ) e do Projudi (mesma obrigação legal de expor o MNI): **não encontrei nenhuma documentação
pública confirmando que o e-SAJ expõe um webservice MNI/SOAP** para consulta externa — a arquitetura
visível do e-SAJ (plugin de navegador "Web Signer" para login com certificado no portal, sistema
próprio de notificação chamado "Sistema Push") sugere um desenho baseado em portal web, bem diferente
da API SOAP limpa do PJe. Perguntei como prosseguir e você escolheu **"Tentar via MNI mesmo assim
(rápido e barato)"** — ciente de que pode não funcionar.

**O que foi construído:** um terceiro conector, `conectores/esaj.py` (slug `esaj_sp`), seguindo
exatamente o mesmo padrão do Projudi — reaproveita a lógica compartilhada de `conectores/mni_soap.py`,
exige a URL do WSDL configurada manualmente (sem tentar adivinhar, como o Projudi), e tem um aviso bem
grande no próprio arquivo deixando claro que este é o piloto MAIS incerto dos três. Também dá pra
configurar pela mesma janela "Configurar..." → "Avançado" (ID consultante, senha, URL do WSDL), igual
aos outros dois tribunais.

**Se o teste com o processo do TJMS der "Não foi possível carregar o WSDL" (ou erro de conexão
parecido):** isso confirma que o e-SAJ realmente não expõe esse serviço — não é bug do agente. Nesse
caso o caminho certo passa a ser automação de navegador (abrir o portal do e-SAJ de verdade, logar com
o certificado, baixar o PDF pela tela mesmo) — que é bem mais trabalhoso que MNI/SOAP e ainda não foi
construído; me avise se o teste falhar dessa forma que eu já preparo essa alternativa.

**Testado:** `py_compile` em todos os arquivos tocados; suíte inteira de testes (`pytest -q`) — 154
passando, incluindo um teste novo (`test_solicitar_busca_autos_com_esaj`); script de verificação
isolado (mockando o cliente SOAP, já que este sandbox não tem `zeep`/Windows) cobrindo: conector
`esaj_sp` monta certo, busca completa (histórico + PDF consolidado) funciona com uma resposta SOAP
simulada, e erro claro quando falta a URL do WSDL. **Nenhuma chamada real foi feita contra o TJMS** —
só o teste do advogado colega, com um processo de verdade, vai confirmar se o e-SAJ aceita isso.

**Arquivos tocados:** `conectores/esaj.py` (novo), `registro_conectores.py`, `config.py`,
`config_store.py`, `config_gui.py`, `tray_app.py`, `app/utils/tribunais_conectores.py`,
`tests/test_agente_local.py`, `README.md`.

## -63. Janela "Configurar Agente Local" travando de vez (não fecha no OK nem no X)

**Pedido:** depois de clicar em "Testar conexão" (que confirmou "Conectado como WILKER BRUNO..."),
a janela de configuração travou de vez — nem "OK" na caixa de confirmação, nem o X da janela,
respondiam mais.

**Causa raiz — bug real, não confusão do usuário:** `tray_app.py` tinha uma suposição errada,
inclusive documentada no próprio comentário do código antigo ("funciona porque cada chamada cria e
destrói o próprio Tk() isoladamente"). No Windows, o `pystray` despacha cada clique no menu do ícone
(inclusive "Configurar...") numa THREAD PRÓPRIA dele, diferente da thread principal do programa. O
`tkinter` (biblioteca da janela de configuração) não é seguro fora da thread onde seu loop principal
já está rodando — abrir uma janela dele numa thread diferente trava de forma silenciosa no Windows
(sem erro nenhum, sem fechar em nada) exatamente como você viu. Isso não apareceu antes porque a
PRIMEIRA vez que a janela abre (quando não há configuração salva ainda) acontece direto na thread
principal, antes do ícone da bandeja existir — só quebra ao reabrir pelo menu "Configurar..." DEPOIS
que o ícone já está rodando, que foi exatamente o que você fez.

**Correção:** reestruturei `tray_app.py` pra ter uma única thread dona de qualquer janela do tkinter
(a THREAD PRINCIPAL, do início ao fim do programa) — cliques no menu do ícone (que rodam numa thread
diferente) agora só colocam um pedido numa fila (`_fila_gui`); quem de fato abre a janela é sempre a
thread principal, processando essa fila (`_bombear_fila_gui`). O ícone da bandeja em si passou a
rodar na própria thread dele. Resultado prático: nenhuma mudança visível pra você — só que agora
"Configurar..." reabre a janela sem travar, não importa quantas vezes.

**Testado:** não dá pra testar pystray/tkinter de verdade neste sandbox (nenhum dos dois existe
aqui) — simulei a lógica da fila com os dois módulos substituídos por dublês (`unittest.mock`),
confirmando que (1) o clique no menu só enfileira o pedido, nunca chama a janela direto; e (2) a
thread principal processa o pedido enfileirado e para corretamente quando "Sair" é sinalizado (sem
travar esperando pra sempre). `py_compile` também conferido.

**⚠️ Se a janela travada ainda estiver aberta na sua tela agora:** feche o processo Python travado
pelo Gerenciador de Tarefas (Ctrl+Shift+Esc → aba "Detalhes" ou "Processos" → procure "python.exe" ou
"pythonw.exe" → Finalizar tarefa) — clicar em OK/X não vai funcionar nele, é o processo antigo, sem
esta correção. Depois de aplicar o arquivo novo, rode `tray_app.py` de novo.

## -62. Agente Local abrindo devagar e mais de uma instância ao mesmo tempo

**Pedido:** rodando `python tray_app.py` direto da fonte (contornando o bloqueio da seção -61), você
reportou que a abertura estava "bem lenta" e que abriam DUAS janelas de configuração ao mesmo tempo
(print mostrando duas janelas "Configurar Agente Local" sobrepostas, e mais de um ícone verde na
bandeja).

**Causa da lentidão:** `tray_app.py` importa `motor.py`, que importa `registro_conectores.py`, que
importa os DOIS conectores de tribunal — e cada um deles importava `zeep` (que carrega `lxml` e
outras libs pesadas) e `certificado.py` importava `cryptography` — tudo isso **no topo do arquivo**,
ou seja, essa importação pesada rodava toda vez que o agente abria, mesmo que você não fosse buscar
autos de nenhum tribunal naquele momento — só de abrir o ícone da bandeja. Numa venv nova (como a que
você acabou de criar), sem essas libs ainda "aquecidas" em cache (e possivelmente com antivírus
escaneando cada arquivo novo lido), isso é visível como alguns segundos de janela em branco, sem
resposta.

**Causa das instâncias duplicadas:** o agente não tinha nenhuma trava contra abrir duas vezes ao mesmo
tempo — rodar `python tray_app.py` de novo enquanto a primeira instância ainda estava subindo (lenta,
pelo motivo acima) abre uma segunda instância inteira, com o próprio ícone e a própria janela.

**Correção:**
1. **Import pesado adiado** (`conectores/mni_soap.py`, `motor.py`) — `zeep` e `cryptography` agora só
   são importados na primeira vez que uma busca de autos é REALMENTE processada (dentro de
   `processar_uma_tarefa`), não mais na abertura do agente. Confirmei que `import motor` sozinho não
   carrega mais nenhuma das duas (`'zeep' in sys.modules` e `'cryptography' in sys.modules` ficam
   `False` até o primeiro uso de verdade).
2. **Trava de instância única** (`tray_app.py`) — usa um mutex nomeado do próprio Windows (via
   `ctypes`, sem dependência nova) pra impedir uma segunda instância de abrir enquanto a primeira
   ainda está rodando; se você tentar abrir de novo, aparece um aviso ("O Agente Local já está
   rodando — veja o ícone perto do relógio do Windows") em vez de duplicar tudo.

**Testado:** `import motor` isolado, medindo tempo e checando `sys.modules` (confirma que `zeep` e
`cryptography` não carregam mais no import) — e o script de verificação da seção -59/-60 (mockando o
Client do zeep) rodado de novo, confirmando que o comportamento dos conectores não mudou com o import
adiado. A trava de instância única é Windows-only (usa `ctypes.windll`) — não dá pra testar de
verdade neste sandbox Linux; só o `py_compile` foi verificado aqui.

## -61. ⚠️ PENDÊNCIA IMPORTANTE, NÃO RESOLVIDA: instalador do Agente Local bloqueado pelo Smart App Control do Windows

**O que aconteceu:** ao rodar o instalador atualizado (`JusControlAgente-Setup.exe`), o Windows
bloqueou a execução do `.exe` recém-instalado com "Uma política de Controle de Aplicativo bloqueou
este arquivo" (Windows Security: "Parte deste aplicativo foi bloqueado... não é possível confirmar
quem publicou JusControlAgente.exe").

**Causa raiz:** o `.exe` que o PyInstaller/Inno Setup geram NÃO é assinado digitalmente (não temos
certificado de assinatura de código) — o **Smart App Control** do Windows 11 (diferente do
SmartScreen clássico, que pelo menos oferece um botão "Executar assim mesmo") bloqueia binários não
assinados/sem reputação de forma definitiva, sem opção de liberar arquivo por arquivo. **Isso não é
um bug do código do agente — é uma barreira de distribuição que provavelmente vai afetar QUALQUER
advogado que for instalar, não só esta máquina.**

**Decisão tomada por enquanto:** perguntei como prosseguir; você escolheu **rodar direto da fonte
(`python tray_app.py`) por agora**, pra não travar o teste do conector Projudi/PJe — resolver a
assinatura digital do instalador fica como pendência separada, pra decidir com calma (tem custo de
certificado + configuração; ver opções abaixo). Rodar `tray_app.py` direto do Python não esbarra
nesse bloqueio (o `python.exe` já é um binário assinado/confiável) e usa o MESMO arquivo de
configuração (`%APPDATA%\JusControlAgente\config.json`) que o instalador já grava — ou seja, não
precisa reconfigurar nada, o token/URL já salvos continuam valendo.

**Opções reais para resolver de vez (nenhuma aplicada ainda — decidir com calma, é decisão de
custo/prioridade, não técnica):**
1. **Certificado de assinatura de código EV** (Extended Validation, com token de hardware) — o único
   que dá reputação IMEDIATA no SmartScreen/Smart App Control, sem período de "aquecimento". Custo
   típico: ~US$300–600/ano (DigiCert, SSL.com, Sectigo, entre outros), mais burocracia de validação
   de identidade jurídica da empresa.
2. **Certificado de assinatura "padrão" (OV)** — mais barato (~US$100–300/ano), mas o Windows só
   passa a confiar depois de um período de reputação acumulada (milhares de downloads/execuções) —
   ou seja, mesmo assinado, pode continuar sendo bloqueado pelo Smart App Control por um tempo.
3. **Desativar o Smart App Control** em cada máquina que for instalar — não é uma solução de
   distribuição (cada advogado teria que fazer isso na própria máquina). ⚠️ **Atualização
   (ver seção -66 abaixo):** a informação de que isso só voltava a ligar com reinstalação limpa do
   Windows estava correta até o fim de 2025, mas mudou — desde a atualização cumulativa de abril/2026
   (KB5083769), desligar e religar o Smart App Control passou a ser reversível, sem reinstalar nada.
   Isso torna essa opção viável como solução TEMPORÁRIA (piloto com poucos advogados), o que não era
   antes — mas continua não sendo uma solução de distribuição em escala.

**Próximo passo, quando você quiser retomar isso:** decidir se vale investir num certificado EV antes
de distribuir o instalador pra outros advogados do escritório (rodar da fonte é aceitável só pra uso
seu, técnico, de teste).

## -60. Aba do processo voltando pra "Andamentos" depois de qualquer ação + aviso importante sobre o instalador do Agente Local

**Pedido:** "toda vez que eu faço alguma função dentro de um processo ele muda para a aba
andamentos" — reproduzido com "Solicitar busca" na aba Documentos: ao clicar, a página recarrega e
volta pra aba Andamentos, mesmo a ação tendo sido na aba Documentos.

**Causa:** `app/templates/processos/detalhe.html` sempre marcava a aba "Andamentos" como `active` no
HTML — qualquer ação dentro do processo (registrar andamento, solicitar busca de autos, cadastrar
prazo, anexar documento, etc., em qualquer aba) faz um POST no servidor seguido de redirect pra
`/processos/<id>` (um GET novo, página carregada do zero), e essa nova carga sempre voltava pra aba
marcada como `active` no HTML — Andamentos, não importa de qual aba a ação partiu.

**Correção:** adicionei um script na própria página (`detalhe.html`) que lembra, no `sessionStorage`
do navegador (só desta aba/janela, não sincroniza com nada nem com o servidor), qual aba do processo
estava aberta por último — sempre que você clica numa aba, ela fica guardada; ao carregar a página de
novo (inclusive depois de um redirect de qualquer ação), a aba guardada é reaberta automaticamente.
Corrige o problema para QUALQUER ação em QUALQUER aba (Andamentos, Prazos, Audiências, Documentos,
Governança, Cofre de senha, Análise IA) — não foi uma correção pontual só do botão "Solicitar busca".

**⚠️ Sobre os erros no log que você mandou junto — leia com atenção:** os 3 erros do log
(`pje..jus.br` de novo, e "Conector 'projudi' ainda não está implementado") são **esperados** e não
indicam que a correção da seção -59 não funcionou — eles indicam que o **Agente Local instalado na
sua máquina ainda é a versão ANTIGA**, de antes da seção -59. Isso é diferente do resto do sistema:

- O **servidor** (JusControl no EasyPanel) atualiza quando você faz `git push` e o EasyPanel builda
  de novo — é rápido, alguns minutos.
- O **Agente Local instalado** (o `.exe` rodando na sua máquina, com ícone na bandeja) é um programa
  compilado à parte, separado do servidor — ele só atualiza quando você gera um instalador NOVO (tag
  Git nova → GitHub Actions builda → Release nova) e reinstala por cima do que já está rodando. Editar
  os arquivos em `agente_local_jc/` no repositório NÃO muda o que já está instalado e rodando na sua
  máquina — só muda o que um instalador FUTURO vai empacotar.

**Pra aplicar a correção do Projudi/PJe (seção -59) e testar de novo:**
1. Feche o agente atual: clique com o botão direito no ícone da bandeja → **Sair**.
2. No terminal, na pasta do projeto:
   ```
   git add .
   git commit -m "Conector Projudi + correção do bug do PJe"
   git push
   git tag agente-v0.1.2
   git push origin agente-v0.1.2
   ```
   (Se já tiver dado `git add`/`commit`/`push` sem a tag antes, só falta a parte da tag — toda tag
   `agente-v*` nova dispara um build novo do instalador.)
3. Espere o build ficar verde nas Actions do GitHub (mesmo processo da seção -57/-58).
4. Na tela "Meu agente local" do JusControl, clique em **"Baixar agente local (Windows)"** de novo —
   o link sempre aponta pra Release mais recente, então já vem o `.exe` atualizado.
5. Rode o instalador baixado — ele atualiza por cima da instalação anterior (mesmo `AppId`, não cria
   uma segunda instalação).
6. Teste de novo: "Verificar agora" no menu do ícone, ou espere o próximo ciclo.

**Testado:** a mudança de `detalhe.html` não tem lógica de servidor nova pra testar em pytest (é só
JavaScript do lado do navegador) — rodei a suíte completa (153 testes) pra confirmar que a página do
processo continua renderizando normalmente com o script novo, sem regressão.

## -59. Conector do Projudi (Agente Local) + correção de um bug real que você bateu ao testar

**Pedido:** depois de testar a busca de autos pela primeira vez de verdade (processo público, sem
ser você o procurador, conector PJe) e ver o erro `Não foi possível carregar o WSDL de
https://pje..jus.br/1g/intercomunicacao?wsdl` — "poderia fazer os conectores dos demais tribunais?"
Perguntei por onde priorizar (você escolheu Projudi, já que o processo de teste usa esse sistema) e
confirmou que topa construir mesmo sem poder testar contra um tribunal real aqui (mesmo espírito do
PJe original).

**Bug real encontrado no teste:** o erro que você viu não era falha de rede nem bug do agente — era
o campo "Tribunal PJe" vazio na configuração avançada, que fazia a URL padrão
(`https://pje.{tribunal}.jus.br/...`) virar `https://pje..jus.br/...` (dois pontos seguidos, domínio
inválido). Isso já confirmava que o encanamento inteiro funciona (agente pareado → pegou a tarefa →
processou → reportou o erro certinho pro servidor) — só faltava o dado. **Aproveitei o refactor desta
entrega pra também corrigir esse comportamento**: agora, com o tribunal em branco e nenhuma URL
manual configurada, o conector do PJe levanta um erro claro ("URL do WSDL do PJe não configurada —
preencha em..."), em vez de montar uma URL quebrada.

**O que foi construído:**
- **`agente_local_jc/conectores/mni_soap.py`** (novo) — a lógica de consulta MNI/SOAP (protocolo
  nacional do CNJ, que PJe, Projudi, e-Proc e e-SAJ são todos obrigados a expor, cada um na própria
  URL) foi extraída do `pje_mni.py` original pra um módulo compartilhado — evita duplicar ~150 linhas
  de código idêntico a cada tribunal novo.
- **`agente_local_jc/conectores/pje_mni.py`** — agora é uma casca fina sobre `mni_soap.py`, mesmo
  comportamento de antes (inclusive a mesma classe `ConectorPjeMni` e constante `PADRAO_URL_WSDL`,
  pra não quebrar nada que já usava esse conector), **exceto** o bug do "tribunal vazio" acima, que
  ficou corrigido.
- **`agente_local_jc/conectores/projudi.py`** (novo) — conector do Projudi, também via MNI/SOAP.
  Diferença importante em relação ao PJe: **não existe padrão de URL conhecido entre tribunais que
  usam Projudi** (cada TJ hospeda num domínio próprio, sem convenção nacional confirmada) — por isso
  este conector NÃO tenta adivinhar a URL do WSDL; ela é obrigatória e precisa ser obtida direto com
  a área técnica (DTI) do tribunal específico antes de usar.
- **`app/utils/tribunais_conectores.py`** — "Projudi" saiu da lista "ainda não implementado" e virou
  uma opção de verdade no dropdown da tela do processo.
- **`agente_local_jc/config_gui.py`** — seção "Avançado" ganhou uma sub-seção "Projudi" (ID
  consultante, senha consultante, URL do WSDL) ao lado da sub-seção "PJe" já existente.
- **`agente_local_jc/config_store.py`**, **`config.py`** (modo dev/`.env`) e **`tray_app.py`** —
  passam os campos novos (`projudi_id_consultante`, `projudi_senha_consultante`, `projudi_url_wsdl` /
  `PROJUDI_ID_CONSULTANTE`, `PROJUDI_SENHA_CONSULTANTE`, `PROJUDI_URL_WSDL`) pro conector igual já
  fazia com o PJe.
- **`agente_local_jc/.env.exemplo`** — não pôde ser reescrito por aqui (arquivo protegido, mesmo caso
  da seção -57); mandei o trecho novo pra você colar manualmente.

**⚠️ Mesmo aviso do PJe original: NENHUMA chamada real foi feita contra nenhum tribunal Projudi** —
escrito só a partir da obrigação legal (resolução do CNJ) de expor o mesmo protocolo MNI que o PJe, e
compartilhando a lógica já usada por ele. Antes de um processo de verdade: descobrir com o tribunal a
URL real do WSDL de intercomunicação MNI, testar contra ela, e confirmar se os nomes de campo batem —
ver aviso completo no topo de `agente_local_jc/conectores/projudi.py`.

**Testado:** 1 teste novo do lado do servidor (`tests/test_agente_local.py`, confirma que a rota
`/processos/<id>/buscar-autos` já aceita `tribunal_conector=projudi`) — 153 testes passando (152 + 1
novo), zero regressão. Do lado do agente (fora do alcance do pytest, que só cobre o servidor), rodei
um script de verificação isolado mockando o `Client` do zeep (não dá pra instalar zeep de verdade
neste ambiente sandbox nem bater num tribunal de verdade): confirmei que (1) sem zeep instalado os
dois conectores reclamam da dependência com mensagem legível; (2) com um cliente SOAP falso, o
Projudi busca autos, extrai histórico e monta o PDF exatamente como o PJe já fazia; (3) Projudi sem
`url_wsdl` configurada levanta erro claro (não tenta adivinhar); (4) PJe com tribunal vazio agora
levanta erro claro em vez de montar `pje..jus.br` (o bug corrigido); (5) PJe com tribunal preenchido
continua montando a URL do jeito que já fazia antes (sem regressão).

**Próximo passo se quiser continuar com e-SAJ ou e-Proc:** mesmo caminho — dá pra reaproveitar boa
parte de `mni_soap.py` se esses sistemas também implementarem o protocolo MNI nacional (não
confirmado ainda por nenhuma fonte primária que consultei; precisaria pesquisar antes de assumir).

## -58. Download do instalador do Agente Local funcionando com o repositório PRIVADO no GitHub

**Pedido:** "posso deixar meu repositorio do github privado e as pessoas continuam conseguindo
baixar o instalador, ou so funciona se tiver publico?" Expliquei que, do jeito que a seção -57
tinha ficado (link direto pras Releases do GitHub), só funcionava com o repositório público — num
privado, quem não fosse colaborador levava erro ao clicar. Ofereci duas opções (repositório
continuar público, ou ficar privado com um proxy no próprio servidor) — você escolheu manter
privado.

**O que mudou:** a rota `/agente-local/baixar` (`app/routes/agente_local.py`) agora sabe entregar o
instalador de duas formas, na seguinte ordem de prioridade:

1. **Repositório privado** (`AGENTE_LOCAL_GITHUB_REPO` + `AGENTE_LOCAL_GITHUB_TOKEN` configurados,
   ver `config.py`): o servidor busca a Release mais recente e o arquivo `JusControlAgente-Setup.exe`
   pela API do GitHub, usando um token seu (Personal Access Token "fine-grained", só leitura,
   escopado a um repositório só — nunca dá acesso a mais nada da sua conta), e entrega os bytes
   direto pra quem clicar — o advogado nunca precisa acessar o GitHub nem ter conta lá, e o token
   nunca é exposto ao navegador dele. Lógica nova em **`app/utils/instalador_agente_local.py`**:
   busca a Release (`GET /repos/{repo}/releases/latest`), acha o asset pelo nome, baixa o conteúdo
   (`GET /repos/{repo}/releases/assets/{id}`) seguindo o redirecionamento que o GitHub faz pro
   armazenamento real dos arquivos — de propósito, a segunda chamada (a que baixa o arquivo de
   verdade) NUNCA leva o cabeçalho `Authorization` do GitHub junto, pra nunca correr o risco de
   vazar o token pra um servidor de terceiro.
2. **Repositório público** (só `AGENTE_LOCAL_INSTALADOR_URL` configurada, comportamento da seção
   -57): continua funcionando do jeito que já estava, sem mudança — usado como alternativa mais
   simples pra quem não se importa com o código ficar visível.

Sem nenhuma das duas configuradas, o botão continua escondido (nunca aponta pra link quebrado).
Quando as duas estão configuradas ao mesmo tempo, o modo privado tem prioridade (testei isso
explicitamente — ver abaixo).

**Testado:** 12 testes novos (`tests/test_instalador_agente_local.py`) — a busca da Release e o
download do asset com `requests.get` trocado por um fake (nunca bate na internet de verdade),
cobrindo: encontra o asset certo pelo nome; erro quando não há Release publicada (404); token
rejeitado (401); token sem permissão (403); Release sem o arquivo esperado; segue o
redirecionamento do GitHub SEM levar o `Authorization` na segunda chamada (o ponto mais sensível
de segurança desta entrega — confirmado por asserção direta nos headers de cada chamada
capturada); erro no download final. E, na rota de verdade: entrega os bytes certos com o
`Content-Disposition` certo quando o modo privado está configurado; o modo privado tem prioridade
sobre o público quando os dois estão configurados; erro do GitHub vira um flash legível (não um
erro 500 cru); o botão aparece na tela quando só o modo privado está configurado (antes só
testava com o modo público). **152 testes passando no total** (140 já existentes + 12 novos),
nenhuma regressão.

⚠️ **Ação sua necessária depois do deploy** — só se você quiser usar o modo repositório privado
(o modo público da seção -57 continua igual, sem ação nova): (1) `git push` de sempre; (2) sem
`sincronizar_schema.py` (nenhuma tabela/coluna nova); (3) gere um Personal Access Token
"fine-grained" no GitHub (github.com/settings/personal-access-tokens → Generate new token →
escolha só o repositório do JusControl → em "Repository permissions", defina "Contents" como
"Read-only", deixe todo o resto sem acesso) e defina `AGENTE_LOCAL_GITHUB_REPO` (formato
`usuario/repositorio`) e `AGENTE_LOCAL_GITHUB_TOKEN` nas variáveis de ambiente do serviço no
EasyPanel — sem precisar de rebuild, só reiniciar o serviço. Se `AGENTE_LOCAL_INSTALADOR_URL`
ainda estiver configurada de uma tentativa anterior, pode deixar ou remover — o modo privado tem
prioridade de qualquer jeito.

**Arquivos novos:** `app/utils/instalador_agente_local.py`, `tests/test_instalador_agente_local.py`.
**Arquivos alterados:** `config.py` (novas variáveis `AGENTE_LOCAL_GITHUB_REPO`/
`AGENTE_LOCAL_GITHUB_TOKEN`), `app/routes/agente_local.py`,
`app/templates/agente_local/meu_agente.html`, `agente_local_jc/README.md`.

## -57. Instalador do Agente Local (ícone na bandeja, início automático com o Windows, download pela própria tela)

**Pedido:** "ok, como eu instalo a ia local no computador do adivogado? teria como eu instalar isso
automaticamente ou criar um instalador e o adivogado instalar através do site?" Expliquei que
nenhum site instala programa sozinho na máquina de alguém sem um clique (isso vale pra qualquer
site, é o motivo de vírus não conseguir se instalar sozinho) — o mínimo possível é baixar +
clique duplo + colar o token uma vez. Combinamos (via pergunta de esclarecimento): repositório é
GitHub, instalador "completo" (ícone na bandeja + inicia com o Windows, não só uma janela que
precisa ficar aberta), e o link de download fica direto na tela "Meu agente local".

**O que mudou — o programa (`agente_local_jc/`) ganhou um segundo modo de rodar**, além do modo
terminal que já existia:
- **`config_store.py`** (novo) — onde a configuração fica salva quando o agente é INSTALADO (não
  rodado do código-fonte): `%APPDATA%\JusControlAgente\config.json` no Windows, só nesta máquina,
  nunca sincronizado com nada. Diferente do `config.py` original (que lê `.env`/variável de
  ambiente — continua existindo, é o "modo desenvolvedor" pra quem for mexer no código).
- **`config_gui.py`** (novo) — janela de configuração (tkinter, já vem com o Python do Windows,
  sem dependência extra): pede endereço do JusControl + token de pareamento, tem um botão "Testar
  conexão" antes de salvar, e uma seção "Avançado" recolhida com os campos de certificado A1 e do
  tribunal-piloto (PJe) — a maioria dos advogados só precisa colar o token.
- **`autostart_windows.py`** (novo) — liga/desliga o início automático com o Windows (Registro em
  `HKEY_CURRENT_USER`, sem precisar de administrador, só afeta o usuário atual).
- **`tray_app.py`** (novo) — o programa com ícone na bandeja: verde = conectado, vermelho = com
  problema; menu com "Configurar...", "Verificar agora", "Ver log", "Sair". É este arquivo que
  vira o `.exe` do instalador.
- **`cliente_api.py`** (reescrito) — virou uma classe (`ClienteJusControl(url, token)`) em vez de
  funções soltas lendo `config.py` direto — necessário porque agora existem DUAS fontes de
  configuração (`.env` pro modo terminal, `config_store.json` pro modo instalado) que precisam do
  mesmo cliente HTTP.
- **`motor.py`** (novo) — a lógica de "pegar tarefa pendente → buscar autos → mandar resultado ou
  erro" foi extraída de dentro do `main.py` pra cá, pra garantir que o modo terminal e o modo
  instalado se comportem EXATAMENTE igual (só muda de onde cada um lê configuração e como cada um
  mostra o andamento) — testei essa peça de verdade: subi o servidor Flask real numa porta local,
  parei um conector fake (sem depender de zeep/tribunal real, mesma técnica já usada nos testes do
  DataJud), e confirmei o ciclo completo `ClienteJusControl` → `motor.verificar_uma_vez` → API →
  `Documento` criado no processo, status "concluída".
- **`main.py`** (reescrito) — agora só orquestra `config.py` + `ClienteJusControl` + `motor.py`,
  bem mais curto que antes.

**Como o instalador de verdade é gerado (sem precisar de um Windows à mão):**
- **`build/gerar_icone.py`** (novo) — desenha o ícone do programa (não depende de nenhum arquivo
  de imagem versionado no repositório) — testei que gera um `.ico` válido.
- **`build/agente_local.spec`** (novo) — spec do PyInstaller: empacota `tray_app.py` num único
  `.exe`, sem janela de terminal.
- **`build/instalador.iss`** (novo) — script do Inno Setup: embrulha esse `.exe` num instalador de
  verdade (assistente Avançar/Avançar/Concluir, atalho no Menu Iniciar, aparece em "Adicionar ou
  remover programas"), sem pedir permissão de administrador.
- **`.github/workflows/build-agente-local.yml`** (novo) — toda vez que uma tag `agente-vX.Y.Z` é
  publicada no GitHub (`git tag agente-v0.1.0 && git push origin agente-v0.1.0`), uma máquina
  Windows temporária e gratuita do GitHub Actions compila tudo sozinha e publica o
  `JusControlAgente-Setup.exe` nas "Releases" do repositório — sem precisar de nenhum Windows seu.

**Lado servidor (JusControl):**
- **`config.py`** — nova variável `AGENTE_LOCAL_INSTALADOR_URL` (aponta pro link do instalador nas
  Releases do GitHub). Sem ela definida, o botão de download simplesmente não aparece — nunca
  aponta pra um link quebrado.
- **`app/routes/agente_local.py`** — rota nova `GET /agente-local/baixar`, que redireciona pro
  instalador (registra no log quem baixou — ajuda a perceber se um advogado começou a instalar mas
  não chegou a colar o token) e as views existentes passam essa URL pro template.
- **`app/templates/agente_local/meu_agente.html`** — cartão novo "Instalar na minha máquina" com o
  botão "Baixar agente local (Windows)" (só aparece quando `AGENTE_LOCAL_INSTALADOR_URL` está
  configurada).

**⚠️ O que NÃO foi testado de verdade** (limite físico deste ambiente, não do código): a janela
`config_gui.py` (tkinter) e o ícone da bandeja `tray_app.py` (pystray) só puderam ser conferidos
por leitura/sintaxe aqui — este ambiente não tem Windows nem tela gráfica pra rodar de fato uma
janela ou um ícone de bandeja. O que FOI testado de verdade: o motor completo (`motor.py` +
`cliente_api.py`) contra o servidor Flask real, de ponta a ponta; a rota de download (3 testes
novos, `tests/test_agente_local.py` — botão aparece/some conforme a configuração, redireciona pro
link certo, avisa quando não configurado); e o build do ícone (`gerar_icone.py`, gera um `.ico`
válido de verdade). Antes de distribuir o instalador pra advogados de verdade, gere uma versão
(`git tag agente-v0.1.0`) e teste você mesmo num Windows: instalar, colar um token de teste, ver o
ícone mudar de cor, clicar em "Verificar agora".

**140 testes passando no total** (137 já existentes + 3 novos desta entrega), nenhuma regressão.

⚠️ **Ação sua necessária depois do deploy:** (1) `git push` de sempre — inclui a pasta
`.github/workflows/`, que só o GitHub usa (não afeta o container do JusControl em nada); (2) não
precisa de `sincronizar_schema.py` (nenhuma tabela/coluna nova nesta entrega); (3) pra o botão de
download aparecer: publique uma tag (`git tag agente-v0.1.0 && git push origin agente-v0.1.0`),
espere o GitHub Actions terminar (aba "Actions" do repositório), confirme que apareceu em
"Releases", e defina `AGENTE_LOCAL_INSTALADOR_URL` nas variáveis de ambiente do serviço no
EasyPanel apontando pra
`https://github.com/<seu-usuario>/<seu-repositorio>/releases/latest/download/JusControlAgente-Setup.exe`
— sem precisar de rebuild do container, só a variável de ambiente.

**Arquivos novos:** `agente_local_jc/config_store.py`, `agente_local_jc/config_gui.py`,
`agente_local_jc/autostart_windows.py`, `agente_local_jc/tray_app.py`, `agente_local_jc/motor.py`,
`agente_local_jc/requirements-build.txt`, `agente_local_jc/build/gerar_icone.py`,
`agente_local_jc/build/agente_local.spec`, `agente_local_jc/build/instalador.iss`,
`.github/workflows/build-agente-local.yml`.
**Arquivos alterados:** `agente_local_jc/cliente_api.py` (reescrito, virou classe),
`agente_local_jc/main.py` (reescrito, usa `motor.py`), `agente_local_jc/requirements.txt`,
`agente_local_jc/README.md`, `config.py`, `.gitignore`, `app/routes/agente_local.py`,
`app/templates/agente_local/meu_agente.html`, `tests/test_agente_local.py`.

## -56. Agente Local: busca de autos completos (PDF + histórico integral) com o certificado do próprio advogado, multi-tribunal

**Pedido (resumo da conversa completa):** o DataJud só traz metadados públicos, nunca o PDF do
processo. Você perguntou se dava pra buscar o PDF completo; expliquei que só autenticando no
sistema do tribunal com o certificado/token do próprio advogado (A1/A3), e que um certificado A3
nunca pode ser exportado pra um servidor. Você perguntou se dava pra fazer isso como uma extensão
rodando na máquina do usuário, sem guardar credencial nenhuma no servidor, de forma gratuita —
confirmei que sim, essa é a arquitetura mais segura. Você confirmou que entendeu que isso é
diferente do agente local desta própria conversa (que roda no SEU computador, fora do JusControl) —
esse seria um agente local rodando na máquina de CADA ADVOGADO cliente do JusControl, nunca na VPS.
Por fim: **"ok, pode dar início ao processo... preciso que o agente busque processos de vários
tribunais, pois os advogados trabalham com diferentes tribunais"** — pedido explícito de
arquitetura multi-tribunal desde o início, mesmo que só um tribunal seja implementado de verdade
agora.

**Desenho da solução:** dois lados, propositalmente bem separados.

1. **Lado servidor** (dentro do JusControl, neste repositório) — só um "correio": guarda o
   pareamento (token pessoal do agente de cada advogado) e a fila de pedidos de busca, e recebe o
   PDF já pronto no final. **Nunca fala com nenhum tribunal.**
2. **Lado agente local** (pasta nova `agente_local_jc/` na raiz do repositório, um programa Python
   separado — NÃO faz parte do Flask app, não sobe pra VPS) — instalado por cada advogado no
   PRÓPRIO computador. É quem autentica de verdade no tribunal com o certificado do advogado
   (nunca sai da máquina dele), busca o PDF e o histórico, e manda só o resultado de volta.

**O que foi criado (lado servidor):**
- **`app/models/agente_local.py`** (novo) — dois modelos:
  - `AgenteLocalPareado`: token de acesso pessoal (hash SHA-256, nunca o valor puro — mesmo padrão
    de `TokenIntegracao`), mas por **usuário**, não por empresa: o certificado é do advogado, não
    do escritório inteiro. `emitir_para`/`validar`/`revogar`, igual ao token de integração.
  - `SolicitacaoBuscaAutos`: a fila — processo, conector de tribunal escolhido (slug), status
    (pendente → em_andamento → concluída/erro/cancelada), quem pediu, qual agente atendeu, e o
    `Documento` resultante.
- **`app/utils/tribunais_conectores.py`** (novo) — registro dos slugs de tribunal que a tela
  oferece (`pje_mni` implementado; `esaj_sp`, `eproc`, `projudi` listados como roadmap, desabilitados
  no formulário) — arquitetura multi-tribunal desde já, como pedido, mesmo com só 1 implementado.
- **`app/routes/agente_local.py`** (novo) — tela **"Meu agente local"** (menu lateral, item "AL"),
  disponível para qualquer usuário logado (não só admin — pareamento é pessoal): gerar/revogar o
  próprio token de pareamento.
- **`app/routes/agente_local_api.py`** (novo) — API que o agente local (na máquina do advogado)
  usa: `GET /api/agente-local/ping` (confirma token), `GET /api/agente-local/tarefas` (só as
  pendentes do PRÓPRIO usuário dono do agente — nunca de outro advogado, mesmo da mesma empresa),
  `POST .../iniciar`, `POST .../resultado` (recebe o PDF, cria um `Documento` normal do processo,
  categoria `autos_completo_agente_local` — mesma tabela/pasta de upload manual), `POST .../erro`.
  Autenticada por `Authorization: Bearer <token>`, isenta de CSRF (mesmo raciocínio de
  `api_integracao.py`: autenticação por token, não por cookie de sessão).
- **`app/routes/processos.py`** — duas rotas novas: `POST /processos/<id>/buscar-autos` (cria a
  solicitação, exige um agente local pareado e ativo) e
  `POST /processos/solicitacoes-busca-autos/<id>/cancelar`. Tela de detalhe do processo ganhou uma
  seção nova na aba "Documentos": escolher o conector, pedir a busca, acompanhar o status, baixar
  o PDF quando pronto.
- **`app/templates/agente_local/meu_agente.html`** (novo), **`app/templates/processos/detalhe.html`**
  e **`app/templates/base.html`** — telas/nav acima.
- **`app/models/__init__.py`**, **`app/__init__.py`** — registro dos 2 modelos novos e dos 2
  blueprints novos (`agente_local_bp`, isento de bloqueio por módulo por não estar no catálogo —
  mesmo padrão de "Integrações"; `agente_local_api_bp`, isento de CSRF).

**O que foi criado (lado agente local, pasta `agente_local_jc/`, fora do Flask app):**
- `conector_base.py` — interface `ConectorTribunalLocal` (mesmo espírito de `ConectorCaptura` do
  projeto principal), pra cada tribunal novo virar só mais um arquivo em `conectores/`.
- `conectores/pje_mni.py` — conector do PJe via protocolo **MNI** (SOAP), usando `zeep` com WSDL
  carregado dinamicamente (nunca um schema fixo hardcoded) e autenticação mTLS com o certificado do
  advogado. Implementa o padrão de 2 chamadas confirmado pela documentação técnica do CNJ/STF/TJRJ:
  1ª chamada traz metadados + lista de IDs de documento; 2ª chamada, passando esses IDs, traz o
  conteúdo binário (base64) de cada um — consolidados num único PDF (via `pypdf`, se instalado).
- `certificado.py` — abre um certificado **A1** (`.pfx`/`.p12`) em memória (biblioteca
  `cryptography`), nunca grava a senha, usa arquivo temporário com permissão restrita só pelo
  tempo da chamada ao tribunal, sempre apagado depois — testei isso de verdade aqui (gerar um
  certificado de teste, carregar, confirmar que o arquivo temporário some depois do `with`, e que
  senha errada é rejeitada com mensagem clara).
- `registro_conectores.py`, `cliente_api.py` (fala com `/api/agente-local/*`), `main.py` (laço de
  polling), `config.py` + `.env.exemplo`, `requirements.txt`, `README.md`.

**⚠️⚠️⚠️ O que NÃO foi testado — leia antes de usar com processo real:** o conector `pje_mni` foi
escrito só a partir de documentação pública (CNJ, STF, TJRJ) — **nenhuma chamada real foi feita
contra nenhum tribunal**, porque não há aqui nenhuma credencial de teste nem certificado real pra
testar com. Os nomes de campo usados (`idConsultante`, `numeroProcesso`, `idDocumento`, `conteudo`
etc.) são os únicos confirmados por fonte primária — mas cada um dos ~90 tribunais roda a própria
instância do PJe, com variações possíveis. **Antes de usar isso com um processo de verdade**, é
preciso: (1) conseguir credencial/certificado de teste com UM tribunal específico; (2) confirmar a
URL real do WSDL dele; (3) rodar o agente contra esse ambiente e conferir se a resposta bate com o
que o código espera, ajustando `conectores/pje_mni.py` se precisar. Isso está documentado bem
grande no topo do próprio arquivo e no `README.md` da pasta — ninguém vai achar isso "parece
funcionar" sem querer.

O que FOI testado de verdade (13 testes novos, `tests/test_agente_local.py`): emissão/validação/
revogação de token de pareamento; a tela "Meu agente local" (parear, revogar, e que um usuário não
consegue revogar pareamento de outro); pedir e cancelar uma busca pela tela do processo, inclusive
a validação de conector ainda-não-implementado sendo recusada; e a API completa do agente
(autenticação por Bearer, isolamento — um agente só vê/atende tarefa do PRÓPRIO usuário dono dele,
nunca de outro advogado —, o fluxo iniciar → enviar resultado → `Documento` criado, e reportar
erro). **137 testes passando no total** (124 já existentes + 13 novos), nenhuma regressão.

⚠️ **Ação sua necessária depois do deploy:** (1) `git push` de sempre; (2) rode
`python sincronizar_schema.py` no Terminal do EasyPanel depois do deploy — duas tabelas novas
(`agentes_locais_pareados`, `solicitacoes_busca_autos`), sem nenhuma coluna NOT NULL sem default,
então o script resolve sozinho; (3) a pasta `agente_local_jc/` não entra no Dockerfile nem precisa
de rebuild — ela NUNCA roda no servidor, é só um programa que cada advogado baixa e roda no próprio
computador (avise a equipe que isso ainda é um piloto, não distribua pra cliente final ainda).

**Arquivos novos:** `app/models/agente_local.py`, `app/utils/tribunais_conectores.py`,
`app/routes/agente_local.py`, `app/routes/agente_local_api.py`,
`app/templates/agente_local/meu_agente.html`, `tests/test_agente_local.py`, e toda a pasta
`agente_local_jc/` (`config.py`, `.env.exemplo`, `certificado.py`, `conector_base.py`,
`conectores/pje_mni.py`, `conectores/__init__.py`, `registro_conectores.py`, `cliente_api.py`,
`main.py`, `requirements.txt`, `README.md`).
**Arquivos alterados:** `app/models/__init__.py`, `app/__init__.py`, `app/routes/processos.py`,
`app/templates/processos/detalhe.html`, `app/templates/base.html`.

## -55. Tela "Novo processo": busca por CNJ primeiro, resto do formulário só aparece depois

**Pedido:** na tela de novo processo, mostrar a princípio só o campo "Nº do processo (CNJ)" e um
botão de lupa para buscar; depois da busca, mostrar como texto simples (não editável) o que foi
encontrado automaticamente e como campo normal o que não foi encontrado, e cada campo preenchido
automaticamente ganhar um ícone de lápis para o usuário poder clicar e transformá-lo de volta em
campo editável, corrigindo o que o DataJud trouxer errado.

**O que já existia:** a tela "Novo processo" (`app/routes/processos.py::novo()`, template
`app/templates/processos/form.html`) já mostrava TODOS os campos de uma vez, com uma busca no
DataJud disparada ao apertar Enter no número CNJ que preenchia os campos vazios — mas sem nenhuma
distinção visual entre "isso veio da busca automática" e "isso eu digitei", e sem esconder nada
antes de buscar. O endpoint de busca em si (`governanca.consultar_cnj_preview`, só leitura, nunca
grava nada) já existia e já era reaproveitado tanto por essa tela quanto pela tela separada
"Cadastrar por CNJ" — não precisou de nenhuma rota nova.

**O que mudou:**
- **`app/templates/processos/form.html`** — reescrito. Em modo CRIAÇÃO (sem `processo` ainda), a
  tela agora nasce só com "Nº do processo (CNJ)", o seletor opcional de tribunal (acelera a busca)
  e o botão **Buscar** (lupa) — todo o resto do formulário fica dentro de
  `#novo-processo-resto`, com `display:none`, e só aparece quando: (a) a busca roda (achando o
  processo ou não — nunca trava o usuário esperando por um resultado que não vem), ou (b) o
  usuário clica em "Não tenho o número agora / prosseguir sem buscar" (processo sem CNJ ainda
  distribuído continua um caso válido — `numero_processo` sempre foi opcional no modelo). Em modo
  EDIÇÃO o comportamento continua idêntico a antes — todos os campos aparecem de cara, sem esse
  fluxo em 2 passos — a mudança foi só para o cadastro novo, como pedido.
  - Todo campo que a busca do DataJud pode preencher (Área do direito, Tipo de ação, Instância,
    Comarca, Vara/Tribunal, Valor da causa, Data de distribuição, Descrição) virou um "campo
    travável": continua sendo um `<input>`/`<textarea>` de verdade (o valor é enviado no formulário
    normalmente, travado ou não), mas quando a busca preenche um campo que estava vazio, o
    JavaScript marca `readonly` nele, aplica um estilo de texto simples (sem borda, fundo neutro) e
    mostra um ícone de lápis ao lado. Clicar no lápis destrava o campo (volta a ser um input normal,
    já com foco e o texto selecionado pra já poder corrigir) e o lápis desaparece.
  - Campo que o DataJud nunca preenche (Nº de controle interno, Cliente, Unidade, Fase, Polo do
    cliente, Parte contrária, Advogado da parte contrária, Responsável) continua sendo um campo
    comum, editável desde o início, sem lápis nenhum do lado — exatamente o "aparecer como input o
    que não tiver sido preenchido" pedido.
- **`app/static/css/estilo.css`** — classes novas `.campo-lockable-wrap`, `.travado` e `.btn-lapis`
  (visual do campo travado e do lápis — some/aparece conforme a classe `.tem-valor-travado` no
  wrapper, controlada pelo JS acima).
- **`tests/test_form_novo_processo.py`** (novo) — confirma que a tela de criação nasce só com o
  Passo 1 (o resto escondido por `display:none`), que a tela de edição continua mostrando tudo
  de cara (comportamento antigo intacto), e que o cadastro manual (sem buscar nada) continua
  criando o processo normalmente — prova de que a reforma foi só de apresentação, não mudou nada
  na rota nem no modelo.

**Testado:** os 3 testes novos acima, mais uma verificação end-to-end num navegador de verdade
(Playwright/Chromium, não só a suíte pytest — pytest não executa o JavaScript da página) com um
conector DataJud falso (sem depender de credencial real): logou, digitou um CNJ, clicou em
"Buscar", e confirmou visualmente que (1) o resto do formulário estava escondido antes e visível
depois da busca; (2) "Comarca" (que o fake devolveu) virou texto travado com o lápis visível; (3)
"Fase" (que o DataJud nunca preenche) continuou um input normal, sem lápis; (4) clicar no lápis da
Comarca devolveu um input editável de verdade, já pronto pra receber a correção. Rodei a suíte
inteira depois — **124 testes passando** (121 já existentes + 3 novos), nenhuma regressão.

⚠️ **Ação sua necessária depois do deploy** — **nenhuma além do `git push` de sempre.** Esta
entrega mexe só em template HTML, CSS e um arquivo de teste — nenhuma coluna nova, nenhuma tabela
nova, nenhum `.cron` novo. Não precisa rodar `sincronizar_schema.py` nem rebuild especial.

**Arquivos alterados:** `app/templates/processos/form.html`, `app/static/css/estilo.css`,
`tests/test_form_novo_processo.py` (novo).

## -54. 🔴 CORREÇÃO DE SEGURANÇA CRÍTICA: prazos/audiências/movimentações/tarefas/compromissos de uma empresa apareciam para admin de OUTRA empresa

**Reportado por você:** "prazos em atenção e prazos perdidos aparecem para empresas diferentes das
que o processo foi cadastrado, corrija isso".

**Gravidade — leia isto:** este era um vazamento real de dado entre empresas clientes (tenants)
diferentes, na plataforma multi-empresa. Não é um problema cosmético nem um bug de exibição — é uma
falha de isolamento de dado (multi-tenancy), a categoria mais grave de bug que este sistema pode ter,
porque significa que o escritório A podia enxergar dado do escritório B (prazo, número de processo,
descrição do ato, texto de movimentação processual, tarefa, compromisso de agenda) sem nenhuma
autorização.

**Causa raiz (confirmada lendo o código, não suposição):** várias telas construíam a consulta ao
banco assim:
```python
prazos_q = Prazo.query.join(Processo)
if not current_user.is_admin:
    prazos_q = prazos_q.filter(Processo.unidade_id == current_user.unidade_id)
```
Repare que o filtro só existe dentro do `if not current_user.is_admin` — ou seja, ele **só
restringia usuário comum** (advogado/funcionário/gestor). Qualquer usuário com papel "admin"
caía no `else` implícito e recebia a consulta **sem filtro nenhum de empresa/unidade**. O problema:
`current_user.is_admin` é `True` para o admin de **qualquer** empresa cliente, não só para o "admin
desenvolvedor" (o admin da empresa dona da própria plataforma, que É, de propósito, quem deve ver
tudo). Ou seja: o admin comum de um escritório cliente qualquer também caía nesse `else` e recebia
prazos, audiências, movimentações e tarefas de **todas as empresas cadastradas no sistema**, não só
da própria. O helper certo pra isso já existe e já era usado em boa parte do sistema —
`aplicar_escopo_unidade()` (`app/utils/acesso.py`) — que implementa a regra correta das 3 camadas:
admin desenvolvedor vê tudo, admin de empresa vê só a própria empresa (todas as unidades dela), os
demais papéis só a própria unidade. O bug era não usar esse helper nesses pontos específicos —
provavelmente um padrão colado de uma tela pra outra antes de `aplicar_escopo_unidade` existir, e
nunca atualizado.

Corrigido um total de **9 pontos** com exatamente esse padrão, em 4 arquivos — achados varrendo TODO
o projeto por esse padrão (`grep` por `if not current_user.is_admin` combinado com toda consulta
`.join(Processo)`/`.query` de Prazo, Audiencia, Movimentacao, Tarefa e Compromisso), não só o ponto
que você reportou, porque um bug desta gravidade merece verificação completa, não um remendo isolado:

- **`app/routes/dashboard.py`** (`index()`, a tela "Painel" — a fonte exata dos cartões "Prazos em
  atenção" e "Prazos perdidos" que você citou): `prazos_q` e `audiencias_q`. Havia ainda um agravante
  aqui — um `if False` deixava morto um trecho que já tentava usar `aplicar_escopo_unidade` (mas
  errado, com `responsavel_id` em vez de `unidade_id`), então na prática rodava sempre a versão sem
  filtro nenhum.
- **`app/routes/governanca.py`**: `fila_intimacoes()` (fila de trabalho de prazos), `painel()` (dois
  pontos — cartões "Prazos fatais"/"Vencidos sem evidência", e a lista de movimentações críticas das
  últimas 24h), `metricas()` (taxa de prazo cumprido/perdido e "Processos com mais prazos perdidos"),
  e `relatorio_semanal_preview()` (dois pontos — prazos da semana/perdidos da semana passada, e
  movimentações da semana).
- **`app/routes/agenda.py`** (`index()`, a Agenda integrada — mês inteiro): os 4 blocos de eventos do
  calendário, prazos, audiências, tarefas e compromissos, todos com o mesmo padrão.
- **`app/routes/agente_ia.py`** (`_escopo_prazos()`, usada pela persona "Operação" do Agente de IA):
  este é o ponto mais delicado dos 9 — o vazamento aqui não aparecia como uma linha numa tabela, e
  sim embutido em **texto gerado pela IA** ("Prazos vencendo nos próximos 7 dias: ..."), então um
  admin podia literalmente perguntar pro assistente e receber, em prosa, prazo de outro escritório.

**O que NÃO foi mexido, de propósito:** dentro de `governanca.metricas()`, a lista `logs_recentes`
(`LogCaptura`) não tem filtro de empresa nenhum, mas só expõe estatística agregada de sucesso/falha
de captura automática de processo (infraestrutura), nunca dado de cliente/processo/prazo — fora da
classe de gravidade deste bug, registrado aqui só pra constar que foi visto e avaliado, não
esquecido. Em `app/routes/timesheet.py`, o mesmo padrão `if not current_user.is_admin` existe mas
**já** está corretamente construído em cima de uma consulta que **já passou** por
`aplicar_escopo_unidade` antes — o `if` ali só reduz ainda mais (mostrar só os apontamentos do
próprio usuário), nunca amplia; não é o mesmo bug e não precisava de correção.

**A correção**, em todos os 9 pontos: trocar o `if not current_user.is_admin: filter(...)` por uma
chamada a `aplicar_escopo_unidade(query, Processo)` (ou `..., Tarefa` / `..., Compromisso` nos casos
em que o modelo já tem `unidade_id` direto, sem precisar de `.join(Processo)`) — o mesmo helper já
usado (e já testado) em `processos.py`, `clientes.py` e outras telas do sistema, agora aplicado de
forma consistente em todo lugar que faltava.

**Testado:** novo arquivo `tests/test_isolamento_multi_tenant_prazos.py`, 13 testes — cria DUAS
empresas clientes reais e independentes (Empresa A e Empresa B), cada uma com seu próprio admin e seu
próprio processo/prazo/audiência/tarefa/compromisso/movimentação, mais uma terceira empresa marcada
como dona da plataforma (admin desenvolvedor) e um advogado comum lotado na Empresa A. Provado, para
cada uma das 6 telas/pontos afetados (Painel, Painel de governança, Fila de intimações, Métricas de
governança, Agenda, contexto do Agente de IA "Operação"):
1. o admin da Empresa A nunca vê dado da Empresa B (nem no HTML renderizado, nem no texto do Agente
   de IA);
2. o admin desenvolvedor continua vendo dado de ambas as empresas — comportamento que precisa
   continuar existindo, não foi quebrado pela correção;
3. um usuário comum (advogado) continua restrito à própria unidade, como sempre foi.

Antes de escrever a correção final, validei que os testes realmente pegam o bug: reverti
temporariamente um dos 9 pontos pro código antigo e confirmei que o teste correspondente falha; depois
restaurei a correção e confirmei que volta a passar. Rodei a suíte inteira depois — **121 testes
passando** (108 já existentes + 13 novos), nenhuma regressão.

⚠️ **Ação sua necessária depois do deploy** — **nenhuma além do `git push` de sempre.** Esta correção
é só lógica de consulta ao banco (troca de filtro), não adiciona coluna nem tabela nova — não precisa
rodar `sincronizar_schema.py`. Também não adiciona nenhum `.cron` novo — não precisa de rebuild
especial do container, só o deploy normal.

**Descoberto de lambuja, sem relação com este bug, registrando pra você decidir:** ao testar
`relatorio_semanal_preview()` (parte deste lote), percebi que essa tela quebra com erro 500 pra
**qualquer** usuário, de qualquer empresa — falta o arquivo de template
`app/templates/governanca/relatorio_semanal_preview.html` (a rota existe, o link no menu existe,
mas o `.html` nunca foi criado). Isso não é um problema de segurança/vazamento — é uma tela
quebrada há algum tempo, sem relação com o bug que você reportou — mas fica pendente pra próxima
rodada, se quiser que eu crie o template.

**Arquivos alterados:** `app/routes/dashboard.py`, `app/routes/governanca.py`,
`app/routes/agenda.py`, `app/routes/agente_ia.py`, `tests/test_isolamento_multi_tenant_prazos.py`
(novo).

## -53. Comparação com o Jusbrasil: referência de estilo na minuta por IA + due diligence de cliente novo

**Contexto:** fora da tabela de prioridades original — surgiu de uma pergunta sobre o que dava pra
aproveitar do Jusbrasil (plataforma de pesquisa jurídica/dados processuais, categoria diferente do
JusControl, que é gestão do escritório). Comparação honesta primeiro: não dá pra "pegar" conteúdo
deles (jurisprudência, design, dado) — isso é vedado pelos Termos de Uso e por direito autoral —, só
dá pra se inspirar na ideia e construir a própria versão, ou contratar a API paga deles como mais um
fornecedor de dados. Duas coisas concretas saíram disso, uma construída de verdade e outra preparada
pra quando você contratar um provedor:

**1) Referência de estilo no rascunho de petição por IA** — o Jus IA (Jusbrasil) usa peças reais e
validadas em tribunais como inspiração de estilo. O JusControl já tinha um gerador de rascunho de
petição por IA local (aba "Análise IA" de cada processo, com proteção contra invenção de fato/valor/
lei — isso é de uma rodada anterior, não desta). O que faltava era deixar o advogado escolher um
documento JÁ anexado a este processo (uma petição anterior que já funcionou) como referência de
ESTILO/ESTRUTURA — mais relevante que um banco genérico, porque é prática real do próprio escritório,
e sem depender de nenhum fornecedor externo.

- **`app/utils/extracao_documento.py`** (novo) — lê o texto de um documento já anexado (.pdf, .docx,
  .txt; outros tipos recusados com erro claro, PDF escaneado sem camada de texto também). Nunca
  quebra a geração: qualquer falha de leitura vira um aviso, e a geração segue sem referência.
- **`app/utils/analise_processo_ia.py`** — `gerar_analise()` ganhou o parâmetro opcional
  `texto_referencia`, injetado no prompt com uma instrução explícita e reforçada: é PROIBIDO copiar
  fato, nome, valor ou fundamento do documento de referência — só estilo. Crucial: o texto de
  referência fica FORA do "digest" usado pela checagem automática de grounding — então, se algum
  valor do documento de referência vazar pro rascunho novo, ele continua sendo sinalizado como
  não-lastreado, exatamente como se fosse invenção do modelo (testado explicitamente).
- **`app/models/agente_ia.py`** — `AnaliseProcessoIA.documento_referencia_id` (nova coluna, nullable)
  guarda qual documento foi usado, só quando a extração deu certo de verdade.
- **`app/routes/processos.py` / `app/jobs/ia_jobs.py`** — a extração acontece na rota (síncrona,
  rápida, só leitura de arquivo) ANTES de enfileirar o job de geração (que continua rodando em
  segundo plano, sem mudança nesse ponto); o texto já extraído é repassado pro job.
- **`app/templates/processos/detalhe.html`** — seletor novo (só aparece com "Rascunho de petição")
  listando os documentos já anexados a ESTE processo; a análise gerada mostra um selo "estilo de
  ‘X’" quando uma referência foi realmente usada.
- **`requirements.txt`** — `pypdf` e `python-docx` (novas dependências, leitura de PDF/DOCX).

**2) Due diligence de cliente novo (busca por CPF/CNPJ em todo o Brasil)** — recurso que a API paga
do Jusbrasil (Jusbrasil Soluções) oferece e que é genuinamente diferente da verificação de conflito
de interesses que o JusControl já tem: conflito de interesses só cruza contra os clientes JÁ
cadastrados neste escritório; due diligence buscaria em TODO o histórico processual do Brasil antes
de aceitar um cliente novo. Isso exige um provedor pago — mesma trava de sempre neste projeto: nunca
implementar contra um provedor sem credencial e documentação reais (arriscaria devolver dado errado/
incompleto silenciosamente).

- **`app/utils/captura_conectores.py`** — novo método abstrato `buscar_processos_por_parte` no
  contrato `ConectorCaptura` (o mesmo ponto de extensão já usado pelo DataJud), e um novo
  `dataclass ProcessoEncontradoDueDiligence`. `obter_conector("due_diligence")` sempre levanta
  `ConectorNaoConfiguradoError` hoje, com a lista de provedores conhecidos (Judit, Escavador,
  Digesto, Codilo, **Jusbrasil Soluções** — adicionado nesta rodada às opções já listadas antes)
  — nunca finge uma busca que não roda.
- **`app/utils/conector_datajud.py`** — `ConectorDataJud` implementa o método novo só pra explicar
  por que o DataJud gratuito não cobre isso (exige saber o tribunal de antemão; due diligence
  precisa buscar em todos).
- **`app/routes/clientes.py` / `app/templates/clientes/due_diligence.html`** (novo) — tela nova
  "Due diligence" no cliente (botão só pra admin/gestor — é uma ferramenta de governança com custo
  por consulta quando um provedor estiver ativo, mesmo critério de acesso da alçada financeira).
  Hoje sempre mostra a explicação de que falta contratar um provedor, com as opções listadas; o
  código já está pronto pra mostrar uma tabela de resultados reais assim que
  `buscar_processos_por_parte` tiver uma implementação de verdade por trás.

**Testado:** 18 testes novos (`tests/test_referencia_estilo_minuta.py`, 12 testes, e
`tests/test_due_diligence.py`, 6 testes) — extração de texto funciona pra .pdf/.docx/.txt e recusa
tipo não suportado/arquivo ausente/texto vazio com erro claro; texto grande é cortado no limite;
um valor "vazado" do texto de referência pro rascunho gerado continua sendo sinalizado pela checagem
de grounding (prova de que a referência não vira "fato confiável"); tipo "resumo" ignora qualquer
referência passada; a rota extrai a referência antes de enfileirar e não bloqueia a geração quando o
documento é de outro processo ou de tipo não suportado (só avisa e segue sem referência); o conector
DataJud recusa `buscar_processos_por_parte` com erro claro; `obter_conector("due_diligence")` sempre
lista os 5 provedores conhecidos; a tela de due diligence mostra a explicação sem provedor
configurado e não grava log nenhum (nenhuma busca de verdade aconteceu); gestor acessa, advogado
comum leva 403; e — com um conector FALSO (sem depender de nenhuma credencial real de provedor
nenhum) — a tela mostra resultado de verdade e grava o log de auditoria, provando que a integração
funciona de ponta a ponta assim que um provedor real for implementado atrás da mesma interface.
Rodei a suíte inteira depois (108 testes) — sem regressão.

⚠️ **Ação sua necessária depois do deploy** — este lote adiciona uma coluna nova
(`AnaliseProcessoIA.documento_referencia_id`, nullable) e uma dependência nova em `requirements.txt`
(`pypdf`, `python-docx`): depois do `git push` de sempre, rode `python sincronizar_schema.py` no
Terminal do container. Como há dependência nova em `requirements.txt` (não só em
`requirements-dev.txt`), o EasyPanel já reconstrói a imagem do zero num redeploy comum — não precisa
de nenhum passo extra além desse.

A due diligence continua **bloqueada** até você escolher e contratar um dos 5 provedores listados
(Judit, Escavador, Digesto, Codilo ou Jusbrasil Soluções) — mesma decisão pendente do item "Captura
paga" (agora com uma opção a mais na lista). Quando decidir, a integração fica rápida: só implementar
uma subclasse de `ConectorCaptura` contra a documentação real do provedor escolhido.

**Arquivos alterados:** `app/utils/extracao_documento.py` (novo), `app/utils/analise_processo_ia.py`,
`app/models/agente_ia.py`, `app/routes/processos.py`, `app/jobs/ia_jobs.py`,
`app/templates/processos/detalhe.html`, `requirements.txt`, `app/utils/captura_conectores.py`,
`app/utils/conector_datajud.py`, `app/routes/clientes.py`,
`app/templates/clientes/due_diligence.html` (novo), `app/templates/clientes/detalhe.html`,
`tests/test_referencia_estilo_minuta.py` (novo), `tests/test_due_diligence.py` (novo).

## -52. Exportação em massa de dados de um cliente

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (Compliance, Impacto médio, Esforço pequeno-médio) — item
DIFERENTE do "Ferramentas de LGPD" já entregue antes (seção -43, ainda
mais acima neste arquivo): aquele item deu o botão "Exportar dados
(LGPD)", que baixa um JSON com todo o CADASTRO estruturado do cliente
(processos, financeiro, horas, agenda). Mas isso nunca incluiu os
arquivos de documento em si — se o cliente pedisse "quero uma cópia de
tudo que vocês têm sobre mim", o JSON sozinho não entregava os
contratos, procurações e petições de verdade, só os NOMES deles. Esta é
exatamente a lacuna que "exportação em MASSA" cobre: um único .zip com
o cadastro estruturado **e** todos os arquivos reais anexados aos
processos do cliente.

**O que mudou:**

1) **`app/utils/lgpd.py`, função nova `montar_pacote_completo_cliente()`**
   — reaproveita a `montar_export_dados_cliente()` já existente pro JSON
   e monta um .zip em memória com `dados_lgpd.json` na raiz e uma pasta
   `documentos/<identificador do processo>/` por processo do cliente,
   com o arquivo de cada `Documento` real (não só o metadado). Dois
   documentos com o MESMO nome original (ex: duas versões de
   "procuracao.pdf") nunca se sobrescrevem dentro do zip — o segundo
   ganha um sufixo `(1)`, `(2)` etc. automaticamente.

2) **Resiliência a arquivo ausente no disco:** se o registro do
   documento existe no banco mas o arquivo sumiu do armazenamento (ex:
   alguém mexeu manualmente na pasta de uploads), a exportação inteira
   NÃO quebra — só aquele arquivo fica de fora, e entra um `AVISOS.txt`
   dentro do próprio zip listando o que não pôde ser incluído, pra quem
   gerou a exportação saber que faltou algo em vez de assumir que está
   tudo lá.

3) **Rota nova `GET /clientes/<id>/exportar-pacote-completo`** e botão
   "Exportar pacote completo (.zip)" na tela do cliente, ao lado do
   "Exportar dados (LGPD)" já existente — os dois continuam
   coexistindo, o JSON puro ainda serve pra quem só quer o dado
   estruturado (ex: importar em outro sistema) sem baixar um zip
   grande.

4) **Conecta com a auditoria de acesso a documentos (seção -51, item
   anterior desta mesma rodada):** baixar um documento dentro do pacote
   em massa conta como "baixou_documento" pra cada documento incluído —
   sem isso, a exportação em massa seria um jeito de baixar qualquer
   documento do cliente sem deixar rastro nenhum no histórico "quem
   baixou este documento", o que anularia a auditoria que acabou de ser
   entregue. Além disso, um log próprio (`exportou_pacote_completo_cliente`)
   registra a exportação em si, com quantos documentos entraram e
   quantos ficaram de fora por aviso.

**Testado:** 6 testes novos (`tests/test_exportacao_massa_cliente.py`)
— o zip inclui o JSON e os documentos de dois processos diferentes do
mesmo cliente, com o conteúdo real do arquivo (não só o nome); dois
documentos com nome igual não se sobrescrevem no zip; um documento sem
arquivo no disco não derruba a exportação e aparece no `AVISOS.txt`;
exportar registra o log da exportação em si E um log de download por
documento incluído (auditoria da seção -51 continua completa mesmo pra
esta forma de download); cliente sem nenhum documento gera um zip só
com o JSON, sem quebrar; e usuário de outra unidade leva 403, mesma
regra de escopo já aplicada em todo o resto do sistema. Rodei a suíte
inteira depois (90 testes) — sem regressão.

⚠️ **Ação sua necessária depois do deploy** — este lote não mexe em
nenhum modelo (não adiciona coluna nem tabela nova), então **não**
precisa rodar `sincronizar_schema.py`; não adiciona `.cron` novo, então
também **não** precisa de rebuild especial — só o `git push`/deploy de
sempre.

**Arquivos alterados:** `app/utils/lgpd.py`, `app/routes/clientes.py`,
`app/templates/clientes/detalhe.html`,
`tests/test_exportacao_massa_cliente.py` (novo).

## -51. Auditoria de acesso a documentos (quem baixou o quê)

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (LGPD/Governança, Impacto médio, Esforço baixo — hoje o sistema
já audita quem enviou e quem excluiu um documento, mas baixar um
documento não deixava rastro nenhum; qualquer pessoa com acesso ao
processo podia baixar qualquer documento sem que ninguém soubesse quem
nem quando). O escritório já tinha toda a infraestrutura de auditoria
pronta (`LogAtividade`/`registrar_log`, a mesma usada em login, exclusão
de dado, edição de alçada etc.) — este item foi conectar o download
nela e dar um jeito de consultar isso por documento.

**O que mudou:**

1) **`app/routes/processos.py`, `baixar_documento()`** — agora chama
   `registrar_log(current_user, "baixou_documento", "Documento", doc.id,
   doc.nome_original)` antes de servir o arquivo. Usei o id do próprio
   **Documento** (não do Processo, como os logs de upload/exclusão já
   existentes) de propósito: assim o histórico de UM documento nunca se
   mistura com o de outro, mesmo que dois arquivos do mesmo processo
   tenham o mesmo nome original (ex: duas versões de "procuracao.pdf"
   enviadas em datas diferentes) — testado explicitamente.

2) **Rota nova `GET /processos/documentos/<id>/historico`** — lista
   todo mundo que já baixou aquele documento específico, com data/hora,
   IP e navegador/dispositivo resumido (reaproveita a mesma função
   `resumir_user_agent` já usada na tela geral de Auditoria). Restrita a
   admin ou gestor — ver quem baixou o quê é informação sobre a
   ATIVIDADE de outras pessoas, não sobre o processo em si, então segue
   o mesmo critério de acesso já usado na aprovação de alçada financeira
   (seção -50): não é "todo mundo que vê o processo", é só quem tem
   papel de gestão.

3) **`app/templates/processos/historico_documento.html`** (novo) — a
   tela dessa rota nova.

4) **`app/templates/processos/detalhe.html`** — cada linha da tabela de
   documentos ganhou um botão "Histórico" (com a contagem de downloads
   entre parênteses, ex: "Histórico (3)"), visível só pra admin/gestor,
   ao lado do "Baixar" já existente.

5) **`app/routes/processos.py`, `detalhe()`** — pré-computa a contagem
   de downloads de todos os documentos do processo numa única consulta
   agregada (`GROUP BY`), em vez de uma consulta por documento na hora
   de montar a tabela.

O download em si nunca foi bloqueado nem ficou mais lento pra quem
baixa — só passou a deixar rastro. E como todo log novo cai também na
tabela `logs_atividade` de sempre, um download de documento também
aparece na tela geral de Auditoria (Configurações → Auditoria) se você
filtrar por usuário/data, junto com toda ação já registrada — a rota
nova só existe pra dar uma visão focada "quem baixou ESTE documento
aqui", sem precisar catar isso no meio de centenas de outras ações.

**Testado:** 7 testes novos (`tests/test_auditoria_documentos.py`) —
baixar um documento registra quem baixou; baixar o mesmo documento
várias vezes registra cada download separado; dois documentos com o
MESMO nome original nunca misturam o histórico um do outro; gestor
consegue ver o histórico com o nome de quem baixou (advogado e outro
gestor, nos dois casos); advogado comum leva 403 tentando abrir o
histórico de um documento; a tela de histórico funciona normalmente
mesmo sem nenhum download ainda registrado; e a tela do processo mostra
o link com a contagem certa só pra admin/gestor, nunca pra usuário
comum. Rodei a suíte inteira depois (84 testes) — sem regressão.

⚠️ **Ação sua necessária depois do deploy** — este lote **não** adiciona
coluna nem tabela nova (só reaproveita `logs_atividade`, que já existe),
então **não** precisa rodar `sincronizar_schema.py`; e não adiciona
`.cron` novo, então também **não** precisa de rebuild especial — só o
`git push`/deploy de sempre.

**Arquivos alterados:** `app/routes/processos.py`,
`app/templates/processos/detalhe.html`,
`app/templates/processos/historico_documento.html` (novo),
`tests/test_auditoria_documentos.py` (novo).

## -50. Alçada/aprovação em múltiplos níveis (financeiro)

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (Governança financeira, Alto impacto — hoje qualquer usuário com
acesso ao financeiro pode marcar uma despesa gigante como paga sozinho,
sem ninguém revisar antes do dinheiro sair, Esforço médio). A ideia é
simples e deliberadamente restrita ao que o item pede: um limite
configurável por empresa, em dois degraus, que passa a EXIGIR aprovação
de outra pessoa antes de uma despesa poder ser marcada como paga. Nada
disso mexe em receita, nem bloqueia o cadastro do lançamento — só o
"marcar como pago".

**Regra de negócio implementada:**

1. Cada empresa configura dois valores opcionais,
   `alcada_nivel1_valor` e `alcada_nivel2_valor` (em Configurações →
   Alçada de aprovação, só admin vê). Por padrão os dois ficam vazios —
   **desligado**, ninguém precisa configurar nada e o sistema continua
   se comportando exatamente como sempre se comportou.
2. Despesa até o nível 1 (ou sem alçada configurada): marca como pago
   direto, como sempre.
3. Despesa acima do nível 1 e até o nível 2 (ou sem nível 2
   configurado): precisa de **1 aprovação** de outra pessoa antes de
   poder ser paga.
4. Despesa acima do nível 2: precisa de **2 aprovações de pessoas
   diferentes**.
5. Só pode aprovar quem é admin ou gestor — um usuário comum, mesmo com
   acesso ao financeiro liberado (como o caso do "sócio" resolvido duas
   rodadas atrás), não aprova alçada.
6. Quem lançou a despesa nunca pode aprovar a própria — mesmo sendo
   admin ou gestor.
7. O mesmo aprovador nunca conta duas vezes: se ele tentar aprovar de
   novo, o sistema avisa que ele já aprovou e não soma uma segunda
   aprovação.
8. Existe também "Rejeitar" (motivo obrigatório) — cancela o lançamento
   (reaproveitei o status "cancelado" que já existia, não criei status
   novo) em vez de deixá-lo pendurado pendente pra sempre.
9. Receita **nunca** passa por nada disso, não importa o valor.

**O que mudou:**

1) **`app/models/empresa.py`** — duas colunas novas, `Numeric(14,2)`,
   `nullable=True` (nenhuma das duas tem valor por padrão — é assim que
   o recurso fica "desligado" sem precisar de uma flag separada).

2) **`app/models/financeiro.py`** — tabela nova `aprovacoes_lancamento`
   (modelo `AprovacaoLancamento`): quem aprovou, quando, comentário
   opcional. Relação `Lancamento.aprovacoes`.

3) **`app/utils/alcada.py`** (novo) — toda a regra de negócio
   concentrada aqui, sem duplicar lógica entre rota e template:
   `nivel_aprovacao_necessario`, `aprovacoes_faltando`,
   `pode_ser_marcado_pago`, `usuario_pode_aprovar` (com o motivo do
   bloqueio, pra mostrar mensagem específica em vez de um "não" seco).

4) **`app/routes/financeiro.py`**:
   - `novo()` — cadastro nunca é bloqueado; se a despesa nascer acima
     da alçada, só avisa (flash) quantas aprovações vai precisar antes
     de poder ser paga.
   - `atualizar_status()` — é o único ponto realmente bloqueado: se
     tentar marcar "pago" sem ter reunido as aprovações necessárias, o
     sistema recusa e explica quantas ainda faltam.
   - rota nova `GET /financeiro/aprovacoes` — lista central de tudo que
     está esperando aprovação, com botão de aprovar (só aparece pra
     quem pode) e de rejeitar.
   - rotas novas `POST /financeiro/<id>/aprovar` e
     `POST /financeiro/<id>/rejeitar-alcada`.
   - `listar()` — cada linha de despesa pendente que está esperando
     alçada mostra "Aguardando alçada (N)" no lugar do botão "Marcar
     pago", com link direto pra tela de aprovação; e um contador no
     topo da tela avisa quantas aprovações estão pendentes no total
     (contagem real, não só da página filtrada atual).

5) **`app/routes/admin.py`** — tela nova
   `/admin/alcada-aprovacao` (só admin, inclusive admin da
   plataforma) pra configurar os dois valores. Aceita vírgula ou ponto
   decimal. Não deixa configurar o nível 2 sem o nível 1 preenchido
   também (não faria sentido pular um degrau), e o nível 2 precisa ser
   maior que o nível 1.

6) **`app/templates/admin/alcada_aprovacao.html`** (novo),
   **`app/templates/financeiro/aprovacoes.html`** (novo),
   **`app/templates/financeiro/listar.html`** e **`app/templates/base.html`**
   (link novo no menu Gestão) — telas e ajustes de interface pra tudo
   acima.

**Testado:** 12 testes novos (`tests/test_alcada_aprovacao.py`) —
sem alçada configurada marca pago direto igual sempre; receita nunca é
bloqueada mesmo com valor gigante; despesa abaixo do nível 1 paga
direto; despesa entre nível 1 e 2 fica bloqueada até 1 aprovação de
outra pessoa; despesa acima do nível 2 exige 2 aprovações de usuários
DISTINTOS (uma só não libera); o mesmo usuário aprovando duas vezes não
conta como duas aprovações; quem lançou a despesa não consegue aprovar
a própria; advogado comum (sem papel de gestão) não consegue aprovar;
rejeitar cancela o lançamento; configurar a alçada pela tela de admin
salva certo; configurar nível 2 sem nível 1 é recusado; e criar uma
despesa acima da alçada mostra o aviso no cadastro sem bloquear a
criação. Rodei a suíte inteira depois (77 testes) — sem regressão.

⚠️ **Pegadinha encontrada e corrigida só nos TESTES** (não é bug do
sistema): a rota `/login` redireciona direto pro painel se o usuário já
estiver autenticado — comportamento correto e sempre existiu. Isso
quebrou meus primeiros testes que tentavam trocar de usuário (ex:
aprovar como gestor depois de já estar logado como admin) sem antes
fazer `/logout`. Corrigido nos testes; a aplicação em si nunca teve
esse problema.

⚠️ **Ação sua necessária depois do deploy** — este lote adiciona duas
colunas novas em `empresas` (`alcada_nivel1_valor`,
`alcada_nivel2_valor`) **e** uma tabela inteira nova
(`aprovacoes_lancamento`). Depois do `git push`/deploy de sempre,
entre no Terminal/Console do container no EasyPanel e rode:

```
python sincronizar_schema.py
```

Isso cria a tabela nova e adiciona as duas colunas novas sem tocar em
nada que já existe (`sincronizar_schema.py` só cria tabela/coluna que
falta, nunca apaga nem torna nada obrigatório). Não adiciona nenhum
`.cron` novo, então **não** precisa de rebuild especial — só o
`sincronizar_schema.py` depois do deploy normal.

Sem configurar nenhum valor de alçada, o sistema continua se
comportando exatamente como hoje — nenhuma despesa pede aprovação até
você decidir ligar isso em Configurações → Alçada de aprovação.

**Arquivos alterados:** `app/models/empresa.py`,
`app/models/financeiro.py`, `app/models/__init__.py`,
`app/utils/alcada.py` (novo), `app/routes/financeiro.py`,
`app/routes/admin.py`, `app/templates/admin/alcada_aprovacao.html`
(novo), `app/templates/financeiro/aprovacoes.html` (novo),
`app/templates/financeiro/listar.html`, `app/templates/base.html`,
`tests/test_alcada_aprovacao.py` (novo).

## -49. Monitoramento de erros (Sentry ou similar)

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (Escala, Alto impacto — hoje um erro em produção só é descoberto
se alguém reclamar, ou por acaso, Baixo esforço). Escolhi Sentry (é o
próprio nome sugerido no item da tabela) — tem plano gratuito que já
cobre um volume razoável pra um escritório, integra direto com Flask, e
segue o MESMO padrão de credencial opcional já usado em todo o resto do
projeto (SMTP, WhatsApp, DataJud): sem configurar nada, o sistema roda
exatamente igual a antes — só não reporta erro pra lugar nenhum.

**O que mudou:**

1) **`app/utils/monitoramento.py`** (novo) — único ponto de entrada.
   `inicializar_sentry(app)` só faz alguma coisa se `SENTRY_DSN` estiver
   definida; senão, retorna sem tocar em nada. Chamada logo no início de
   `create_app()` — cobre, com o mesmo ponto único, os workers do
   gunicorn (processo web), o worker da fila de IA em segundo plano
   (`app/jobs/ia_jobs.py`, que também chama `create_app()`) e todos os
   scripts `.cron` já existentes (`enviar_lembretes_*.py`,
   `capturar_movimentacoes.py` etc.) — nenhum precisou ser alterado.

2) **Cuidado deliberado com dado pessoal (LGPD):** `send_default_pii=False`
   (nunca manda IP, cookie ou corpo bruto da requisição automaticamente)
   + uma função `before_send` que raspa à força qualquer campo de
   formulário que ainda viesse sensível (senha, csrf_token, token de
   API, CPF/CNPJ, cookie de sessão inteiro) do contexto de um evento,
   como segunda camada de proteção mesmo com `send_default_pii`
   desligado. O usuário logado é identificado pro Sentry só por ID
   numérico + papel + empresa/unidade — nunca nome, e-mail ou qualquer
   outro dado pessoal do usuário ou de cliente.

3) **`app/__init__.py`** — chama `inicializar_sentry(app)` no início de
   `create_app()`; um `before_request` novo identifica o usuário logado
   pro Sentry a cada requisição (`identificar_usuario_atual()`, no-op se
   o Sentry não estiver ativo); e um `@app.errorhandler(500)` novo (não
   existia nenhum antes) troca a página de erro padrão feia do
   Flask/Werkzeug por uma consistente com o resto do sistema, sem vazar
   traceback nem caminho de arquivo interno pro usuário — o Sentry já
   captura o erro de verdade ANTES deste handler entrar em ação, ele só
   cuida da resposta que o usuário vê.

4) **`app/jobs/ia_jobs.py`** — os dois jobs de IA em segundo plano
   (mensagem do Agente de IA e Análise de processo) já tratavam erro com
   carinho de propósito, pra nunca deixar nada travado em "processando"
   pra sempre (viram uma mensagem amigável pro usuário em vez de
   quebrar). Isso tinha um efeito colateral: o RQ nunca via esses jobs
   como "falhos", então um bug de verdade ali NUNCA apareceria em lugar
   nenhum. Acrescentei `sentry_sdk.capture_exception(e)` nos dois blocos
   `except Exception` (só nesses — os outros `except` cobrem situação
   esperada tipo "IA indisponível", não bug) pra manter a visibilidade
   sem abrir mão do tratamento gentil pro usuário.

5) **`config.py`** — `SENTRY_DSN`, `SENTRY_ENVIRONMENT` (padrão
   `"producao"`), `SENTRY_RELEASE` (opcional) e
   `SENTRY_TRACES_SAMPLE_RATE` (rastreamento de PERFORMANCE, não de
   erro — fica em `0` por padrão de propósito, ligar isso consome a cota
   gratuita do Sentry bem mais rápido e o objetivo aqui é capturar erro,
   não performance).

6) **`requirements.txt`** — `sentry-sdk[flask]==2.68.0`. ⚠️ Isso é uma
   dependência NOVA de verdade (ao contrário do item anterior, que só
   mexeu em `requirements-dev.txt`) — precisa entrar na imagem de
   produção.

7) **`.env.example`** — documentado o `SENTRY_DSN` e as variáveis
   opcionais, mesmo padrão de comentário do resto do arquivo.

**Testado:** 7 testes novos (`tests/test_monitoramento_erros.py`) —
sem `SENTRY_DSN`, `inicializar_sentry()` nunca chama `sentry_sdk.init()`
(confirmado via `sentry_sdk.is_initialized()`); com `SENTRY_DSN`
configurada, o SDK é inicializado de verdade (sem nunca disparar uma
captura real — os testes verificam só a inicialização, pra não fazer
nenhuma chamada de rede de verdade contra o Sentry durante o CI); a
raspagem de campo sensível (`_before_send`) tira senha/token/csrf/CPF de
dict aninhado e lista, e sempre remove o bloco de cookies por inteiro
(achei isso testando — meu primeiro código só raspava por NOME de campo,
o que não pega o nome de um cookie de sessão tipo "session", que pode
ser qualquer coisa; corrigido pra sempre remover o bloco de cookies
inteiro, nunca confiar em bater nome); o hook de identificação de
usuário não quebra nenhuma requisição normal sem Sentry ativo; e um
teste força de propósito uma exceção de verdade dentro da rota do Painel
(via `monkeypatch`) pra confirmar que a página de erro 500 do próprio
sistema aparece, sem vazar `RuntimeError`/traceback pro usuário. Rodei a
suíte inteira (65 testes) depois disso — sem regressão, e sem nenhum
teste anterior quebrar por causa do estado global do SDK do Sentry
(cada teste que liga o Sentry desliga de novo no final, de propósito).
Também testei fora do pytest, subindo `create_app()` em processos
separados (simulando o comportamento real de produção, onde a variável
de ambiente já existe ANTES do processo Python subir — mesma pegadinha
de `config.py` já documentada pros outros itens: `SENTRY_DSN`, assim
como `DATABASE_URL`, só é lida na primeira vez que `config.py` é
importado no processo): sem a variável, sobe normal e Sentry fica
desligado; com a variável, sobe normal e Sentry inicializa de verdade.

⚠️ **Ação sua necessária depois do deploy** — isso é diferente de todos
os outros itens até agora, porque exige uma conta externa pra realmente
começar a captar erro (o código funciona sozinho, mas sem isso, continua
em modo "desligado"):

1. Crie uma conta gratuita em https://sentry.io (ou, se preferir, use
   outra ferramenta compatível com o protocolo do Sentry — o SDK
   `sentry-sdk` funciona com qualquer serviço que fale esse protocolo,
   não só o Sentry.io hospedado).
2. Crie um projeto do tipo "Flask" (ou "Python" genérico).
3. Copie a DSN que ele mostrar e cole na variável de ambiente
   `SENTRY_DSN` do serviço no EasyPanel.
4. Redeploy normal (`git push`) — como este lote adiciona dependência
   NOVA em `requirements.txt` (não só em `requirements-dev.txt`), o
   EasyPanel já reconstrói a imagem do zero em qualquer redeploy comum,
   então não precisa de nenhum passo extra além do de sempre. Não
   adiciona coluna nova nenhuma (não mexe em nenhum modelo), então
   **não** precisa rodar `sincronizar_schema.py`; não adiciona `.cron`
   novo, então **não** precisa de rebuild "especial" nenhum — só o
   `git push` de sempre já cobre a dependência nova.

Sem fazer os passos 1 a 3 acima, o sistema continua funcionando
exatamente como está hoje — só sem reportar erro nenhum, como sempre foi.

**Arquivos alterados:** `app/utils/monitoramento.py` (novo),
`app/__init__.py`, `app/jobs/ia_jobs.py`, `config.py`,
`requirements.txt`, `.env.example`,
`tests/test_monitoramento_erros.py` (novo).

## -48. Testes automatizados / CI

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (Escala, Alto impacto em segurança contra regressão, Médio
esforço). Até esta rodada, todo teste feito neste projeto era um script
avulso descartável (rodado com `python3 arquivo.py` e depois jogado
fora) — funcionava pra validar cada entrega na hora, mas nada ficava
guardado pra rodar de novo automaticamente quando um código futuro
mexesse sem querer em alguma regra já validada. Este item troca isso por
uma suíte de testes de verdade (`tests/`, framework `pytest`) que fica
no repositório e roda sozinha a cada `git push`/pull request via GitHub
Actions.

**O que mudou:**

1) **`tests/conftest.py`** — a infraestrutura compartilhada de toda a
   suíte: sobe a aplicação Flask uma única vez contra um banco SQLite
   descartável (nunca o MySQL de produção, nunca precisa de credencial
   nenhuma), com `db.create_all()`/`drop_all()` isolando cada teste
   individualmente mesmo rodando todos no mesmo processo. Fixtures
   prontas pra todo teste reaproveitar: `client` (cliente HTTP de
   teste), `login(email)` (loga de verdade, extraindo o csrf_token real
   da tela de login), `post_csrf(url, dados)` (POST com csrf_token
   válido, sem precisar escrever a extração do token em cada teste),
   `empresa_basica` (empresa + licença ativa + unidade — o mínimo que
   quase todo teste precisa, já que sem licença ativa o middleware de
   licenciamento bloqueia qualquer rota com 402) e `criar_usuario`
   (cria usuário de teste com senha já criptografada).

2) **`tests/test_smoke.py`** — confirma que a própria infraestrutura de
   teste funciona (app sobe, login funciona, cada teste começa com
   banco vazio de verdade).

3) **Portados pra testes permanentes** (antes eram scripts avulsos em
   `/tmp`, feitos e descartados durante o desenvolvimento de cada item
   anterior da tabela): `test_rbac_financeiro.py` (seção -45),
   `test_desligamento_usuario.py` (seção -46), `test_paginacao.py`
   (seção -47), `test_lgpd.py`, `test_conflito_interesse.py`,
   `test_modelos_cobranca.py`, `test_relatorio_financeiro_area.py`,
   `test_timesheet_faturamento.py` (cobre CSRF em várias telas +
   conta_terceiros + gerar cobrança por horas), e
   `test_lembretes_prazos_audiencias.py` (testa o próprio script
   agendado `.cron`, chamando a função Python diretamente). No total,
   **58 testes** cobrindo o que já tinha sido validado manualmente em
   rodadas anteriores.

4) **`requirements-dev.txt`** — só acrescenta `pytest` em cima do
   `requirements.txt` de sempre; nunca instalado na imagem de produção
   (o `Dockerfile` continua usando só `requirements.txt`).

5) **`pytest.ini`** — configura `pytest` pra descobrir os testes em
   `tests/` automaticamente, sem precisar passar caminho na mão.

6) **`.github/workflows/tests.yml`** — roda a suíte inteira a cada
   `git push` e a cada pull request, em qualquer branch. O passo de
   instalação espelha de propósito a mesma linha de `pip install` do
   `Dockerfile` (mesmo `--extra-index-url` do wheel pré-compilado do
   `llama-cpp-python`), pra pegar cedo qualquer incompatibilidade de
   dependência que só apareceria no build de produção — sem precisar
   subir MySQL nenhum no CI, já que a suíte inteira roda em SQLite.

**Sobre um bug real encontrado ao escrever os testes:** o teste que
confirma que o admin da plataforma (dono do sistema, enxerga todas as
empresas clientes) nunca pode escolher um substituto de reatribuição de
outra empresa (seção -46) estava mirando no usuário errado — testava a
tela de desligamento de um usuário SEM nenhuma pendência, e a tela só
monta a lista de candidatos a substituto quando existe pendência de
verdade pra reatribuir. Corrigido apontando o teste pro usuário certo
(o que tem processos/prazos/tarefas em aberto); não era um bug do
sistema, só do próprio teste anterior — mas só foi pego escrevendo o
teste de verdade em vez de rodar o script avulso uma vez e confiar.

**Testado:** os 58 testes rodam juntos, na mesma suíte, sem interferir
uns nos outros (isolamento por teste confirmado mesmo com toda a
infraestrutura — app, banco, csrf — compartilhada entre eles pra não
pagar o custo de recriar tudo a cada teste). Rodei tanto `pytest tests/`
quanto só `pytest` (sem argumento nenhum, exatamente como o CI vai
rodar) direto da raiz do projeto, pra confirmar que o `pytest.ini`
resolve o caminho sozinho.

⚠️ **Regra nova pra manter ao adicionar teste novo no futuro:** nunca
fazer `from tests.conftest import algumacoisa` dentro de um arquivo de
teste — isso faz o `pytest` carregar `conftest.py` duas vezes sob nomes
diferentes, o que reexecuta a configuração do banco no meio da suíte e
corrompe a conexão SQLite compartilhada (erro observado durante o
desenvolvimento: `sqlite3.OperationalError: attempt to write a readonly
database`). Tudo que um teste precisa de `conftest.py` tem que estar
exposto como fixture (`@pytest.fixture()`), nunca como função importável
direto.

Este lote não adiciona coluna nova nenhuma nem `.cron` novo (só testes e
config de CI) — depois do deploy, só o `git push` de sempre, sem
precisar rodar `sincronizar_schema.py` nem rebuild completo. Rodar a
suíte não é obrigatório pra produção funcionar — é rede de segurança pra
mudança futura, não faz parte do runtime da aplicação.

**Arquivos novos:** `tests/conftest.py`, `tests/test_smoke.py`,
`tests/test_rbac_financeiro.py`, `tests/test_desligamento_usuario.py`,
`tests/test_paginacao.py`, `tests/test_lgpd.py`,
`tests/test_conflito_interesse.py`, `tests/test_modelos_cobranca.py`,
`tests/test_relatorio_financeiro_area.py`,
`tests/test_timesheet_faturamento.py`,
`tests/test_lembretes_prazos_audiencias.py`, `requirements-dev.txt`,
`pytest.ini`, `.github/workflows/tests.yml`.

**Não incluído nesta rodada:** cobertura de captura DataJud, fila de
triagem, Agente de IA e outras áreas que já tinham script avulso de
teste em rodadas bem anteriores a esta (antes da tabela de prioridades
atual) não foram portadas pra `tests/` — só o que foi construído/testado
nas rodadas mais recentes (RBAC financeiro, desligamento, paginação,
LGPD, conflito de interesses, modelos de cobrança, relatório por área,
timesheet e lembretes). Se fizer sentido portar o resto também, é só
pedir — o padrão de fixtures em `conftest.py` já está pronto pra
reaproveitar.

## -47. Paginação em listas grandes (processos, painel)

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (Escala, Alto impacto em volume real de escritório grande, Médio
esforço). Levantamento (`grep ".all()"` em todo `app/routes/`) achou
várias telas montando a query certa, filtrada e ordenada, mas
carregando a tabela INTEIRA de uma vez, sem limite — o problema mais
grave em **Processos** (`processos.listar`, a listagem principal do
sistema) e no **Painel de governança** (`governanca.painel`).

⚠️ **Importante sobre como testei isto:** meu ambiente de testes
(sandbox) não tinha cópia local de `processos/listar.html`,
`clientes/listar.html` nem `governanca/fila_intimacoes.html` (mesmo
problema já avisado antes, nas seções -26/-28). Desta vez, em vez de
editar às cegas ou reconstruir esses arquivos do zero, busquei a versão
ATUAL de cada um direto do seu computador (a pasta já está conectada)
antes de mexer — então as mudanças abaixo foram feitas em cima do
arquivo real, não de uma suposição.

**O que mudou:**

1) **Utilitário novo e único** (`app/utils/paginacao.py`) — `paginar(query)`
   aplica paginação de verdade (Anterior/Próxima) lendo `?pagina=N` e
   `?por_pagina=N` da própria URL, sempre limitado a no máximo 100 por
   página mesmo que alguém edite a URL na mão, e nunca quebra a tela com
   erro por causa de um número de página inválido (`error_out=False` —
   página fora do intervalo só mostra lista vazia). Um segundo helper,
   `limitar_com_total(query, teto=50)`, é pra widget de dashboard (não
   uma lista navegável): devolve os N primeiros itens JUNTO com o total
   real, pra tela poder avisar "mostrando 50 de 312" honestamente.

2) **Processos** (`processos.listar`) — agora paginado, 25 por página
   por padrão. Os filtros já existentes (status, área, busca, unidade)
   continuam funcionando normalmente e são preservados ao trocar de
   página.

3) **Clientes** (`clientes.listar`) — mesmo tratamento (achei durante o
   levantamento — mesma classe de risco que Processos, não estava no
   escopo original do item mas é a mesma causa).

4) **Fila de intimações** (`governanca.fila_intimacoes`) — a "tela de
   trabalho do dia" (todo prazo em aberto do escopo, sem filtro nenhum
   antes disto) também paginada, mesmo padrão.

5) **Painel de governança** (`governanca.painel`) — tratamento diferente
   dos itens acima, porque aqui a maioria dos números é só ESTATÍSTICA
   (cartão com uma contagem), não uma tabela navegável:
   - Os 3 cartões "Prazos fatais — 7 dias / 8 a 15 dias / vencidos sem
     evidência" viravam uma lista inteira carregada na memória só pra
     contar o tamanho (`|length` no template) — trocado por `.count()`
     direto no banco, sem trazer nenhuma linha.
   - "Processos não monitoráveis automaticamente" é a única lista deste
     painel que É mesmo desenhada linha a linha — essa usa
     `limitar_com_total` (top 50 mais recentes + total real), com aviso
     "mostrando os 50 mais recentes de N" quando ultrapassa o teto, e
     link pra tela de Processos (já paginada) pra ver o resto.

**Testado (sqlite descartável + login real via `test_client` HTTP, 8
cenários, com massa de dados grande de propósito — 40 processos, 30
clientes, 30 prazos, 51 processos não monitoráveis):** página 1 de
Processos mostra 25 linhas e o total certo; página 2 mostra outro
conjunto; filtro de status combinado com paginação funciona e o link de
"Próxima" preserva o filtro na URL; página inválida (além do total,
texto não-numérico, negativa) nunca quebra a tela; `por_pagina`
customizado funciona e é limitado ao teto mesmo com valor absurdo na
URL; Clientes e Fila de intimações mostram o total certo; Painel de
governança mostra o total REAL (51) mesmo listando só os 50 primeiros,
com o aviso de truncamento aparecendo. Regressão completa: todos os
scripts de teste de rodadas anteriores (lembretes, LGPD, conflito de
interesses, relatório por área, modelos de cobrança, timesheet/
faturamento, RBAC financeiro, desligamento de usuário) rodados de novo
depois desta mudança — todos continuam passando.

Este lote não adiciona coluna nova nenhuma (é só código de leitura,
nada no modelo de dados muda) — depois do deploy, só o `git push` de
sempre, sem precisar rodar `sincronizar_schema.py` nem rebuild completo.

**Arquivos alterados:** `app/utils/paginacao.py` (novo), `app/__init__.py`
(registra `url_pagina` como global do Jinja), `app/templates/_paginacao.html`
(novo — partial reutilizável de controles Anterior/Próxima),
`app/routes/processos.py`, `app/routes/clientes.py`, `app/routes/governanca.py`
(rotas `fila_intimacoes` e `painel`), `app/templates/processos/listar.html`,
`app/templates/clientes/listar.html`, `app/templates/governanca/fila_intimacoes.html`,
`app/templates/governanca/painel.html`.

**Não incluído nesta rodada (mesma classe de problema, fora do escopo
explícito deste item — "processos, painel"):** listagens de Financeiro,
Tarefas e Timesheet também usam `.all()` sem limite. Se algum escritório
já sentir lentidão nessas telas específicas antes de eu chegar nelas na
tabela, é só avisar que aplico o mesmo `paginar()` — a infraestrutura já
está pronta, é reaproveitar.

## -46. Reatribuição de casos no desligamento de usuário

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (Operação, Médio impacto, Pequeno esforço). Antes desta rodada,
desativar um usuário (desmarcar "Usuário ativo" em Configurações →
Usuários) não fazia nenhuma verificação: o usuário virava inativo mesmo
tendo processo, prazo, audiência, tarefa ou compromisso futuro sob a
responsabilidade dele — o registro continua existindo, mas fica "órfão",
sem ninguém que consiga logar pra tratar aquilo nem receber lembrete
(o lembrete de prazo/audiência da seção -44, por exemplo, simplesmente
para de disparar pra esse item, porque o `responsavel_id` aponta pra
alguém inativo).

**O que mudou:**

1) **Checagem antes de desligar** (`app/utils/desligamento.py`, novo) —
   `itens_em_aberto(usuario_id)` conta, por categoria, quanto ainda está
   em aberto sob a responsabilidade do usuário: processo ativo, prazo
   pendente (não conta "cumprido", "perdido" nem "historico_anterior" —
   esse último é propositalmente neutro, ver docstring de `Prazo`),
   audiência agendada, tarefa pendente/em andamento, e compromisso
   agendado ainda no futuro (um compromisso que já passou da hora não
   muda mais nada sendo reatribuído).

2) **Desligamento direto continua funcionando** pelo mesmo checkbox de
   sempre (`admin/usuario_form.html`) quando o usuário **não** tem nada
   em aberto — não criei fricção nenhuma pro caso comum (alguém que já
   entregou tudo antes de sair).

3) **Quando HÁ algo em aberto**, desmarcar o checkbox não desliga mais
   sozinho: os outros campos do formulário (nome, telefone, papel etc.)
   são salvos normalmente, mas o usuário continua ativo, com um aviso
   explicando quantos itens de cada tipo estão pendentes e um link pra
   tela nova, dedicada.

4) **Tela nova "Desligar usuário"** (`/admin/usuarios/<id>/desligar`,
   `app/routes/admin.py::desligar_usuario` +
   `admin/desligar_usuario.html`) — lista as pendências por categoria,
   exige escolher um substituto (dropdown com usuário ativo dentro do
   escopo de quem está desligando — gestor só vê a própria unidade,
   admin só a própria empresa) e marcar uma caixa de ciência antes de
   confirmar. Ao confirmar, numa única transação: todo item EM ABERTO
   (mesmos filtros do passo 1) muda de responsável pro substituto
   escolhido, e só depois o usuário é marcado como inativo — nunca fica
   num estado parcial (ou os dois acontecem juntos, ou nenhum). Fica
   registrado no log de atividade tanto a reatribuição (quantos itens de
   cada tipo, de quem pra quem) quanto o desligamento em si.

5) **Nunca mexe em campo de auditoria histórica** — `criado_por_id`
   (Tarefa, Compromisso), `alterado_por_id`/`regularizado_por_id`
   (Prazo) continuam apontando pra quem realmente criou/alterou o
   registro; só `responsavel_id` (quem é o dono AGORA) é reatribuído.
   Pelo mesmo motivo, item já FECHADO (processo encerrado, prazo
   cumprido/perdido/histórico, audiência cancelada, tarefa
   concluída/cancelada, compromisso já realizado ou no passado)
   continua com o nome do usuário desligado — reescrever isso apagaria
   rastro real do que aconteceu.

6) **Isolamento multi-tenant reforçado** — mesmo o admin desenvolvedor
   (que enxerga usuário de qualquer empresa cliente) só pode escolher um
   substituto da MESMA empresa de quem está sendo desligado. Sem essa
   trava extra, dava pra um processo de uma empresa cliente acabar
   reatribuído pra alguém de outra empresa cliente — quebraria o
   isolamento entre tenants que o resto do sistema toma tanto cuidado
   pra manter (ver `app/utils/acesso.py`).

**Testado (sqlite descartável + login real via `test_client` HTTP, 11
cenários):** desligamento direto funciona quando não há pendência;
desligamento fica bloqueado quando há pendência, mas os outros campos do
formulário são salvos mesmo assim; a tela de desligamento mostra a
contagem certa por categoria (e confirma que item já fechado/histórico/
passado NÃO entra na contagem); gestor só vê candidato a substituto da
própria unidade; POST sem escolher substituto não desliga; POST sem
marcar a caixa de ciência não desliga; desligamento completo move só os
itens em aberto pro substituto e deixa os fechados intocados com o
usuário antigo; ninguém consegue desligar o próprio usuário por essa
tela; tentar desligar quem já está inativo só avisa, sem erro; usuário
comum (não admin/gestor) recebe 403 na tela; admin desenvolvedor não
consegue escolher substituto de outra empresa cliente mesmo enxergando
todo mundo. Regressão completa: todos os scripts de teste de rodadas
anteriores (lembretes, LGPD, conflito de interesses, relatório por área,
modelos de cobrança, timesheet/faturamento, RBAC financeiro) rodados de
novo depois desta mudança — todos continuam passando.

Este lote não adiciona coluna nova nenhuma (só usa campos que já
existiam) — depois do deploy, só o `git push` de sempre, sem precisar
rodar `sincronizar_schema.py` nem rebuild completo desta vez.

**Arquivos alterados:** `app/utils/desligamento.py` (novo — contagem e
reatribuição), `app/routes/admin.py` (checagem em `editar_usuario` +
rota nova `desligar_usuario`), `app/templates/admin/usuario_form.html`
(aviso + botão "Desligar usuário"), `app/templates/admin/
desligar_usuario.html` (novo — tela de confirmação).

## -45. Papel financeiro/sócio dedicado no RBAC — dado financeiro deixou de ser visível pra todo mundo

**Contexto:** próximo item da tabela de prioridades do relatório de
20/08 (Permissões, Alto impacto — "dado financeiro hoje aberto a todo
mundo"). Levantamento confirmou o problema: TODA rota de
`app/routes/financeiro.py` só exigia `@login_required` — sem checagem de
papel nenhuma —, então qualquer usuário logado (inclusive
funcionário/estagiário) via lançamento financeiro, valor de honorário,
recibo, tudo, contanto que estivesse na mesma unidade. Achei mais dois
lugares vazando o mesmo tipo de dado que não estavam no escopo original
do item, mas são a mesma falha: o card de resumo por unidade no painel
principal (`dashboard/index.html`) já era só-admin (esse já estava
certo), e a persona **"Agente de Negócios"** do Agente de IA (pensada
pra sócio) — que expunha receita a receber/atrasada pra QUALQUER usuário
logado, bastava escolher essa persona na tela do agente, sem nenhuma
checagem de papel.

**O que mudou:**

1) **Checagem única de acesso financeiro** — `Usuario.pode_ver_financeiro`
   (novo, em `app/models/usuario.py`): `True` pra `admin`/`gestor`
   sempre (já é esperado desses dois papéis — o próprio `gestor` já tem
   "financeiro da unidade" na descrição do papel), ou pra qualquer outro
   usuário com o novo campo `acesso_financeiro=True` concedido
   explicitamente.

2) **Novo campo** `Usuario.acesso_financeiro` (booleano, opcional) — a
   forma de dar acesso financeiro a alguém que não é admin/gestor (ex:
   um sócio que atua como advogado, sem ser o gestor da unidade). Some
   usuário admin ou gestor consegue marcar essa caixa no cadastro/edição
   de usuário (`admin/usuario_form.html`) — nunca é automático.

3) **Novo decorador** `requer_acesso_financeiro`
   (`app/utils/acesso.py`), aplicado em TODA rota de
   `app/routes/financeiro.py` (listar, novo, gerar cobrança de horas,
   atualizar status, duplicar retainer, recibo).

4) **Agente de IA "Negócios"** (`app/routes/agente_ia.py`) — criar uma
   conversa nova nessa persona agora exige `pode_ver_financeiro`; abrir
   uma conversa já existente nessa persona ou mandar mensagem nela
   também exige (cobre o caso de um usuário que tinha acesso quando
   criou a conversa mas teve o acesso revogado depois — o histórico
   antigo não vaza o dado de novo). O cartão "Nova conversa" dessa
   persona só aparece pra quem tem acesso; conversas antigas continuam
   listadas em "Suas conversas" pelo título (não seria certo elas
   simplesmente sumirem do histórico), só não abrem mais se o acesso não
   estiver mais lá.

5) **Menu lateral** — o item "Financeiro" só aparece pra quem tem
   `pode_ver_financeiro` (`app/templates/base.html`).

⚠️ **Mudança de comportamento real, não só um ajuste de tela — importante
avaliar antes do deploy:** depois desta rodada, um advogado ou
funcionário comum (que não seja gestor/admin) **perde** o acesso que
tinha à aba Financeiro, à geração de cobrança de horas, ao recibo em PDF
e à persona "Negócios" do Agente de IA, a menos que alguém marque a
caixa "Acesso a dados financeiros" no cadastro dele. Se hoje algum
advogado sócio (que não é o gestor cadastrado da unidade) depende de ver
essas telas no dia a dia, é preciso ou (a) marcar essa caixa pra ele em
Configurações → Usuários → editar, ou (b) promovê-lo a `gestor`, o que
já dá esse acesso automaticamente mas também dá permissão de gerenciar
a equipe da unidade (nem sempre é o que se quer). Vale revisar a lista
de usuários depois do deploy pra confirmar que ninguém que precisa do
acesso ficou de fora.

**Testado (sqlite descartável + login real via `test_client` HTTP, 8
cenários):** admin acessa financeiro; gestor acessa financeiro; advogado
comum recebe 403 em TODAS as rotas do blueprint financeiro (listar,
novo, recibo) e não vê o link "Financeiro" no menu; funcionário recebe
403; advogado com `acesso_financeiro=True` acessa financeiro normalmente
e vê o link no menu; advogado comum não consegue criar conversa
"negócios" no Agente de IA (403) nem vê o cartão dela na tela; advogado
comum com uma conversa "negócios" antiga (criada antes desta rodada) não
consegue mais reabri-la nem mandar mensagem nela (403 nos dois casos).
Regressão completa: todos os scripts de teste de rodadas anteriores
(lembretes, LGPD, conflito de interesses, relatório por área, modelos de
cobrança, timesheet/faturamento) rodados de novo depois desta mudança —
todos continuam passando, nenhuma regressão.

Bug pego durante o próprio teste, corrigido antes de entregar: a
primeira versão desta mudança filtrava o dicionário de personas do
Agente de IA ANTES de mandar pro template, o que quebrava a tela
inteira (erro 500) pra qualquer usuário sem acesso financeiro que já
tivesse uma conversa "negócios" antiga no histórico — o template tentava
achar o título dela num dicionário de onde "negócios" tinha sido
removido. Corrigido mandando o dicionário completo pro template (pra
"Suas conversas" sempre achar o título de qualquer conversa antiga) e
filtrando só a lista usada pra desenhar os cartões de "nova conversa".

⚠️ **Este lote adiciona uma coluna nova** (`Usuario.acesso_financeiro`,
opcional, nenhum dado existente é afetado — todo usuário existente
começa sem a concessão extra, valendo só a regra por papel de
admin/gestor). Depois do deploy: `git push` de sempre + rodar
`python sincronizar_schema.py` no Terminal do container. Não mexe em
nenhum `.cron`/Dockerfile — não precisa de rebuild completo desta vez,
um redeploy normal já é suficiente.

**Arquivos alterados:** `app/models/usuario.py` (coluna
`acesso_financeiro` + propriedade `pode_ver_financeiro`),
`app/utils/acesso.py` (decorador `requer_acesso_financeiro`),
`app/routes/financeiro.py` (decorador aplicado em todas as rotas),
`app/routes/agente_ia.py` (checagem na persona "negócios" em 3 pontos +
filtro do cartão de nova conversa), `app/routes/admin.py` (salva o campo
no cadastro/edição de usuário), `app/templates/base.html` (item de menu
condicional), `app/templates/agente_ia/index.html` (cartões filtrados),
`app/templates/admin/usuario_form.html` (checkbox novo + texto
explicativo).

## -44. Lembrete de prazo/audiência por WhatsApp/e-mail

**Contexto:** próximo item da tabela (Comunicação, Alto impacto — "risco
de perda de prazo") depois das ferramentas de LGPD (seção -43).

O sistema já tinha lembrete automático pra Compromisso da Agenda (seção
-32/anterior), mas não pra **prazo processual** nem pra **audiência** —
os dois eventos com o risco mais sério de esquecimento (perder prazo
processual é um dos piores erros que um escritório pode cometer). Este
item fecha essa lacuna, reaproveitando a mesma infraestrutura de envio
(notificação in-app, e-mail, WhatsApp) já usada pelos lembretes de
compromisso.

**Como funciona:** um script novo,
`enviar_lembretes_prazos_audiencias.py`, roda **uma vez por dia** (6h da
manhã) dentro do próprio container via cron (mesmo mecanismo já usado
pela recaptura diária do DataJud e pelos lembretes de compromisso — não
depende de nenhuma configuração externa de agendamento). Diferente de
Compromisso (onde você escolhe um horário exato de lembrete), aqui a
regra é "faltam N dias pra vencer": por padrão, **2 dias antes** para
prazo e **1 dia antes** para audiência — ajustável sem mexer em código,
só trocando as variáveis de ambiente `LEMBRETE_PRAZO_DIAS_ANTES` /
`LEMBRETE_AUDIENCIA_DIAS_ANTES` no painel do EasyPanel. Um prazo que já
venceu e ainda está pendente (sem nunca ter recebido lembrete) também
dispara — lembrete atrasado é melhor que nenhum.

**Diferença de destinatário entre os dois** (decisão deliberada):
- **Prazo**: só o responsável interno pelo processo. Jargão como "prazo
  pra contestar" não costuma fazer sentido pro cliente sem contexto, e
  mandar isso desacompanhado por WhatsApp arriscaria confundir ou
  preocupar à toa.
- **Audiência**: responsável interno **e** o cliente do processo
  (quando tem e-mail/WhatsApp cadastrado) — audiência é um evento que o
  cliente frequentemente precisa saber ou comparecer, diferente de um
  prazo interno.

Cada lembrete é enviado só uma vez (marca `lembrete_enviado_em` assim
que dispara, mesmo padrão de `Compromisso.notificacao_enviada_em`) —
rodar o job de novo nunca duplica um lembrete já enviado. Canais: dentro
do sistema sempre; e-mail se SMTP estiver configurado; WhatsApp se
`WHATSAPP_BRIDGE_URL` estiver configurada e a empresa já tiver
conectado o próprio número — exatamente as mesmas condições e o mesmo
comportamento de degradar graciosamente (nunca falha o lembrete inteiro
por falta de um canal) já usados nos lembretes de compromisso.

**Testado:** 5 cenários de prazo (dentro do prazo configurado, fora
ainda, já cumprido — não dispara mesmo dentro da janela, já vencido mas
ainda pendente — dispara mesmo assim, e soft-deletado — nunca dispara) e
3 de audiência (dentro, fora, cancelada), confirmando notificação in-app
criada certinho pro responsável, e rodando o script duas vezes seguidas
pra confirmar que não duplica lembrete já enviado.

⚠️ **Este lote adiciona colunas novas** (`Prazo.lembrete_enviado_em`,
`Audiencia.lembrete_enviado_em` — ambas opcionais). Depois do deploy:
`git push` de sempre + rodar `python sincronizar_schema.py` no Terminal
do container. **Diferente dos lotes anteriores, este também precisa que
o container seja reconstruído do zero** (não só reiniciado) pra pegar o
novo arquivo de cron (`docker/lembretes-prazos-audiencias.cron`) — um
redeploy normal do EasyPanel (que reconstrói a imagem a partir do
Dockerfile) já cobre isso, só citando pra você confirmar que não foi só
um restart.

**Arquivos alterados:** `app/models/processo.py` (`Prazo.lembrete_enviado_em`,
`Audiencia.lembrete_enviado_em`), `config.py` (`LEMBRETE_PRAZO_DIAS_ANTES`,
`LEMBRETE_AUDIENCIA_DIAS_ANTES`), `enviar_lembretes_prazos_audiencias.py`
(novo), `docker/lembretes-prazos-audiencias.cron` (novo), `Dockerfile`
(registra o novo cron).

## -43. Ferramentas de LGPD (exportação, anonimização, consentimento)

**Contexto:** próximo item da tabela (Compliance, Alto impacto) depois da
verificação de conflito de interesses (seção -42).

⚠️ **Aviso de escopo, importante para entender o que isto cobre e o que
não cobre:** "ferramentas de LGPD" aqui significa ajuda operacional pra
atender uma solicitação de titular de dados — não é uma garantia
automática de conformidade legal (isso depende de política, processo,
contrato e avaliação jurídica, que estão fora do que um software
consegue garantir sozinho). Especificamente, a exportação e a
anonimização cobrem os campos **estruturados** de dado pessoal do
cliente (nome, CPF/CNPJ, contatos, endereço) — texto livre em outros
lugares do sistema (ex: se o nome de alguém foi mencionado dentro da
descrição de um processo ou de uma movimentação) **não** é varrido nem
reescrito automaticamente; fazer isso sem revisão humana arriscaria
corromper o histórico do caso.

**1) Consentimento / base legal do tratamento** — novo bloco no cadastro
do cliente: base legal (consentimento, execução de contrato,
cumprimento de obrigação legal, legítimo interesse, outra), data em que
o consentimento foi obtido, e observações livres (ex: "assinado no
contrato de honorários"). Isto é só **registro/documentação** — o
sistema não valida nem decide se a base legal escolhida é
juridicamente correta, isso é avaliação de quem cadastra.

**2) Exportação de dados (portabilidade, art. 18 V)** — novo botão
"Exportar dados (LGPD)" no detalhe do cliente, disponível pra qualquer
usuário com acesso normal ao cliente (não precisa ser admin — é uma
resposta a uma solicitação legítima do próprio cliente, não uma ação
sensível). Baixa um JSON estruturado com: dados cadastrais, processos
vinculados, lançamentos financeiros, apontamentos de hora dos processos
do cliente, e compromissos de agenda. Cada exportação fica registrada
no log de atividade (quem exportou e quando).

**3) Anonimização (direito ao esquecimento, art. 18 VI)** — novo botão
"Anonimizar", **só admin** (mesmo padrão de acesso de outras ações
sensíveis/irreversíveis do sistema — decisão de que não há mais base
legal pra reter o dado é tipicamente do sócio/gestor). Sempre com uma
tela de confirmação explícita antes de executar: lista exatamente o que
vai ser apagado, avisa se o cliente tem processo **ativo** vinculado
(nesse caso, o próprio processo ativo costuma ser a base legal que ainda
impede a anonimização — o sistema avisa mas não bloqueia, a decisão
final é humana), e exige marcar uma caixa de ciência antes do botão
funcionar. Ao confirmar: nome vira "Cliente anonimizado #N", CPF/CNPJ,
RG, e-mail, telefone, WhatsApp, endereço e observações são apagados.
Processos, lançamentos financeiros e apontamentos de hora vinculados
são **mantidos intactos** (obrigação legal/fiscal de guarda de
registro) — só a identificação pessoal do cliente é removida. Ação
irreversível, registrada no log de atividade, e o cliente ganha um
banner permanente no detalhe avisando que já foi anonimizado (e por
quem/quando).

**Testado (sqlite descartável + login real via `test_client` HTTP, 8
cenários):** campos de consentimento salvos e exibidos corretamente;
exportação traz cadastro + processos + lançamentos + apontamentos do
cliente certo; usuário comum consegue exportar mas recebe 403 ao tentar
anonimizar; tela de confirmação exige o checkbox marcado (POST sem
marcar não anonimiza nada); anonimização apaga o dado pessoal mas
mantém processo/lançamento/apontamento vinculados intactos; tentar
anonimizar de novo um cliente já anonimizado é recusado com aviso
claro; banner de "já anonimizado" aparece no detalhe.

⚠️ **Este lote adiciona colunas novas** (`Cliente.base_legal_tratamento`,
`consentimento_obtido_em`, `consentimento_observacoes`, `anonimizado_em`,
`anonimizado_por_id` — todas opcionais, nenhum dado existente é
afetado). Depois do deploy: `git push` de sempre + rodar
`python sincronizar_schema.py` no Terminal do container.

**Arquivos alterados:** `app/models/cliente.py` (5 colunas novas),
`app/utils/lgpd.py` (novo — exportação e anonimização), `app/routes/clientes.py`
(campos de consentimento no cadastro/edição, rotas `exportar_dados_lgpd` e
`anonimizar`), `app/templates/clientes/form.html` (campos de LGPD),
`app/templates/clientes/detalhe.html` (banner de anonimizado, botões,
info de consentimento), `app/templates/clientes/anonimizar_confirmar.html` (novo).

## -42. Verificação de conflito de interesses

**Contexto:** próximo item da tabela (Compliance, Alto impacto) depois da
segmentação financeira por área (seção -41).

**O conflito clássico da advocacia** que este item mira: o escritório
representa alguém como CLIENTE num processo e, ao mesmo tempo, é adverso
a essa mesma pessoa/empresa em OUTRO processo (`parte_contraria`) — às
vezes sem ninguém perceber, porque os dois casos são de unidades ou
responsáveis diferentes. É uma questão de ética profissional (OAB), não
só organização — por isso a verificação enxerga o escritório **inteiro**
(todas as unidades da mesma empresa), nunca só a unidade de quem está
olhando, mas **nunca cruza a fronteira de empresa** (cada escritório
cliente da plataforma só vê conflito dentro do próprio escritório —
verifiquei isso especificamente com um teste dedicado).

**Onde aparece:**
1. **Aviso imediato no cadastro** — ao criar um cliente cujo nome já
   aparece como parte contrária em algum processo do escritório, ou ao
   criar/editar um processo cuja parte contrária já é cliente do
   escritório em outro caso, um aviso vermelho aparece na hora (não
   bloqueia o cadastro — a decisão de aceitar ou recusar um caso por
   conflito é sempre humana, o sistema só avisa).
2. **Banner permanente na página do cliente e do processo** — diferente
   do aviso do item 1 (que só quem cadastrou vê, uma vez), este banner é
   calculado toda vez que a página é aberta, então continua avisando
   mesmo que o conflito só tenha passado a existir depois (ex: cliente A
   já existia, e um processo com parte contrária = nome de A foi criado
   depois, por outra pessoa, em outra unidade).
3. **Tela dedicada "Verificação de conflitos"** (`/governanca/conflitos`,
   menu Governança, só admin — decisão de aceitar/recusar caso por
   conflito costuma ser de sócio/gestor) — varredura completa do
   escritório inteiro, lista todo par cliente↔processo em conflito de
   uma vez, pra revisão periódica proativa (não só reativa, na hora do
   cadastro).

**Como a comparação de nome funciona:** normalizado (sem acento, sem
diferença de maiúscula/minúscula, espaços colapsados), mas **exato**
depois de normalizado — de propósito, sem similaridade aproximada
("fuzzy"). Um match "quase igual" que passa por cima de nomes
realmente diferentes geraria aviso demais e a equipe pararia de prestar
atenção nos avisos reais; prefere não pegar um nome digitado muito
diferente a virar ruído.

**Testado (sqlite descartável + login real via `test_client` HTTP, 6
cenários):**
- Conflito detectado corretamente atravessando unidades (mesma empresa).
- **Nunca cruza fronteira de empresa** — cliente com nome idêntico em
  outro escritório cliente da plataforma não aparece.
- Processo do PRÓPRIO cliente com parte contrária = nome dele mesmo não
  conta como conflito (não é o caso que este item cobre).
- Cliente sem nenhuma relação não gera falso positivo.
- Usuário comum recebe 403 na tela dedicada (só admin).
- Aviso aparece já no ato de cadastrar um novo processo com parte
  contrária = cliente existente.

**Não precisa de `sincronizar_schema.py`** — nenhuma coluna nova (a
verificação é sempre calculada na hora, nunca fica salva); usa os campos
`Cliente.nome` e `Processo.parte_contraria` que já existiam. Só o `git
push` de sempre.

**Arquivos alterados:** `app/utils/conflito_interesse.py` (novo),
`app/routes/clientes.py` (aviso no cadastro + banner no detalhe),
`app/routes/processos.py` (aviso no cadastro + banner no detalhe),
`app/routes/governanca.py` (nova rota `verificacao_conflitos`),
`app/templates/clientes/detalhe.html`, `app/templates/processos/detalhe.html`
(banners), `app/templates/governanca/conflitos.html` (novo), `app/templates/base.html`
(item de menu novo, "GB").

## -41. Segmentação financeira por área do direito (+ correção de um vazamento de conta_terceiros que eu mesmo introduzi)

**Contexto:** próximo item da tabela depois de "Modelos de cobrança"
(seção -40) — o painel de Relatórios consolidados (`/admin/relatorios`,
só admin) já mostrava contagem de processos por área do direito, mas
nenhum número financeiro por área.

**O que mudou:** a tabela "Processos por área do direito" virou
"Processos e financeiro por área do direito", com duas colunas novas —
receita recebida e receita pendente — somando só lançamentos
**vinculados a um processo daquela área** (lançamento avulso, sem
processo, não entra em nenhuma área — não tem como saber de qual área
é). Mesma régua do resto do sistema: só receita (não despesa), e conta
de terceiros excluída do total (senão um depósito judicial grande
inflaria artificialmente a "receita" de uma área).

**Efeito colateral bom encontrado no caminho:** ao extrair o filtro de
`conta_terceiros` pra um lugar só (`app/utils/financeiro_util.py`,
reaproveitado pela tela Financeiro E por este relatório), percebi que a
tabela "Desempenho por unidade" do mesmo painel de Relatórios **não**
excluía conta de terceiros do cálculo de receita — ou seja, desde a
criação da conta de terceiros (seção -39, mais cedo nesta mesma sessão),
esse relatório por unidade estava com o mesmo problema que motivou
segregar a conta de terceiros em primeiro lugar. Corrigido junto (agora
usa o mesmo filtro, testado com um depósito judicial de propósito
grande no teste pra confirmar que não aparece somado).

**Testado:** processo de uma área com lançamento pago + pendente +
depósito de terceiros + despesa, outro processo de área diferente com
só um lançamento pago, e um lançamento avulso sem processo nenhum —
confirmado que cada área mostra só a receita própria certa (terceiros e
despesa excluídos, avulso não aparece em nenhuma área). Renderização
real da tabela conferida (números batendo exatamente com o esperado).

**Não precisa de `sincronizar_schema.py`** — nenhuma coluna nova, só
lógica de código (um arquivo utilitário novo + duas rotas). Só o `git
push` de sempre.

**Arquivos alterados:** `app/utils/financeiro_util.py` (novo — filtro de
conta_terceiros extraído pra ser compartilhado), `app/routes/financeiro.py`
(passou a importar o filtro em vez de ter a própria cópia — comportamento
idêntico, só reorganização), `app/routes/admin.py` (`relatorios` — corrige
"Desempenho por unidade" e adiciona segmentação financeira por área),
`app/templates/admin/relatorios.html` (tabela por área com as colunas novas).

## -40. Modelos de cobrança (êxito %, retainer, PDF de recibo)

**Contexto:** próximo item da tabela de prioridades, depois de fechar CSRF
(reforçado), Volume Docker (confirmado por você direto no painel do
EasyPanel) e timesheet→faturamento/conta de terceiros (seção -39).

Até aqui todo lançamento financeiro era só "valor fixo digitado na mão" —
sem nenhum jeito de registrar que um honorário é sobre êxito (percentual
de um valor de causa/acordo) ou uma mensalidade recorrente, e sem
nenhuma forma de emitir um recibo formal pro cliente.

**1) Modelo de cobrança no lançamento:** novo campo `modelo_cobranca` em
todo lançamento — "Valor fixo" (padrão, comportamento de sempre), "Êxito
(%)" ou "Retainer (mensalidade)". Isso é só **rastreabilidade e apoio
visual** — o campo `valor` continua sendo sempre o que de fato
entra/sai do caixa, nunca calculado escondido.

**2) Êxito (%):** ao escolher esse modelo no formulário "Novo
lançamento", aparecem dois campos extras — percentual e valor-base
(normalmente o valor da causa ou o valor do acordo/recuperação). A tela
sugere automaticamente o valor-base a partir do valor da causa do
processo escolhido (quando cadastrado) e calcula, ao vivo, uma sugestão
de valor final (percentual × valor-base) no campo Valor — mesmo padrão
de governança já usado em "Gerar cobrança a partir de horas" (seção
-39): é só um ponto de partida editável, o valor que realmente é salvo é
sempre o que a pessoa confirmar, nunca aplicado sozinho. Assim que
alguém edita o campo Valor manualmente, a sugestão automática para de
sobrescrever, pra não brigar com o que já foi digitado.

**3) Retainer (mensalidade):** não existe fila/agendador neste projeto
pra gerar cobrança recorrente sozinha automaticamente — e não seria
prudente criar lançamento financeiro sem revisão humana de qualquer
forma. Em vez disso, todo lançamento marcado como "retainer" ganha um
botão **"Gerar próximo mês"** na tela Financeiro, que duplica o
lançamento com o vencimento um mês à frente (tratando corretamente
virada de ano e meses com dias diferentes, ex: lançamento com
vencimento dia 31 gera o próximo com vencimento no último dia do mês
seguinte) e status voltando pra "pendente" — sempre uma ação explícita
de quem está usando o sistema, nunca algo rodando escondido.

**4) Recibo em PDF:** novo botão "Recibo (PDF)" em todo lançamento já
marcado como **pago** (só faz sentido pra algo que de fato foi recebido
— gerar recibo de algo ainda pendente seria emitir comprovante de algo
que não aconteceu). O PDF traz nome/CNPJ do escritório, endereço da
unidade, valor, cliente, descrição, processo vinculado (se houver),
data e forma de pagamento, e uma linha de assinatura. Usa a biblioteca
`reportlab` (adicionada ao `requirements.txt`) — testado inclusive com
nomes e textos com acentuação (ç, ã, õ, á, é...), que aparecem
corretamente no PDF gerado.

**Testado (sqlite descartável + login real via `test_client` HTTP):**
- Formulário mostra o valor da causa de cada processo (pro cálculo de
  sugestão do JS) e os campos de percentual/valor-base aparecem/somem
  corretamente conforme o modelo escolhido.
- Lançamento "êxito" salva percentual e valor-base certos; o valor final
  salvo é o confirmado, não recalculado no servidor.
- Trocar de volta pra "fixo" não deixa percentual/valor-base "fantasma"
  salvo por engano, mesmo que o formulário mande esses campos
  preenchidos (ex: usuário mudou de ideia sem limpar os campos).
- "Gerar próximo mês" cria a cobrança seguinte com vencimento +1 mês e
  status pendente; rejeitado (400) se tentado num lançamento que não é
  do modelo retainer.
- Recibo recusa gerar PDF pra lançamento ainda pendente (redireciona com
  aviso); gera um PDF válido (cabeçalho `%PDF`, conteúdo revisado
  visualmente, inclusive com acentuação) pra lançamento pago.

⚠️ **Este lote adiciona colunas novas** (`Lancamento.modelo_cobranca`,
`Lancamento.percentual_exito`, `Lancamento.valor_base_exito` — todas
opcionais, nenhum dado existente é afetado) **e uma dependência nova**
(`reportlab`, já no `requirements.txt`). Depois do deploy: (1) `git push`
de sempre, (2) rodar `python sincronizar_schema.py` no Terminal do
container, (3) confirmar que o build do Docker instalou o `reportlab`
(o próprio `requirements.txt` já cuida disso, só citando pra você saber
o que esperar no log do build).

**Arquivos alterados:** `requirements.txt` (`reportlab`),
`app/models/financeiro.py` (`modelo_cobranca`, `percentual_exito`,
`valor_base_exito`), `app/routes/financeiro.py` (`novo` atualizado, novas
rotas `duplicar_retainer` e `recibo`), `app/templates/financeiro/form.html`
(seletor de modelo + JS de sugestão), `app/templates/financeiro/listar.html`
(badge de modelo, botão de recibo, botão "Gerar próximo mês").

## -39. Tabela de prioridades do relatório de 20/08: mais 3 formulários sem csrf_token encontrados, Volume Docker, e "Vínculo timesheet → faturamento" + "Conta de terceiros"

**Contexto:** você mandou a tabela de 23 itens do relatório e pediu pra
começar pelo mais importante e ir avançando item por item até fechar
tudo. Depois de fechar "Login sem proteção CSRF" e "Busca rápida vaza
processos em segredo de justiça" (seção -38, mesma sessão), continuei
pela tabela.

### 1) Auditoria completa de CSRF — 3 formulários a mais estavam quebrados

Antes de seguir pra frente, fui conferir se a correção de CSRF da seção
-28 realmente cobriu 100% dos formulários POST do sistema, já que
`auth/login.html` (seção -38) tinha sido pulado justamente por não estar
disponível no ambiente de teste usado naquela rodada — sinal de que
podia ter mais algum na mesma situação.

Levantei o horário de modificação de **todos** os templates do seu
repositório real e separei os que claramente fizeram parte daquela leva
de correção (mesmo lote de horário) dos que ficaram de fora. Conferi um
por um os ~20 que ficaram de fora, procurando `<form method="post"` sem
`csrf_token` (e também chamadas `fetch`/AJAX em POST, que usariam o
padrão de header `X-CSRFToken` — não achei nenhuma fora do padrão).
Resultado: a maioria (telas só de leitura/listagem, ex:
`admin/unidades.html`, `dashboard/index.html`) não tem formulário
nenhum e está OK. Mas encontrei **3 formulários realmente quebrados**,
que iam devolver erro 400 ("Bad Request") pra qualquer usuário que
tentasse usar:

- **`financeiro/form.html`** ("Novo lançamento") — o formulário mais
  usado da tela financeira. Este é o mais grave dos três: se alguém no
  seu escritório tentou cadastrar um lançamento financeiro recentemente
  e recebeu um erro estranho, era isso.
- **`clientes/detalhe.html`** (botão "Inativar"/"Reativar" cliente).
- **`governanca/importar_lote.html`** (upload de CSV pra importação em
  lote de processos).

Todos os três receberam o mesmo `<input type="hidden" name="csrf_token"
value="{{ csrf_token() }}">` que todo outro formulário do sistema já
tem. Testado com login real via `test_client` HTTP: cada um dos três
POSTs sem token é rejeitado (400) e com o token certo funciona
normalmente — confirmando tanto que o problema era real quanto que a
correção resolve sem quebrar o fluxo.

**Isso fecha de verdade o item "Login sem proteção CSRF" da tabela — o
escopo real era maior do que só o login.**

### 2) Volume Docker/persistência de documentos

`Dockerfile` ganhou `VOLUME ["/app/uploads"]` depois da criação da pasta
de uploads. Isso **documenta a intenção** (a pasta onde ficam os
documentos anexados a processos precisa sobreviver a um redeploy) e
ajuda o EasyPanel a sugerir esse caminho ao configurar um mount — mas
**não substitui, sozinho, configurar o volume persistente de verdade no
painel do EasyPanel**. Isso é uma ação sua, fora deste repositório:

✅ **Confirmado por você em 20/08/2026:** o painel do EasyPanel já tem uma
montagem de volume configurada (`uploads` → `/app/uploads`), então os
documentos anexados a processos sobrevivem a um redeploy/reinício do
container. Este item está **fechado** — não precisa de mais nenhuma ação.

### 3) Vínculo timesheet → faturamento

Até aqui, apontamento de hora (timesheet) e lançamento financeiro
existiam lado a lado sem nenhuma ligação — transformar horas trabalhadas
em cobrança era manual, por fora do sistema, sem nenhum controle contra
cobrar a mesma hora duas vezes.

- Nova coluna `Apontamento.lancamento_id` (nullable) — vazia enquanto o
  apontamento não foi cobrado.
- Nova tela **"Gerar cobrança a partir de horas"** (acessível pelo botão
  na tela Financeiro e por um link direto na linha de cada apontamento
  faturável na tela Timesheet): escolhe um processo, mostra as horas
  faturáveis ainda não cobradas desse processo, com uma **sugestão** de
  valor (horas × valor/hora padrão do cliente, só se cadastrado — nunca
  obrigatório, sempre editável antes de confirmar). Ao confirmar, cria o
  lançamento financeiro e vincula cada apontamento selecionado a ele.
- Revalidação no servidor: o POST nunca confia cegamente nos IDs que
  vieram do formulário — refiltra por processo, faturável e ainda-não-
  vinculado antes de gerar a cobrança, então mesmo que dois usuários
  tentem faturar a mesma hora ao mesmo tempo, só o primeiro consegue.
- Tela Timesheet agora mostra, em cada linha faturável, se já foi
  "faturado" ou um link direto pra gerar a cobrança.

### 4) Conta de terceiros (valores de cliente)

Escritórios de porte precisam segregar dinheiro que só **passa** pelo
escritório (depósito judicial, valor recebido em nome do cliente pra
repasse) do caixa **próprio** do escritório — misturar os dois é erro
comum e problema de compliance (OAB).

- Nova coluna `Lancamento.conta_terceiros` (booleana). Um checkbox no
  formulário "Novo lançamento" marca explicitamente quando é valor de
  terceiros — nunca é automático, sempre uma escolha do usuário no
  momento do lançamento.
- A tela Financeiro agora tem duas abas: **"Caixa do escritório"**
  (padrão, o que a tela sempre mostrou) e **"Conta de terceiros"**. Os
  totais (a receber, recebido no mês, em atraso) de cada aba são
  calculados só com os lançamentos daquele tipo — nunca somados juntos,
  pra nunca mascarar o caixa real do escritório. Mesmo na aba
  operacional, um aviso mostra o saldo em trânsito na conta de
  terceiros, como lembrete.

⚠️ **Detalhe técnico importante sobre esta coluna especificamente:**
`conta_terceiros` foi criada como **opcional** (aceita nulo) por
propósito, mesmo o valor "de fato" sendo sempre sim/não — o motivo é que
`sincronizar_schema.py` aplica coluna nova como `NOT NULL` sem nenhum
valor padrão no banco, e isso **quebraria** ao tentar aplicar numa
tabela `lancamentos_financeiros` que já tem linhas (que é exatamente o
seu caso em produção). Deixando opcional, o `sincronizar_schema.py`
funciona normalmente, e todo lançamento antigo (que fica com valor nulo
nessa coluna depois do `ALTER TABLE`) continua aparecendo
corretamente na aba "Caixa do escritório" — testado especificamente
esse cenário (lançamento simulando dado pré-existente, com o campo
nulo, continua aparecendo no lugar certo).

### Testado (sqlite descartável + login real via `test_client` HTTP, cobrindo os 4 itens acima)

- Os 3 formulários com csrf_token novo: GET traz o token, POST sem token
  é rejeitado (400), POST com token funciona.
- "Novo lançamento" com e sem marcar "conta de terceiros": salva a coluna
  certa nos dois casos; as duas abas da tela Financeiro mostram só o que
  é de cada tipo, sem misturar.
- Lançamento com `conta_terceiros` nulo (simulando dado antigo pós-
  migração) continua aparecendo na aba operacional — não some.
- Cadastro de cliente com valor/hora padrão (aceitando tanto "180.50"
  quanto "180,50").
- Gerar cobrança a partir de horas: sugestão de valor calculada certa
  (soma das horas × valor/hora do cliente); cria o lançamento e vincula
  os apontamentos certos; apontamentos já faturados somem da lista de
  elegíveis na próxima vez que a tela é aberta pro mesmo processo (não
  cobra duas vezes); processo de cliente sem valor/hora cadastrado não
  quebra a tela, só pede o valor manualmente.
- Tela Timesheet mostra o status de faturamento por linha.

⚠️ **Este lote adiciona colunas novas** (`Cliente.valor_hora_padrao`,
`Apontamento.lancamento_id`, `Lancamento.conta_terceiros` — todas
opcionais/nullable, nenhuma quebra dado existente) — depois do deploy,
**rode `python sincronizar_schema.py` no Terminal do container no
EasyPanel** pra criar essas colunas no banco. Sem isso, as telas que
usam esses campos vão dar erro.

**Arquivos alterados:** `Dockerfile` (VOLUME), `app/models/cliente.py`
(`valor_hora_padrao`), `app/models/apontamento.py` (`lancamento_id`),
`app/models/financeiro.py` (`conta_terceiros`), `app/routes/financeiro.py`
(`novo`, `listar`, nova rota `gerar_cobranca_horas`), `app/routes/clientes.py`
(`valor_hora_padrao` no cadastro/edição), `app/templates/financeiro/listar.html`,
`app/templates/financeiro/form.html`, `app/templates/financeiro/gerar_cobranca_horas.html`
(novo), `app/templates/clientes/form.html`, `app/templates/clientes/detalhe.html`,
`app/templates/timesheet/listar.html`, `app/templates/governanca/importar_lote.html`.

**Itens da tabela fechados nesta rodada:** "Volume Docker/persistência de
documentos", "Vínculo timesheet → faturamento", "Conta de terceiros
(valores de cliente)" — e um reforço do item "Login sem proteção CSRF"
(escopo ampliado pros 3 formulários extras encontrados).

## -38. Fechadas as duas pendências de segurança do relatório de 20/08/2026 (CSRF no login + vazamento na busca rápida)

**Contexto:** o relatório "Avaliação para adoção por escritório de grande
porte" (entregue como .docx nesta mesma data) reavaliou os três itens
críticos da auditoria de 19/08 e encontrou duas pendências pequenas e
concretas, ambas fechadas agora.

**1) CSRF no login:** `app/templates/auth/login.html` — que não estava
disponível no ambiente de testes quando a proteção CSRF foi ativada em
todo o resto do sistema (seção -28) — foi lido diretamente do seu
repositório real (via o bridge com seu computador) e recebeu o mesmo
`<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` que
todo outro formulário do sistema já tem. Removido o `@csrf.exempt` e o
comentário temporário de `app/routes/auth.py::login` — o login agora tem
a mesma proteção que o resto do sistema, sem exceção.

**2) Busca rápida vazando processo em segredo de justiça:**
`app/routes/api.py::busca_rapida` (autocomplete usado por telas de busca)
filtrava só por unidade (`aplicar_escopo_unidade`), sem aplicar
`filtrar_processos_visiveis` (a mesma função que já protege
`processos.listar` e `governanca.painel`/`fila_intimacoes` desde a
correção da muralha ética, seção -28). Um usuário sem grant explícito
conseguia ver número e existência de um processo sigiloso por essa busca,
mesmo com a tela de detalhe corretamente bloqueada. Agora usa a mesma
função de filtro que o resto do sistema.

**Testado (4 cenários, login real via `test_client` HTTP, não só chamada
direta da view):**
- GET /login traz o csrf_token no HTML; POST sem csrf_token agora é
  rejeitado (400); POST com o token certo continua logando normalmente.
- Busca rápida: usuário sem grant não vê o processo sigiloso (mas continua
  vendo processos normais); o responsável pelo processo continua vendo
  normalmente; um usuário com `ProcessoAcessoRestrito` explícito também
  continua vendo — a correção não superbloqueia quem tem acesso legítimo.

**Não precisa de `sincronizar_schema.py`** — nenhuma coluna nova, só
lógica de código. Só o `git push` de sempre.

**Arquivos alterados:** `app/templates/auth/login.html` (csrf_token),
`app/routes/auth.py` (remoção do `@csrf.exempt`), `app/routes/api.py`
(`busca_rapida` agora usa `filtrar_processos_visiveis`).

## -37. Aba Prazos separada em "em aberto" / "concluídos" + captura do campo extra do DataJud (complementos)

**O que foi esclarecido:** "não aparece mais nada" (seção -36) não era a
tela vazia — era que a aba Prazos só mostrava os 105 pendentes, misturados
numa tabela só, e o(s) prazo(s) já concluído(s)/com evidência (o que você
finalizou manualmente) ficava perdido no meio, difícil de achar. Você
pediu pra ver claramente os já concluídos com toda a evidência e data.

**1) Aba Prazos agora tem duas tabelas separadas** em vez de uma só
misturando tudo:
- **"Prazos em aberto"**: status pendente, em elaboração ou protocolado
  aguardando evidência — os que realmente precisam de ação.
- **"Prazos concluídos e finalizados"**: cumprido, perdido e histórico
  anterior — cada um com todo o detalhe já disponível (data de
  cumprimento + evidência completa da movimentação/documento pros
  cumpridos; motivo/quem/quando pros regularizados; nota explícita pros
  perdidos, que antes ficavam sem nenhuma explicação na linha).

Tecnicamente: extraído o HTML de uma linha de prazo pra uma macro Jinja
reaproveitada nas duas tabelas (`app/templates/processos/detalhe.html`),
pra não duplicar a lógica dos botões/formulário de evidência.

**2) Captura do campo `complementosTabelados` do DataJud** (você pediu
"sim" na pergunta sobre buscar mais dado por movimentação): alguns tipos
de ato vêm com um detalhe estruturado extra que o DataJud manda separado
do texto principal — ex: resultado de um julgamento (procedente/
improcedente), tipo de audiência, meio de intimação. Isso era descartado
na captura; agora é formatado como texto curto e guardado em
`Movimentacao.complemento` (nova coluna, nullable), aparecendo junto do
texto do ato nas abas Andamentos, Governança → Painel (movimentações
críticas) e nas evidências da aba Prazos. A maioria das movimentações
continua sem nada aqui (None) — isso é o normal, a maior parte dos atos
não tem complemento.

⚠️ Mesmo aviso de sempre sobre este conector: os nomes exatos dos campos
desse complemento seguem o schema publicado do DataJud, mas não pôde ser
testado contra uma chamada real (rede de saída restrita neste ambiente).
Depois do deploy, se um tipo de ato que claramente tem complemento (ex:
julgamento) vier sem nada, me avise com um exemplo do JSON de resposta
pra eu ajustar o mapeamento — mesmo texto de aviso que já existe no topo
de `app/utils/conector_datajud.py`.

**Arquivos alterados:** `app/models/movimentacao.py` (coluna
`complemento`), `app/utils/captura_conectores.py` (campo no dataclass),
`app/utils/conector_datajud.py` (`_formatar_complementos` + uso na
captura), `app/utils/captura_pipeline.py` (persiste o campo),
`app/templates/processos/detalhe.html` (split de tabelas + exibição do
complemento), `app/templates/governanca/painel.html` (complemento nas
movimentações críticas).

**Testado:** `_formatar_complementos` com vários formatos plausíveis de
entrada (com/sem descrição, só código, lista vazia); persistência
ponta a ponta pelo pipeline (com e sem complemento); renderização real da
aba Prazos confirmando a separação em duas seções (contagem certa em cada
uma, ordem correta, nota de "perdido" aparecendo, complemento aparecendo
na evidência de um prazo cumprido).

**⚠️ Esta entrega PRECISA de `sincronizar_schema.py`** — `complemento` é
coluna nova na tabela `movimentacoes`. Rode o comando no terminal do
container do EasyPanel depois do `git push`/deploy, do jeito de sempre.

## -36. Evidência completa visível na aba Prazos + fila de triagem (Governança) agora lista o que falta mapear, não só a quantidade

**O que foi pedido:** "quero que em prazos apareçam todas as evidências já
existentes e todos os seus dados e datas" + "em governança aparecem
bastante coisa para mapear manualmente, quero que busque também o que já
foi mapeado dos processos e os dados que foram inseridos nesse
mapeamento" — resumindo: buscar/mostrar o máximo de dado já disponível no
sistema, em vez de só números/status sem detalhe.

**1) Evidência completa na aba Prazos:** um prazo "cumprido" só mostrava
o status, sem nenhum detalhe da evidência (nem data, nem qual
movimentação/documento) — os dados já existiam no banco
(`evidencia_movimentacao_id`/`evidencia_documento_id`/`cumprido_em`), só
não tinham `relationship` no modelo nem apareciam no template. Adicionado:
- `Prazo.evidencia_movimentacao` e `Prazo.evidencia_documento`
  (`app/models/processo.py`) — relationships sobre as colunas que já
  existem, não precisa de `sincronizar_schema.py`.
- Aba Prazos agora mostra, pra todo prazo "cumprido": a data de
  cumprimento e, ou a movimentação de evidência (data + texto do ato), ou
  o documento comprobatório (nome + data de envio) — mesmo padrão visual
  que já existia pro status "histórico anterior" (motivo/quem/quando).

**2) Fila de triagem agora é uma lista, não só um número
(Governança → Mapa de estado):** antes, a tela só mostrava "N movimentação(ões)
aguardando triagem" sem dizer com o quê. Nova função
`fila_triagem_agrupada` (`app/routes/governanca.py`) agrupa as
movimentações sem mapeamento por código TPU (ou por texto, quando não tem
código) e mostra: quantidade, um exemplo real do texto do ato, em quais
processos aparece (com link direto pra aba Prazos/Andamentos daquele
processo), e um botão "Mapear agora" que abre o formulário de novo
mapeamento já com código/texto pré-preenchidos — antes só existia esse
atalho a partir de dentro de um processo (prazo genérico "Análise
necessária"), agora também existe centralizado, olhando a carteira
inteira de uma vez. A lista de mapeamentos JÁ CADASTRADOS (código,
descrição, texto, estado de negócio resultante) continua na mesma tela,
sem mudança — ela já mostrava tudo que foi mapeado antes.

**Testado:** renderização real da aba Prazos com um prazo "cumprido" com
evidência de movimentação (confirma data + texto aparecem), e renderização
real da tela de Mapa de estado com movimentações de triagem pendente em 2
processos diferentes (confirma agrupamento, processos afetados e o link
"Mapear agora" pré-preenchido funcionando).

**Não precisa de `sincronizar_schema.py`** — nenhuma coluna nova, só
relationships e agrupamento em cima do que já existe no banco.

**Pendente de resposta sua (ver conversa) — "em prazos não aparece mais
nada":** preciso confirmar se isso é o resultado ESPERADO de já ter usado
a regularização em lote (ou de os prazos verdadeiramente pendentes já
terem sido todos fechados) nesse processo específico, ou se é um erro de
verdade (nesse caso, provavelmente ligado a `sincronizar_schema.py` ainda
não ter sido rodado — a mesma causa do erro `Unknown column` anterior,
seção -33/-34). E se você quer que eu vá além do que já existe no DataJud
hoje (capturar também o campo `complementosTabelados` — detalhe extra que
alguns tipos de movimentação trazem, ex: resultado de julgamento, tipo de
audiência — atualmente descartado) ou se "máximo de dados possível" já
fica satisfeito com o que foi entregue aqui.

## -35. Sugestão automática de evidência para prazos históricos (assistida — sempre com confirmação humana)

**O que foi pedido:** depois da seção -33 (regularização em lote pro
status neutro "histórico anterior", sem evidência) e do diagnóstico do bug
da seção -34, você perguntou se dava pra ir além: os prazos históricos já
virem com evidência de cumprimento de verdade, fechando como "cumprido"
igual a qualquer prazo normal, em vez de só sumir da contagem como
"histórico anterior".

**Por que não é uma automação total (sem clique nenhum):** o próprio
projeto já define isso como governança central — "o prazo só fecha como
cumprido quando há evidência real, marcar 'feito' no botão sozinho não
fecha o prazo" (ver docstring de `Prazo`, seção 7.2 do briefing). Pra um
prazo genérico ("Análise necessária — ato sem regra cadastrada") isso é
ainda mais delicado: como o próprio sistema não identificou qual ação era
exigida por aquele ato, não existe "isto prova que a obrigação foi
cumprida" nenhum pra procurar — não é uma limitação técnica que dê pra
contornar, é a natureza do prazo genérico. Fechar isso sozinho, sem
revisão humana, criaria exatamente o risco que a governança do projeto foi
desenhada pra evitar: um prazo marcado como cumprido sem ninguém nunca ter
conferido se aconteceu de verdade — problema sério numa ferramenta de
controle de prazo jurídico.

**O que foi construído (versão assistida):** `sugerir_evidencia_historica`
(nova função em `app/routes/processos.py`) — só atua em prazos históricos
que TÊM uma `RegraProximaAcao` cadastrada (ex: "Citação para contestar" →
ação exigida "Apresentar contestação"), porque só nesses há uma obrigação
definida pra verificar. Procura, entre as movimentações do mesmo processo
nos 180 dias seguintes ao início do prazo, a primeira que contém uma
palavra-chave de resposta/protocolo ("contestação", "manifestação",
"recurso", "embargos" etc.). Se a movimentação mais próxima no tempo for,
em vez disso, uma certidão de decurso de prazo/silêncio da parte, a busca
para ali e NÃO sugere nada — esse é o próprio registro de que a parte não
respondeu naquele período.

Quando acha uma correspondência plausível, a aba Prazos do processo mostra
um selo "sugestão encontrada" no botão "Fechar com evidência" desse prazo,
o formulário já abre expandido com a movimentação pré-selecionada e um
aviso explicando que é uma heurística por palavra-chave, não uma prova —
revise o teor antes de confirmar. O fechamento em si continua sendo a
mesma rota de sempre (`cumprir_prazo_com_evidencia`), exigindo o clique em
"Confirmar cumprimento" — nada fecha sozinho, só ficou mais rápido revisar
e confirmar do que caçar a movimentação certa manualmente em décadas de
andamento.

**Testado:** três cenários (prazo com regra + resposta plausível depois →
sugere certo; prazo com regra + só certidão de decurso de prazo depois →
não sugere nada; prazo genérico sem regra → nunca sugere, mesmo com uma
movimentação boa depois) e um teste end-to-end renderizando a aba Prazos
de verdade (via `test_request_context` + login simulado) confirmando que o
selo aparece e a movimentação certa vem pré-selecionada no formulário.

**Não precisa rodar `sincronizar_schema.py` para esta entrega** — não foi
criada nenhuma coluna nova no banco, a sugestão é calculada na hora, sem
persistir nada. Só precisa do `git push` de sempre pra ir pro ar.

**Arquivos alterados:** `app/routes/processos.py` (função
`sugerir_evidencia_historica` + wiring na rota `detalhe`),
`app/templates/processos/detalhe.html` (selo, aviso e pré-seleção no
formulário "Fechar com evidência").

## -34. Bug corrigido: captura periódica gerava prazos genéricos "fantasma" para movimentações antigas indexadas tarde pelo tribunal

**O que aconteceu:** você reportou um processo real com 105 prazos atrasados,
a maioria "Análise necessária — ato sem regra de próxima ação cadastrada"
com datas de 2002, 2003 e 2012, e perguntou se o sistema "não buscou as
informações" desse processo.

**Diagnóstico:** a captura funcionou certo — o histórico completo do
processo (2002 a hoje) está de fato no sistema, vindo do DataJud. O
problema era outro: a proteção contra "avalanche de prazos genéricos
antigos" (ver seção -3/prazos_engine.py, "ato sem regra cadastrada gera
tarefa genérica, nunca é ignorado, mas não pode virar alarme falso pra ato
de 20 anos atrás") só cobria a carga INICIAL do processo (`captura_inicial=
True`, cadastro por CNJ). Ela não cobria a captura periódica diária (o
cron `capturar_movimentacoes.py`, que roda com `captura_inicial=False`).

Tribunais mais antigos (ou processos migrados de físico pra eletrônico)
frequentemente indexam o histórico completo no DataJud aos poucos, não
tudo de uma vez — então uma movimentação de 2003 pode aparecer como "nova"
pro sistema só meses depois do cadastro, capturada por um desses ciclos
diários do cron. Como essa captura não é a inicial, o filtro antigo deixava
passar, e cada uma dessas movimentações "descobertas tarde" sem regra
cadastrada virava um prazo genérico com vencimento já vencido há anos —
foi exatamente isso que inundou a aba Prazos desse processo.

**Correção aplicada (`app/utils/captura_pipeline.py`):** o critério pra
permitir o prazo genérico "Análise necessária" deixou de depender de
`captura_inicial` (se é a carga inicial ou não) e passou a depender só da
DATA REAL do ato: só gera esse prazo genérico quando o ato aconteceu nos
últimos 60 dias (constante `JANELA_DIAS_MOVIMENTACAO_RECENTE`), contados
de hoje — folga generosa acima do maior prazo processual comum (30 dias),
pra nunca engolir um alerta genuinamente recente. Vale tanto pra carga
inicial (cadastro por CNJ) quanto pra captura periódica — mesma regra nos
dois caminhos, sem duplicar lógica. Testado com um cenário simulando
exatamente o caminho do cron periódico (lote com 3 atos antigos sem regra
de 2002/2003/2012 + 1 ato recente sem regra de 5 dias atrás): só o ato
recente gerou prazo; os 3 antigos ficaram registrados e visíveis (aba
Governança, badge "triagem pendente"), sem virar alarme falso.

**Importante — o que NÃO muda:** quando HÁ uma regra cadastrada
(`RegraProximaAcao`) que bate com o ato, o prazo sempre é gerado,
independente da idade — isso é intencional e continua igual (é assim que
um prazo processual de verdade, mesmo antigo, nunca é "ignorado
silenciosamente"). Testado também.

**O que fazer com os 105 já existentes neste processo:** eles não somem
sozinhos com esta correção (ela só evita gerar NOVOS prazos-fantasma
daqui pra frente). Pra regularizar os que já existem, use a ação
"Regularizar prazos anteriores ao cadastro" no topo da aba Prazos do
processo (seção -33 abaixo) — ela pega exatamente esse padrão (prazo
pendente com vencimento antes da data em que você cadastrou o processo no
sistema) e fecha todos de uma vez com o status neutro "histórico
anterior" (não conta mais como atrasado/perdido nos painéis, mas também
não finge ter evidência de cumprimento — ver seção -33). **Só funciona
depois de rodar `python sincronizar_schema.py` no terminal do container —
se você ainda não confirmou ter rodado esse comando após o último deploy,
rode agora antes de tentar usar o botão.**

**Sobre pedir evidência de cumprimento automática (sua pergunta mais
recente):** ver resposta detalhada na conversa — em resumo, não é seguro
fechar como "cumprido" automaticamente pra esses prazos antigos porque o
sistema não tem como saber com confiança qual movimentação posterior
efetivamente satisfez cada obrigação específica (mais ainda pros de
"Análise necessária", que por definição não têm uma ação exigida
identificada). Ficou combinado avaliar uma versão assistida (sistema
sugere, humano confirma) como próximo passo, se você quiser.

## -33. Regularização em lote de prazos "perdidos" vindos do histórico anterior ao cadastro no sistema

**O que foi pedido:** você registrou um processo público pelo número CNJ e
percebeu muitos "prazos perdidos" — perguntou se dava pra também trazer o
que já tinha ocorrido no processo, pra não ter que recadastrar tudo na mão.

**Isso já acontecia — o "problema" era outra coisa:** ao cadastrar pelo
CNJ, o sistema já busca e grava o histórico completo de movimentações do
tribunal via DataJud (ver `app/routes/governanca.py::novo_por_cnj` e
`app/utils/captura_pipeline.py`) — nada precisa ser digitado manualmente,
e isso já funcionava antes desta mudança. Os "prazos perdidos" eram um
efeito colateral desse próprio histórico: quando uma movimentação antiga
bate com uma regra cadastrada (`RegraProximaAcao`), o motor de prazos gera
um `Prazo` com vencimento na data real daquela movimentação — que, sendo
histórica, já nasce vencida. Como nada marca esse prazo como cumprido
automaticamente (não existia evidência real capturada na época — o
escritório não usava o sistema ainda), ele fica "pendente" com data
passada, e é isso que os painéis contam como "perdido" — mesmo que, na
vida real, o processo tenha seguido tramitando normalmente.

**O que foi construído:** uma ação em lote na aba "Prazos" de cada
processo (`app/routes/processos.py::regularizar_prazos_historico`) — um
aviso aparece automaticamente quando há prazos pendentes com vencimento
anterior à data em que aquele processo foi cadastrado no sistema, com um
botão "Regularizar N prazo(s)" que pede um motivo obrigatório (fica
registrado, com quem aplicou e quando) e marca todos de uma vez com um
status novo, neutro: **`historico_anterior`**.

**Por que não usei "cumprido":** o projeto tem uma regra de governança
central e deliberada (ver docstring de `Prazo` em `app/models/processo.py`)
— um prazo só fecha como "cumprido" com evidência real anexada (movimentação
capturada ou documento). Prazos históricos regularizados em lote não têm
essa evidência (o escritório não estava usando o sistema na época), então
marcar como "cumprido" seria construir uma mentira de compliance. O status
novo é honesto sobre isso: não finge evidência, só reconhece que aquele
prazo é anterior ao uso do sistema e não deve mais contar como pendência
em aberto. Ele nunca desaparece nem é apagado (governança: nunca exclusão
física) — continua visível na aba Prazos, com o motivo/quem/quando.

**Onde esse novo status foi excluído de propósito** (pra não continuar
aparecendo como "vencido"/"pendente"/"perdido" em nenhum lugar):
painel de governança (3 listas — próximos 7 dias, 8-15 dias, vencidos sem
evidência), fila de intimações, calendário da Agenda, contexto que a IA
usa pra montar resumo/rascunho de petição. **Onde deixei de propósito
sem mexer**: o cálculo de "taxa de cumprimento de prazo" (`cumpridos` /
`cumpridos + perdidos`) já ignorava qualquer status que não fosse
"cumprido"/"perdido" — um prazo regularizado não conta nem a favor nem
contra essa taxa, o que é o comportamento certo (não foi nem confirmado
cumprido, nem perdido de verdade).

**Colunas novas (banco):** `prazos.motivo_regularizacao`,
`prazos.regularizado_em`, `prazos.regularizado_por_id` — todas NULLABLE
(mesmo motivo de sempre — `sincronizar_schema.py` não aplica `DEFAULT`).

**Passo extra pra ativar:** depois do `git push` de sempre, rode
`python sincronizar_schema.py` no terminal do container (mesmo passo já
usado nas seções -32 e -28) pra criar as 3 colunas novas.

**Testado nesta rodada:** simulei um processo com 4 prazos — dois
"históricos" (vencimento de 2015 e 2016, antes do cadastro), um real (daqui
a 5 dias) e um já legitimamente cumprido antes. Confirmei: o aviso mostra a
contagem certa (2, não os 4); tentar regularizar sem motivo é bloqueado e
nada muda; regularizar com motivo marca só os 2 históricos, sem tocar no
prazo real nem no já cumprido; o aviso some da tela depois (não sobra mais
nenhum elegível); o painel de governança e a fila de intimações não listam
mais os prazos regularizados; e o endpoint genérico de trocar status
recusa `"historico_anterior"` (só passa pela rota dedicada, que exige
motivo). Reexecutei os testes dos 3 itens críticos da seção -28 — todos
continuam passando.

## -32. Fila de processamento em segundo plano (Redis + RQ) — item 2.3 do `AUDITORIA_GRANDE_PORTE.md`

**O que foi pedido:** você pediu pra eu apontar o que mais faltava da
auditoria de grande porte e escolher por onde começar. Escolhi este item
(2.3 — fila de processamento em segundo plano) porque ele resolve, de
raiz, o tradeoff que a seção -31 (acima) tinha acabado de introduzir: com
`-w 1`, o sistema inteiro ficava bloqueado pra todo mundo enquanto
qualquer pessoa gerava uma resposta de IA. Isso deixa de ser um problema
com esta mudança.

**O que mudou, tecnicamente:** antes, tanto o chat do Agente de IA
(`app/routes/agente_ia.py`) quanto a Análise de processo
(`app/routes/processos.py::gerar_analise_ia`) chamavam o modelo de IA
DIRETO dentro do próprio pedido do navegador — o worker do gunicorn ficava
ocupado (minutos, no modelo local por CPU) até o modelo terminar de
responder. Agora:

- A rota web só valida o pedido, cria o registro (mensagem/análise) já
  como **"processando"** no banco, e devolve a tela na hora (sem esperar
  o modelo).
- Quem chama o modelo de verdade é um **processo separado** — um worker do
  RQ (`app/jobs/ia_jobs.py`), consumindo uma fila no Redis
  (`app/utils/fila.py`), iniciado em segundo plano pelo
  `docker/entrypoint.sh` (mesmo padrão que já existia pro cron).
- A tela do chat (`conversa.html`) e a aba "Análise IA" do processo
  (`detalhe.html`) checam sozinhas, a cada poucos segundos, se o resultado
  já ficou pronto (JavaScript simples, sem WebSocket) e atualizam a tela
  automaticamente — sem precisar recarregar manualmente.
- **`-w 1` virou `-w 2`** no `Dockerfile` (gunicorn): o motivo original do
  `-w 1` (worker do gunicorn preso gerando IA) deixou de existir, já que os
  workers do gunicorn nunca mais chamam o modelo diretamente.
- Redis roda **dentro do mesmo container** da aplicação (não escuta fora
  de `127.0.0.1`, não precisa configurar nada novo no EasyPanel) —
  limitação consciente: se o container reiniciar com algum job na fila ou
  em processamento, esse job específico se perde (fica "processando" pra
  sempre, sem re-tentar sozinho); gerar de novo é só clicar no botão outra
  vez. Se um dia isso incomodar, a evolução natural é um Redis em serviço
  separado, com persistência própria.
- O worker do RQ roda com `--worker-class rq.worker.SimpleWorker` — de
  propósito, pra continuar carregando o modelo de IA **uma vez só na
  memória** (não a cada mensagem) — ver comentário detalhado no
  `docker/entrypoint.sh` sobre por que isso importa.

**Colunas novas (banco):** `mensagens_agente_ia.status` e
`analises_processo_ia.status` — ambas NULLABLE, `None` tratado como
"pronta" em todo o código (mesmo motivo de sempre: `sincronizar_schema.py`
só sabe adicionar coluna sem `DEFAULT` no banco).

**⚠️ Passo extra pra ativar, além do de sempre:**
1. `git add` / `commit` / `push` (de sempre).
2. Depois do deploy, rode `python sincronizar_schema.py` no terminal do
   container (pelo próprio painel do EasyPanel — não precisa de SSH) pra
   criar as duas colunas novas acima. Ele sempre pergunta antes de aplicar
   e nunca apaga dado.

**Testado nesta rodada:** simulei o fluxo inteiro de ponta a ponta —
processo web (Flask test client) enfileirando o job e um worker RQ de
verdade (processo separado, apontando pro mesmo banco/Redis) consumindo a
fila — tanto pro chat quanto pra análise de processo. Confirmei: o POST
que envia a mensagem/pedido responde em milissegundos (não trava esperando
o modelo); a tela mostra "processando" imediatamente; o job processa em
outro processo e grava o resultado; a tela para de mostrar "processando"
depois que o job termina; rodei duas mensagens seguidas no mesmo worker
(sem reiniciar) pra confirmar que o modelo ficaria mesmo
carregado uma vez só, sem recriar a conexão com o banco a cada mensagem
(o que vazaria conexão com um worker de vida longa). Reexecutei também os
testes dos 3 itens críticos da seção -28 — todos continuam passando. Não
testei com o modelo de IA de verdade carregado (sem os pesos baixados
aqui no meu ambiente), só com o caminho de "provedor indisponível" — vale
confirmar o tempo real de resposta com o modelo de verdade depois do
deploy.

## -31. IA local REATIVADA — a desativação da seção -30 foi revertida; em vez disso, limitei a IA a 1 worker pra travar o consumo em ~1,1 GB

**O que aconteceu:** logo depois da mudança da seção -30 (motor da IA
local desativado), a tela do Agente de IA passou a mostrar "Agente de IA
indisponível para esta empresa no momento" — que é exatamente o
resultado esperado daquela mudança (nenhum provedor configurado pra
aquela empresa), mas você interpretou como um novo erro e pediu pra
"desinstalar a IA que instalou que esgotou a memória e instalar a
antiga". Perguntei o que você preferia e você esclareceu: queria a IA
local ligada de novo, mas te preocupava o fato dela (na sua percepção)
estar consumindo ~2,5 GB, contra os ~1,1 GB "de antes".

**Esclarecimento importante — não existiam dois modelos diferentes:** o
modelo sempre foi o mesmo, o "pequeno" (Qwen2.5-1.5B, ~1,1 GB,
`baixar_modelo_ia_local.py`). O modelo "grande" (~2,5 GB) existe no
script mas nunca foi ativado — nem antes, nem depois de nenhuma mudança
minha nesta rodada. O número "~2,5 GB" que você percebeu não era um
modelo diferente: é a SOMA de até 2 workers do gunicorn (`-w 2`,
configurado no `Dockerfile`) carregando, cada um, sua própria cópia do
mesmo modelo pequeno de 1,1 GB na memória — cada worker é um processo
Python separado, então o modelo não é compartilhado entre eles. Com os 2
workers em uso ao mesmo tempo, o total batia perto de ~2,2-2,5 GB, mesmo
sendo sempre o modelo de 1,1 GB.

**O que foi feito:**
- Revertida a desativação da seção -30: `llama-cpp-python` descomentado
  de novo em `requirements.txt`, `Dockerfile` voltou a instalar via
  `--extra-index-url` (wheel pré-compilada, seção -29) e a baixar o
  modelo pequeno (~1,1 GB) durante o build — exatamente como estava antes
  da seção -30, nenhuma mudança de modelo.
- `Dockerfile`: `gunicorn` mudou de `-w 2` pra **`-w 1`** — com só 1
  worker, só existe UMA cópia do modelo carregada por vez, então o teto
  de RAM da IA fica garantido em ~1,1 GB, não importa quantas mensagens
  diferentes cheguem.
- `app/utils/ia_local.py`: as mensagens de erro (que antes diziam
  "desativado de propósito") voltaram a tratar a ausência do modelo como
  uma falha genuína — já que a IA volta a ser o padrão ativo — mas
  continuam amigáveis pro usuário final, sem instruir a rodar comando
  nenhum no servidor.

**⚠️ Tradeoff real que vem junto com `-w 1` — vale você saber:** com só 1
worker, o sistema INTEIRO (não só o Agente de IA) atende uma requisição
de cada vez. Se dois usuários acessarem ao mesmo tempo, um espera o outro
terminar — pra telas normais isso é rápido e quase imperceptível. Mas
enquanto o Agente de IA está gerando uma resposta (pode levar até
alguns minutos, já que roda por CPU, sem GPU), o sistema fica bloqueado
pra TODOS os outros usuários até terminar, não só pra quem pediu a IA.
Isso é diferente do tradeoff menor que já existia com 2 workers (onde
sobrava 1 worker livre pro resto do sistema enquanto o outro gerava a
resposta). Pra um escritório de porte maior com vários usuários
simultâneos, isso pode incomodar na prática — se acontecer, me avise que
a solução definitiva é tirar a geração de IA do ciclo de
requisição/resposta (fila em segundo plano), não é algo que dá pra
resolver só ajustando esse número de novo.

**Testado nesta rodada:** reexecutei os testes dos 3 itens críticos da
seção -28 (Data Lake, CSRF, sigilo de processo) — todos continuam
passando. Não tenho como testar o carregamento de verdade do modelo
aqui no meu ambiente (sem os pesos baixados, sem GPU/RAM de servidor de
produção pra reproduzir), mas a mudança em si é só de configuração
(quantidade de workers) e não mexe em nenhuma lógica do motor de IA.

**⚠️ Lembrete de sempre:** só chega no ar depois de `git add` / `commit`
/ `push` na pasta do projeto.

## -30. IA local DESATIVADA temporariamente (motor + download do modelo) — até migrar de VPS

**⚠️ Esta seção foi REVERTIDA pela seção -31 acima, no mesmo dia — deixei
o histórico abaixo só pra registro, mas a IA local está ativa de novo.**

**O que foi pedido:** mesmo depois do ajuste da seção -29 (wheel
pré-compilada em vez de compilar do zero), o build voltou a falhar de
novo logo depois — o log parava de responder logo após "Installing
collected packages", sem erro explícito, o que é compatível com o
processo de build sendo morto por falta de RAM (o servidor de produção
tem só 2 núcleos / ~7,8 GB, e provavelmente o container antigo — já com o
modelo de IA carregado em memória — ainda estava rodando ao mesmo tempo
que o build novo). Você pediu pra voltar a IA local pra uma versão
anterior, mais leve, "pelo menos até migrar a VPS" — confirmei que era só
o motor/modelo de IA local (não as correções de segurança da seção -28,
que continuam ativas e intactas).

**O que foi feito:** o motor da IA local (`llama-cpp-python`, biblioteca
que roda o modelo dentro do próprio servidor) e o download dos pesos do
modelo (~1,1 GB, `baixar_modelo_ia_local.py`) foram **comentados**, não
apagados, em três arquivos:
- `requirements.txt` — linha `llama-cpp-python==0.3.34` comentada;
- `Dockerfile` — a etapa de `pip install` voltou a ser simples (sem
  precisar mais do `--extra-index-url` da seção -29, já que não instala
  mais o `llama-cpp-python` de jeito nenhum agora) e as duas linhas que
  baixavam o modelo (`COPY baixar_modelo_ia_local.py .` / `RUN python
  baixar_modelo_ia_local.py`) foram comentadas;
- `app/utils/ia_local.py` — as duas mensagens de erro internas (biblioteca
  não instalada / arquivo do modelo não encontrado) foram reescritas: antes
  instruíam a "rodar tal comando no servidor", o que ia parar direto no
  chat do Agente de IA pro usuário final (advogado, não tec) e parecer um
  build quebrado; agora dizem claramente que é uma desativação temporária
  e planejada, e sugerem usar a API do Claude com chave própria (\"Minhas
  Integrações\") como alternativa nesse meio tempo.

**Por que é seguro (nada quebra):** o sistema já tinha, desde antes desta
mudança, um tratamento gracioso pra "IA local indisponível" em toda
chamada — `app/utils/agente_ia_router.py` embrulha qualquer falha do
motor local numa `ProvedorIAIndisponivelError` com mensagem amigável, e
todo lugar que chama isso (`/agente-ia`, análise de processo em
`processos.py`) já capturava essa exceção e mostrava um aviso em vez de
deixar a tela quebrar. Testei de ponta a ponta nesta rodada (com o
`llama-cpp-python` desinstalado do ambiente de teste, simulando
exatamente o servidor depois deste deploy): a aplicação sobe normalmente,
a tela do Agente de IA mostra "não configurado", e uma mensagem enviada
recebe a resposta amigável em vez de erro. Reexecutei também os testes
dos 3 itens críticos da seção -28 (Data Lake, CSRF, sigilo de processo) —
todos continuam passando, confirmando que essa mudança não afeta a
segurança corrigida antes.

**Empresas que já usam a API do Claude com chave própria** (opção
"PROVEDOR_IA_CLAUDE_BYOK" em "Minhas Integrações") **não são afetadas** —
continuam funcionando normalmente, porque não dependem do motor local.

**Efeito esperado no próximo deploy:** o build fica mais leve e mais
rápido (não baixa mais os ~1,1 GB do modelo nem precisa da wheel do
`llama-cpp-python`), e o consumo de RAM em runtime cai também (o motor
local não carrega mais nada em memória, já que nem está instalado). Se a
causa do segundo travamento (seção -29) era mesmo falta de RAM durante o
build concorrendo com o container antigo, isso deve resolver — mas como
não consegui confirmar a causa exata (os comandos de diagnóstico que
sugeri via SSH ainda não foram rodados), vale acompanhar o painel de
recursos do EasyPanel no próximo deploy com atenção.

**Como reativar quando migrar pra uma VPS com mais RAM:** descomentar as
linhas indicadas nos três arquivos acima (todas com comentário explicando
o que descomentar) — é rápido, é só me pedir quando chegar a hora.

**⚠️ Lembrete importante (aprendido nesta mesma rodada):** o EasyPanel
faz o build a partir do repositório Git conectado, não a partir da pasta
sincronizada no seu computador — depois que eu entregar estes arquivos
aqui, é preciso fazer `git add`, `git commit` e `git push` na pasta do
projeto pra essa mudança realmente chegar no próximo deploy. Sem esse
passo, o EasyPanel vai continuar buildando a versão antiga (com a IA
local ainda ativa) mesmo que os arquivos já estejam atualizados na sua
pasta local.

## -29. Build travando o servidor inteiro (RAM 99%, CPU 100%) — `Dockerfile` ajustado pra não compilar mais o llama-cpp-python do zero

**O que aconteceu:** ao tentar subir a versão com as correções de segurança
(seção -28 abaixo), o build ficou preso muito tempo compilando o
`llama-cpp-python` (motor do modelo de IA local) a partir do código-fonte
— e isso derrubou o servidor inteiro (painel do EasyPanel e até o SSH
pararam de responder), porque o servidor de produção tem só 2 núcleos e
~7,8 GB de RAM, e o gráfico de recursos confirmou RAM em 99% e CPU em
100% durante o build. Precisou de um reboot forçado pelo painel da
hospedagem (fora do EasyPanel) pra voltar.

**Causa raiz:** o `llama-cpp-python` não vem com uma wheel pré-compilada
no PyPI normal pra esta combinação de SO/Python — sem uma, o `pip`
compila o `llama.cpp` (código C++) inteiro na hora do build, o que é
pesado o bastante pra estourar a RAM de um servidor deste porte.

**Correção:** `Dockerfile` alterado pra instalar as dependências
apontando também pro índice de wheels pré-compiladas que o próprio autor
do `llama-cpp-python` mantém
(`--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`).
Confirmei que existe uma wheel pronta pra exatamente a versão presa em
`requirements.txt` (0.3.34, Linux x86_64) antes de aplicar — com isso o
`pip` baixa o binário já compilado em vez de compilar do zero, o que deve
tornar o build bem mais rápido e tirar esse risco de derrubar o servidor
de novo. `gcc`/`g++`/`cmake` continuam instalados no `Dockerfile` só como
plano B, caso um dia a versão do `llama-cpp-python` mude pra uma sem
wheel pronta nesse índice.

**Não testei este ajuste rodando o build de verdade** (não tenho como
reproduzir a compilação pesada nem o servidor de produção por aqui) — é
uma mudança de uma linha, documentada e usando o caminho oficial do
próprio projeto `llama-cpp-python` pra evitar compilação, mas vale
acompanhar o próximo build com atenção pra confirmar que ficou rápido
mesmo.

## -28. Corrigidos os 3 achados críticos da auditoria (-27): token do Data Lake por empresa, proteção CSRF em todo o sistema, e sigilo de processo agora bloqueia acesso de verdade

**O que foi pedido:** você pediu pra começar pelos 3 itens críticos da
seção 1 do `AUDITORIA_GRANDE_PORTE.md` — falhas de segurança já ativas em
produção, não melhorias futuras. Os três foram corrigidos e testados
nesta rodada.

### 1) API do Data Lake (`/api/v1/*`) — token por empresa, não mais um único token global

**Como era:** um único `DATALAKE_API_TOKEN` no `.env` dava acesso de
leitura aos processos/movimentações/decisões/prazos de **todas** as
empresas clientes da plataforma, sem filtro nenhum por empresa.

**Como ficou:** tabela nova `tokens_integracao` (`TokenIntegracao`, ver
`app/models/token_integracao.py`) — um token por empresa, gerado e
revogado na tela `/plataforma/empresas/<id>` (nova seção "API de
integração (Data Lake)"). Só o HASH do token é guardado; o valor puro só
aparece uma vez, no momento em que é gerado. Toda consulta em
`app/routes/api_integracao.py` agora filtra pela empresa dona do token
(mesma função `aplicar_escopo_unidade`/`ids_unidades_da_empresa` que o
resto do sistema já usa). **`DATALAKE_API_TOKEN` no `.env` não faz mais
nada** — se você tinha alguma integração usando o token antigo, ela parou
de funcionar; gere um token novo pela tela pra empresa certa e atualize a
configuração do lado do Data Lake.

### 2) Proteção CSRF ativada em todo o sistema

**Como era:** `Flask-WTF` estava instalado mas `CSRFProtect` nunca era
chamado — nenhum formulário do sistema tinha proteção contra CSRF.

**Como ficou:** `CSRFProtect(app)` ativado em `app/__init__.py` (objeto
`csrf` em `app/extensions.py`). Adicionei `<input type="hidden"
name="csrf_token" value="{{ csrf_token() }}">` em todo formulário POST
que eu tinha uma cópia local pra editar (28 arquivos de template — lista
completa no fim desta seção) e um `<meta name="csrf-token">` em
`base.html` pras duas chamadas `fetch(...POST...)` em JavaScript
(notificações). O webhook do Mercado Pago (`/webhooks/mercadopago`) foi
isentado de propósito (`@csrf.exempt`) — é uma chamada servidor-a-servidor
externa, sem cookie de sessão nem token CSRF pra enviar.

**⚠️ Isto é importante — meu ambiente de testes está incompleto (ver seção
-26 mais abaixo), então NÃO tenho certeza de ter coberto 100% dos
formulários do seu projeto real.** Durante os testes desta rodada eu
mesmo descobri três templates que meu sandbox não tinha
(`auth/login.html`, `processos/listar.html`, `admin/unidades.html`) —
`login.html` eu isentei de CSRF temporariamente (ver `app/routes/auth.py`,
`@csrf.exempt` na view `login`, com um comentário explicando) porque não
tive como confirmar/editar esse template sem risco de sobrescrever algo
que já existe de forma diferente no seu projeto de verdade.

**Depois de subir esta versão, rode este comando no seu projeto real**
pra achar qualquer formulário POST que eu não tenha coberto (o comando
lista todo template com `method="post"` que AINDA NÃO tem `csrf_token`):

```bash
grep -rL "csrf_token" $(grep -rl 'method="post"' app/templates -i) 2>/dev/null
```

Se aparecer algum arquivo na lista (além de `auth/login.html`, que já
sei que falta), me avise qual — eu adiciono o campo nesse template
específico. Sem isso, esse formulário específico vai passar a responder
`400 Bad Request` ao tentar salvar, até o campo ser adicionado (nenhum
dado é perdido, o formulário só não salva até o campo existir).

### 3) `segredo_justica` agora bloqueia acesso de verdade (antes era só um rótulo visual)

**Como era:** marcar um processo como sigiloso não tinha nenhum efeito
sobre quem conseguia abri-lo — testei na prática e confirmei que
qualquer usuário da mesma unidade via normalmente.

**Como ficou:** tabela nova `processos_acesso_restrito`
(`ProcessoAcessoRestrito`, ver `app/models/processo.py`) — lista explícita
de quem mais pode ver um processo sigiloso, além de quem já tem acesso
automaticamente (admin da empresa, o responsável pelo processo, quem
cadastrou). Nova função `usuario_pode_ver_processo`/
`checar_acesso_processo_ou_403` em `app/utils/acesso.py`, aplicada em
TODAS as rotas que abrem um processo ou algo vinculado a ele (detalhe,
edição, andamentos, prazos, audiências, documentos — incluindo download —,
análises de IA, cofre de senha do processo, movimentações, captura),
tanto em `app/routes/processos.py` quanto em `app/routes/governanca.py`.
Também criei `filtrar_processos_visiveis`, aplicada nas listagens/painéis
principais (`processos.listar`, `governanca.painel`,
`governanca.fila_intimacoes`) — um processo sigiloso sem permissão nem
aparece mais nessas telas, não só fica bloqueado se você tentar abrir
direto pela URL.

Na tela de edição do processo (`processos/form.html`), quando quem edita
é admin e o processo já existe, aparece uma lista de checkboxes "Quem mais
pode ver este processo" — só tem efeito quando "Processo em segredo de
justiça" está marcado.

**Não cobri nesta rodada** (documentando pra não esconder, não porque
esqueci): os painéis de estatística puramente agregada
(`governanca.metricas`, `governanca.produtividade`,
`governanca.contingenciamento`, `governanca.relatorio_semanal_preview`)
ainda incluem processos sigilosos nos números/médias agregados — não
identificam qual processo é qual nessas telas, então achei um risco bem
menor que os itens já corrigidos, mas é um gap real que fica pra uma
próxima rodada se você quiser fechar também.

**Testado no sandbox:** os três itens foram testados de ponta a ponta
(token isolando dados entre duas empresas diferentes, geração/revogação
pela tela real; POST sem token CSRF bloqueado com 400 e com token
passando, header `X-CSRFToken` funcionando pras chamadas fetch, webhook
isento continuando a funcionar; funcionário sem relação com um processo
sigiloso tomando 403 e sumindo da listagem, responsável e admin sempre
com acesso, acesso concedido pela tela de edição liberando o funcionário
imediatamente).

**Como isso chega em produção:** as duas tabelas novas
(`tokens_integracao`, `processos_acesso_restrito`) são criadas
automaticamente na próxima vez que você rodar `python
sincronizar_schema.py` (mesmo passo de sempre, testei contra um banco
"antigo" simulado e a sincronização detectou exatamente as 2 tabelas
faltando, sem mexer em mais nada). Depois disso, gere os tokens de
integração novos pela tela `/plataforma/empresas/<id>` pra quem precisar
da API do Data Lake, e rode o `grep` acima pra achar qualquer formulário
que eu não tenha coberto com CSRF.

<details>
<summary>Lista completa dos 28 templates com csrf_token adicionado</summary>

admin/unidade_form.html, admin/usuario_form.html,
agenda/compromisso_form.html, agente_ia/conversa.html,
agente_ia/index.html, auth/cadastro_empresa.html, clientes/form.html,
financeiro/listar.html, governanca/mapa_estado_form.html,
governanca/mapa_estado_lista.html, governanca/novo_por_cnj.html,
governanca/regra_proxima_acao_form.html,
governanca/regras_proxima_acao_lista.html,
integracoes/minhas_integracoes.html, licenciamento/minha_licenca.html,
licenciamento/modulos.html, plataforma/empresa_detalhe.html,
plataforma/empresa_form.html, plataforma/licenca_form.html,
plataforma/modulo_form.html, plataforma/modulos_empresa.html,
plataforma/modulos_lista.html, plataforma/painel_licencas.html,
plataforma/planos_form.html, processos/detalhe.html, processos/form.html,
tarefas/form.html, tarefas/listar.html, timesheet/form.html,
timesheet/listar.html.

</details>

## -27. Auditoria completa do sistema como escritório de grande porte — ver `AUDITORIA_GRANDE_PORTE.md`

**O que foi pedido:** analisar o projeto inteiro e testar como se fosse um
escritório de advocacia real, apontando o que falta implementar ou
melhorar para atividades reais de um escritório de **grande porte**.

**Como ficou:** rodei seis auditorias independentes (gestão processual,
financeiro/faturamento, clientes/documentos/LGPD, segurança/permissões/
auditoria, agente de IA/automações, infraestrutura/escala/integrações),
cada uma lendo o código e testando no meu ambiente local. Os achados
completos, organizados por prioridade, estão em `AUDITORIA_GRANDE_PORTE.md`
(arquivo novo, na raiz do projeto). Resumo rápido: os três achados mais
graves são falhas de segurança **já ativas** hoje em produção — a API do
Data Lake (`/api/v1/*`) vaza dados de todas as empresas clientes, não só
da dona do token; não existe proteção CSRF em nenhum formulário apesar do
Flask-WTF estar instalado; e o campo `segredo_justica` não restringe
acesso de verdade (confirmado testando na prática). Nada disso foi
corrigido ainda — o arquivo é só o mapa do que existe e do que falta, para
você decidir prioridade.

## -26. ALERTA IMPORTANTE: descobri que meu ambiente de testes (sandbox) está incompleto — pode ter sobrescrito arquivo(s) de verdade no seu computador na pendência nº -24. Preciso que você confira.

**O que descobri:** ao testar a funcionalidade de preços padrão (pendência
nº -25, logo abaixo), percebi que meu ambiente de testes local NÃO TEM o
arquivo `app/templates/auth/login.html` — a tela de login. Isso é
impossível de ser verdade em produção (você loga no sistema de verdade há
várias rodadas de testes nesta conversa), então a única explicação é: meu
"espelho" local do seu projeto está incompleto — ele só tem os arquivos
que eu efetivamente abri/editei ao longo desta conversa, não uma cópia
completa e fiel de tudo que existe no seu computador.

**Por que isso importa de verdade:** na pendência nº -24 (sistema de
módulos), eu afirmei que várias telas do painel administrativo — 
`plataforma/empresas.html`, `plataforma/empresa_form.html`,
`plataforma/empresa_detalhe.html`, `plataforma/licenca_form.html` e a
pasta inteira `licenciamento/` (incluindo `minha_licenca.html`) —
"não existiam" e por isso eu as criei do zero e ENVIEI DIRETO PRO SEU
COMPUTADOR (sobrescrevendo qualquer arquivo que já estivesse nesses
caminhos exatos). Diante do que acabei de descobrir sobre meu ambiente
estar incompleto, agora não tenho mais certeza de que essa afirmação
estava certa — é bem possível que esses arquivos JÁ EXISTISSEM de verdade
no seu projeto (com conteúdo customizado ou diferente do que eu escrevi),
e que eu simplesmente não tinha uma cópia deles no meu lado pra saber
disso, e os substitui sem querer.

**O que peço que você faça:** se você usa Git (ou qualquer controle de
versão) nesse projeto, rode `git status` e `git diff` nesses arquivos
específicos:
- `app/templates/plataforma/empresas.html`
- `app/templates/plataforma/empresa_form.html`
- `app/templates/plataforma/empresa_detalhe.html`
- `app/templates/plataforma/licenca_form.html`
- `app/templates/licenciamento/minha_licenca.html`

Se o diff mostrar que esses arquivos JÁ TINHAM conteúdo antes (e não
apareciam como arquivo novo/untracked), isso confirma que eu sobrescrevi
algo que já existia — me avise imediatamente com o que você vê, e me
mande o conteúdo anterior (ex.: `git show HEAD~1:caminho/do/arquivo` ou o
que o histórico tiver) que eu reaplico minhas mudanças (o sistema de
módulos) em cima do que já existia, em vez de substituir. Se você não usa
Git, ou se esses arquivos aparecem como novos/nunca versionados, é sinal
de que minha suspeita não se confirma nesse caso — mas ainda vale
verificar visualmente se as telas "Empresas clientes" e "Minha licença"
continuam funcionando como você esperava antes desta rodada.

Sinto muito pela confusão — devia ter sinalizado a limitação do meu
ambiente de testes antes de assumir que "não encontrei o arquivo" queria
dizer "o arquivo não existe em lugar nenhum". A partir de agora vou tratar
qualquer arquivo que eu não consiga localizar no meu sandbox como
"desconhecido pra mim", não como "inexistente", e vou perguntar antes de
criar/substituir um arquivo em um caminho que eu mesmo não escrevi nesta
conversa.

## -25. Preços padrão do cadastro público agora são gerenciáveis pelo admin desenvolvedor (antes só mudavam editando .env e reiniciando o servidor)

**O que foi pedido:** uma área pros admins desenvolvedores gerenciarem os
valores dos planos (mensal/trimestral/anual) mostrados no cadastro
público self-service (`/cadastrar-empresa`) — antes esses valores vinham
fixos de `PRECO_PADRAO_MENSAL`/`TRIMESTRAL`/`ANUAL` no `.env`, só mudando
com uma edição manual do arquivo e reinício do servidor.

**Como ficou:** nova tela `/plataforma/planos` (menu lateral, seção
"Plataforma" → "Preços padrão"), só pro admin desenvolvedor, com um
formulário simples pros três valores. Guardado numa tabela nova de
configuração única (`ConfiguracaoPlataforma`, sempre uma linha só) — 
enquanto ninguém salva nada por essa tela, o sistema continua caindo nos
valores de `.env`/`config.py` de sempre (sem quebrar nada em produção
antes de rodar a sincronização de schema). Importante: mudar aqui só
afeta o preço mostrado pra quem se cadastra sozinho DAQUI PRA FRENTE —
empresas já cadastradas continuam com o valor negociado que já tinham
(`Licenca.valor_negociado`), sem mudar sozinho.

Arquivos novos: `app/models/configuracao.py` (`ConfiguracaoPlataforma`),
`app/templates/plataforma/planos_form.html`. Alterados:
`app/models/__init__.py`, `app/routes/plataforma.py` (rota
`editar_planos`), `app/routes/auth.py` (`cadastrar_empresa` agora lê o
preço de `ConfiguracaoPlataforma.obter()` em vez de direto do config),
`app/templates/base.html` (link no menu).

**Testado no sandbox:** leitura sem nenhuma configuração salva ainda cai
no fallback do `.env` sem gravar nada sozinha; salvar pela tela persiste
os três valores; salvar de novo atualiza a mesma linha (não duplica);
cadastro público reflete o valor novo tanto na tela quanto no valor
realmente gravado na licença da empresa criada.

**Como isso chega em produção:** a tabela nova (`configuracoes_plataforma`)
é criada automaticamente na próxima vez que você rodar
`python sincronizar_schema.py` (mesmo passo de sempre — pode rodar junto
com a sincronização da pendência nº -24, é a mesma execução).

## -24. Sistema de módulos vendidos separadamente — cada empresa cliente pode ter um pacote diferente de telas liberadas

**O que foi pedido:** separar o sistema em módulos, de forma que o admin
desenvolvedor consiga gerenciar quais módulos cada empresa cliente tem,
o cliente escolhendo os módulos desejados ANTES do primeiro pagamento, e
podendo pedir módulos adicionais DEPOIS, com custo a mais.

**Como ficou (decisões tomadas, todas ajustáveis depois):**

- Um módulo por blueprint já existente (Processos, Clientes, Financeiro,
  Tarefas, Agenda, Timesheet, Governança de carteira, Agente de IA) — não
  pacotes agrupados. Cadastrado em `Modulo` (tabela nova `modulos`), com a
  `chave` de cada um batendo exatamente com o nome do blueprint no código
  (é assim que o sistema descobre a que módulo uma tela pertence, sem
  precisar de nenhum mapa manual redundante).
- "Clientes" e "Processos" marcados como `obrigatorio=True` no catálogo
  inicial (semeado automaticamente, ver abaixo) — toda empresa tem os
  dois sempre, sem precisar de nenhuma seleção, porque são a base mínima
  do produto (sem cadastro de cliente/processo não tem o que os outros
  módulos operarem em cima). Os demais seis ficam opcionais por padrão.
  Essa é uma decisão de PRODUTO, não técnica — totalmente reversível (ou
  extensível pra outros módulos) depois, direto pela tela
  `/plataforma/modulos`, sem precisar mexer em código.
- Seleção ANTES do primeiro pagamento: ao cadastrar uma empresa nova em
  `/plataforma/empresas/nova` (você, admin desenvolvedor, no painel — foi
  a opção que você escolheu, em vez de um formulário público de
  orçamento), agora aparecem checkboxes dos módulos opcionais com um
  campo de valor adicional por módulo, somando um total sugerido pra
  `Licenca.valor_negociado` (o valor final continua sempre editável — a
  soma é só uma referência, igual ao preço sugerido de cada módulo nunca
  ser mostrado como "tabela de preços" pra nenhuma empresa).
- Pedido DEPOIS do primeiro pagamento, com custo a mais: a própria
  empresa cliente (usuário admin dela) agora tem uma tela "Módulos" (menu
  lateral, "Minha licença" → "Módulos") onde vê o que já tem e pode
  clicar "Solicitar" num módulo novo. Isso cria um pedido pendente
  (status "solicitado") e avisa você (notificação, o sininho "Avisos" no
  topo) — você define o valor adicional e ativa em
  `/plataforma/empresas/<id>/modulos`. Não tem checkout automático nem
  preço público — mesmo espírito de "sempre negociado por você" que já
  existia pra Licenca (ver pendência antiga sobre `valor_negociado`).
  Se um dia isso incomodar na prática (muitos pedidos, você preferir um
  preço fixo com pagamento automático via Mercado Pago), dá pra evoluir
  reaproveitando a integração que já existe em `Pagamento` — ficou fora
  do escopo desta rodada de propósito, pra não versionar processo de
  cobrança sem ter certeza de como você quer que funcione.
- Bloqueio de acesso: nova função `bloquear_modulo_nao_contratado` em
  `app/__init__.py`, rodando LOGO DEPOIS do bloqueio de licença que já
  existia (`bloquear_empresa_sem_licenca_ativa`) — sequência importa:
  não faz sentido avisar "módulo não contratado" pra quem nem tem licença
  ativa. Admin desenvolvedor e a empresa dona da plataforma nunca são
  bloqueados por módulo (mesmo bypass de sempre). Uma tela de um
  blueprint SEM módulo cadastrado no catálogo (login, painel, admin,
  api, plataforma, licenciamento, integrações) nunca é bloqueada — só
  entra nessa checagem quem tem uma linha correspondente em `Modulo`.

**Gap real encontrado no caminho (não criado por mim, já existia):**
ao testar o fluxo de ponta a ponta, descobri que várias telas que este
sistema de módulos precisava pra funcionar de verdade simplesmente NÃO
EXISTIAM ainda — os templates `plataforma/empresas.html`,
`plataforma/empresa_form.html`, `plataforma/empresa_detalhe.html`,
`plataforma/licenca_form.html` e a pasta inteira `licenciamento/`
(incluindo `minha_licenca.html`) estavam faltando, apesar das ROTAS já
existirem no código. Ou seja: hoje em produção, `/plataforma/empresas`
(cadastrar/ver/editar empresa cliente), o link "Minha licença" que todo
admin de empresa cliente vê no menu, e o cadastro público self-service em
`/cadastrar-empresa` (que redireciona pra "Minha licença" assim que
termina) provavelmente estão devolvendo erro 500 pra quem clica. Como
essas telas são exatamente onde o sistema de módulos precisava viver,
construí todas elas nesta rodada (estilo consistente com o resto do
sistema, mesmas classes CSS/padrões de formulário já usados em
Governança) — então isso sai já corrigido junto, mas vale você testar
esse fluxo específico (cadastro público de empresa nova) num teste real,
já que eu não tenho como simular o Mercado Pago de verdade no sandbox.

**Arquivos novos:**
- `app/models/modulo.py` (`Modulo`, `EmpresaModulo`)
- `app/utils/modulos.py` (regras de negócio: catálogo, liberar/solicitar/
  aprovar/cancelar módulo, seed inicial `MODULOS_CATALOGO_INICIAL`)
- Templates: `plataforma/empresas.html`, `empresa_form.html`,
  `empresa_detalhe.html`, `licenca_form.html`, `modulos_lista.html`,
  `modulo_form.html`, `modulos_empresa.html`; `licenciamento/minha_licenca.html`,
  `licenciamento/modulos.html`.

**Arquivos alterados:**
- `app/models/empresa.py` (relacionamento `modulos_associados`)
- `app/models/__init__.py` (registro dos models novos)
- `app/__init__.py` (novo `before_request` de bloqueio por módulo)
- `app/routes/plataforma.py` (CRUD do catálogo, seleção de módulos no
  cadastro de empresa nova, gestão de módulos por empresa)
- `app/routes/licenciamento.py` (tela do cliente pra ver/solicitar módulos)
- `app/templates/base.html` (menu: "Módulos" pro cliente, "Catálogo de
  módulos" pro admin desenvolvedor)
- `sincronizar_schema.py` e `criar_tabelas.py` (seed automático e
  idempotente do catálogo inicial — ver próxima seção)

**Como isso chega em produção:** as tabelas `modulos` e `empresa_modulos`
são criadas automaticamente na próxima vez que você rodar
`python sincronizar_schema.py` no servidor (mesmo processo de sempre) —
e, PELA PRIMEIRA VEZ, esse script também semeia dados (só linhas NOVAS,
identificadas por `chave`; nunca sobrescreve o que você já tiver editado
manualmente pela tela `/plataforma/modulos` numa execução anterior — ver
a segunda exceção documentada no topo do próprio arquivo). Rode o script,
confira o catálogo em `/plataforma/modulos`, e ajuste preços/obrigatório
como fizer sentido pro seu negócio antes de cadastrar a próxima empresa.

**Testado no sandbox** (Flask test_client simulando login de admin
desenvolvedor e de admin de empresa cliente, banco sqlite descartável):
catálogo inicial semeado corretamente e de forma idempotente; CRUD
completo do catálogo de módulos; cadastro de empresa nova com módulos
selecionados virando `incluido_inicial` com o valor certo; módulo
obrigatório liberado mesmo sem nenhuma linha em `EmpresaModulo`; bloqueio
de acesso funcionando nos três estados (não contratado, solicitado,
ativo) com o redirecionamento certo em cada caso; fluxo completo de
solicitação pelo cliente → notificação pro admin desenvolvedor → aprovação
→ acesso liberado; admin desenvolvedor nunca bloqueado por módulo, mesmo
em blueprint sem nenhum módulo incluído. Não testado (sem como simular no
sandbox): o Mercado Pago de verdade no cadastro público self-service —
peço que você valide esse fluxo específico com um pagamento real de teste.

## -23. Mesmo bug da pendência nº -22, só que com vírgula em vez de ponto — a checagem de valor voltou a sinalizar o R$ 10.000 real, agora escrito "R$ 10,000"

**O que foi reportado:** logo depois da correção da pendência nº -22 (que
tratava "R$ 10.000" — ponto sem vírgula — como o valor real de dez mil), um
novo rascunho de petição pro mesmo processo voltou a sinalizar o mesmo valor
real como "não confirmado" — só que desta vez o modelo escreveu o valor como
"R$ 10,000" (vírgula, sem ponto, estilo de milhar em inglês) em vez de
"R$ 10.000". A correção anterior só tinha ensinado a função a desconfiar de
UM ponto sozinho seguido de 3 dígitos; não tinha aplicado a mesma lógica pro
caso espelhado com vírgula sozinha.

**Correção:** apliquei a mesma regra (moeda nunca tem 3 casas decimais) para
o caso de vírgula sozinha também: se só aparece vírgula (sem ponto) e o
grupo depois dela tem exatamente 3 dígitos — ou há mais de uma vírgula —
trata como separador de milhar, não decimal. Testei tanto o caso isolado
("R$ 10,000" → bate com 10000.00 do cadastro) quanto um texto com as duas
formas juntas (ponto e vírgula, ambas representando o mesmo valor real) e
confirmei que nenhuma das duas gera mais aviso de falso-positivo, e que
valores realmente inventados continuam sendo sinalizados normalmente.

Este é o mesmo tipo de ambiguidade da pendência anterior, só que no
"espelho" (vírgula em vez de ponto) — o padrão de fundo é que qualquer
separador sozinho seguido de um grupo de exatamente 3 dígitos é
estruturalmente impossível de ser decimal de moeda (moeda só tem 0, 1 ou 2
casas decimais), então a mesma regra agora vale pros dois separadores.

Arquivo alterado: `app/utils/analise_processo_ia.py`
(`_normalizar_valor_monetario` — ramo da vírgula sozinha ganhou a mesma
lógica que já existia pro ramo do ponto sozinho).

## -22. Correção: o valor de R$ 10.000 do relato da pendência nº -21 NÃO era inventado — era o valor real que você mesmo cadastrou, e a checagem automática tinha um falso-positivo por ambiguidade de formato de número

**Correção importante sobre a pendência anterior:** você apontou, com razão,
que o valor de R$ 10.000 mencionado no relato da pendência nº -21 foi
digitado por você mesmo no campo "Valor da causa" — não foi inventado pelo
modelo. Eu tinha concluído o contrário com base numa tela antiga (de antes de
você preencher o campo) e escrevi isso errado tanto na conversa quanto nos
comentários do código e na pendência nº -21. Peço desculpa pela confusão:
a afirmação de que "o campo Valor da causa está vazio e o modelo inventou
~R$ 10.000" estava incorreta. Deixei uma nota de correção também dentro do
próprio código (`_checar_grounding`, em `app/utils/analise_processo_ia.py`),
já que a pendência nº -21 continua registrada abaixo por histórico, mas o
trecho específico sobre o campo vazio não reflete mais a realidade.

**O bug de verdade, que essa confusão ajudou a descobrir:** a função que
compara "o valor que o modelo escreveu" com "o valor real cadastrado"
(`_normalizar_valor_monetario`) tinha uma ambiguidade não tratada: quando o
modelo escreve um valor com PONTO e sem vírgula nenhuma (ex.: "R$ 10.000",
do jeito que o modelo local costuma escrever, sem seguir a formatação BR
completa com vírgula decimal), não dava pra saber se aquele ponto era
separador de milhar ("dez mil", que é o que o modelo quis dizer) ou separador
decimal ("dez inteiros e zero-zero-zero", formato cru que vem do banco). A
função escolhia sempre a leitura decimal — "10.000" virava 10.0 — o que NUNCA
batia com o valor real de 10000.0 guardado no cadastro, e por isso a checagem
acusava, errado, que o valor "não aparece nos dados reais do processo".

**Correção aplicada:** já que valor monetário nunca tem 3 casas decimais (só
0, 1 ou 2), agora a função usa essa regra pra desempatar: se o(s) grupo(s)
depois do(s) ponto(s) tiverem exatamente 3 dígitos (ou houver mais de um
ponto), trata todos os pontos como separador de milhar; só com 1 ou 2 dígitos
depois do único ponto é que continua tratando como decimal. Testei essa regra
contra uma bateria de casos (incluindo o caso real relatado, "10.000" → deve
bater com 10000.00 do cadastro; "100.00" com só 2 casas → não pode virar
100000; "1.234.567" com vários grupos; valores já vistos em relatos
anteriores como "12.768" e "9.054") e também um teste de ponta a ponta
simulando o digest real com Valor da causa = R$ 10000.00 e uma resposta da
IA escrevendo "R$ 10.000" — confirmado que não gera mais aviso de
falso-positivo, e que um valor genuinamente inventado (ex. "R$ 99.999,99",
ausente do digest) continua sendo sinalizado normalmente.

Também reforcei o comentário da função `_checar_grounding` deixando
explícito que ela é uma checagem MECÂNICA de texto (compara dígitos), não
uma checagem de verdade/mentira — um aviso dela significa "confira este
ponto", nunca "isto é uma alucinação confirmada". Foi exatamente essa
diferença que causou a confusão desta rodada.

Arquivo alterado: `app/utils/analise_processo_ia.py`
(`_normalizar_valor_monetario` com a nova regra de desempate, docstring de
`_checar_grounding` corrigida e reforçada).

## -21. Checagem automática (pendência nº -20) tinha um bug que deixava passar valor seguido de vírgula de pontuação + estendida também para o Resumo

**O que foi reportado:** testando de novo depois da pendência nº -20, a
checagem automática funcionou bem para várias citações legais inventadas e
alguns valores — mas o próprio texto trazia inconsistência forte: o mesmo
processo apareceu com "R$ 100,00", "R$ 100.00" e "(mil reais)" ao mesmo
tempo (nem "cem reais" bate com "mil reais" escrito do lado), e depois "R$
10.000"/"R$ 10,000" mencionados de novo na seção de pedidos — tudo sinalizado
corretamente como não confirmado, o que já ajudou bastante. Só que ao
investigar, achei um bug na PRÓPRIA checagem (não no modelo): quando um
valor real aparece seguido de vírgula de pontuação da frase (ex.: "R$
10.000,00, em fase..." — a primeira vírgula é decimal, a segunda é só
pontuação), a expressão regular antiga capturava a vírgula de pontuação
JUNTO do valor ("R$ 10.000,00,"), o que quebrava a conversão pra número e
fazia a checagem simplesmente PULAR esse valor em silêncio — um
falso-negativo, o oposto do que deveria acontecer.

**Correção:** ajustei a expressão regular de valores em R$ pra sempre
terminar num dígito de verdade (nunca num separador solto), o que resolve
tanto esse caso (não captura mais a vírgula de pontuação da frase) quanto
um problema irmão que só apareceu ao testar mais fundo — números de 4+
dígitos sem nenhum separador (ex.: "R$ 5000", "R$9054") estavam sendo
CORTADOS no meio pela versão de teste seguinte que eu tentei (virava "R$
500" ou "R$905", perdendo um dígito) — a versão final captura os dois casos
corretamente. Testei os dois cenários lado a lado pra não reintroduzir um
problema tentando corrigir o outro.

**⚠️ Correção (ver pendência nº -22 acima):** o parágrafo abaixo, escrito
nesta rodada, partiu de uma tela desatualizada e concluiu errado que o
campo "Valor da causa" estava vazio e que o ~R$ 10.000 mencionado era
invenção do modelo — na verdade você já tinha preenchido esse campo, o
valor era real, e o que causou o alarme falso foi um bug separado na própria
checagem (ambiguidade de formato numérico), corrigido na pendência nº -22.
Mantido abaixo só por histórico de como o problema foi rastreado.

**Também notei, olhando os dados de novo (texto original, ver correção
acima):** o campo "Valor da causa" deste processo real está VAZIO no
cadastro (você mesmo confirmou isso na tela "Editar processo" mais cedo) —
e mesmo assim, tanto o Resumo quanto o Rascunho de petição mencionaram um
valor de causa (~R$ 10.000) com confiança, em gerações SEPARADAS. Ou seja,
esse tipo de invenção não é
exclusivo do rascunho de petição — o Resumo dos autos também é vulnerável,
só que a checagem automática da pendência nº -20 só tinha sido ligada pro
rascunho. Agora ela roda para os DOIS tipos de análise (resumo e rascunho),
já que o risco de um valor inventado passar despercebido é o mesmo nos
dois. Vale conferir/preencher o campo "Valor da causa" de verdade neste
processo, se você tiver esse dado à mão — com o campo preenchido
corretamente, o modelo tem a informação real disponível e não precisa mais
"adivinhar".

Arquivo alterado: `app/utils/analise_processo_ia.py` (regex de valor
monetário corrigida, checagem de grounding passou a rodar pros dois tipos
de análise, função renomeada de `_checar_grounding_rascunho` para
`_checar_grounding` já que não é mais exclusiva do rascunho). Testado no
sandbox: valor seguido de pontuação de frase agora é capturado
corretamente (era o falso-negativo relatado), múltiplas variantes malformadas
continuam sendo pegas, valor real (em formato diferente do texto gerado)
continua sem gerar falso-positivo, e o Resumo dos autos agora também exibe
o aviso quando aplicável — reproduzindo o cenário exato relatado (valor de
causa vazio no cadastro, modelo inventa um valor mesmo assim).

## -20. Rascunho de petição inventou valores em R$ e uma citação legal, sem marcar nada como [REVISAR] — checagem automática pós-geração

**O que foi reportado:** com o loop de repetição já corrigido (pendência nº
-19), um novo teste de rascunho de petição gerou um texto bem estruturado
(endereçamento, DOS FATOS, DO DIREITO, DOS PEDIDOS, fecho, seção de
revisão) — mas o CONTEÚDO tinha problema grave: o modelo inventou um valor
de penhora ("R$ 5000"), inventou uma "avaliação do imóvel" de R$ 12.768 e um
"histórico anterior" de R$ 9.054 (nenhum desses três dados existe em lugar
nenhum do processo cadastrado), inventou uma citação legal ("Lei nº 167,
artigo 89 do CPC" — não existe fundamento jurídico correspondente a essa
citação) e mencionou "o último leilão em julho/2023" sem essa data constar
em nenhum lugar dos dados reais — e nenhum desses itens veio marcado como
"[REVISAR: ...]", mesmo o system prompt (`RASCUNHO_SYSTEM`) e o pedido do
próprio usuário instruindo EXPLICITAMENTE pra nunca inventar valor/data/
citação e sempre marcar como revisão em vez disso.

**Por que isso é mais grave que os problemas anteriores:** os bugs das
pendências nº -15/-19 (repetição, duplicação) eram óbvios de perceber — um
parágrafo repetido 40 vezes não engana ninguém. Este aqui é o oposto:
números específicos, formatados como se fossem dado real extraído dos
autos, apresentados com confiança total. Um advogado batendo o olho rápido
pode facilmente achar que "R$ 12.768" veio de uma avaliação de verdade no
processo — quando na verdade não veio de lugar nenhum, foi inventado pelo
modelo.

**Causa:** limitação de capacidade do modelo local (Qwen 1,5B, pequeno,
grátis, sem GPU) — mesmo com a instrução escrita de forma bem explícita
(no system prompt E no pedido do usuário), um modelo desse porte pode
simplesmente não seguir a regra "nunca invente, marque como [REVISAR]" de
forma confiável. Não é um bug de configuração que dá pra "consertar" só
escrevendo o prompt melhor — já tentamos isso nas pendências anteriores e o
modelo ainda assim inventou dado nesta rodada. É um teto de capacidade.

**Correção (defesa em profundidade, não confia só na palavra do modelo):**
adicionei uma checagem automática DETERMINÍSTICA (código Python puro, não
outra pergunta pro modelo) que roda DEPOIS da geração de todo rascunho de
petição: extrai todo valor em R$ mencionado no texto gerado e confere se
ele aparece, de verdade, nos dados reais do processo que foram injetados no
contexto (`digest`) — se não aparecer, é sinalizado como possivelmente
inventado. Da mesma forma, procura citação de lei/artigo/súmula que NÃO
esteja marcada com "[REVISAR: ...]" como foi pedido, e sinaliza também. Os
avisos aparecem num bloco "⚠️ VERIFICAÇÃO AUTOMÁTICA" bem no topo do
rascunho salvo, ANTES do texto da peça — não é mais preciso confiar que o
modelo vai se auto-policiar corretamente, o sistema confere por fora.

Testado com o texto real que você reportou (reproduzido no teste): a
checagem identificou corretamente os três valores inventados (R$ 5000, R$
12.768, R$ 9.054) e a citação legal não marcada ("Lei nº 167", "artigo
89"), sem acusar falso-positivo no valor real do processo (R$ 10.000,00,
que estava correto). Também reforcei mais uma vez o `RASCUNHO_SYSTEM` com
uma frase específica sobre valores em R$ (redundante com a checagem
automática de propósito — mais uma camada, não substitui a outra).

**Limite honesto do que essa checagem cobre:** ela pega valor em R$ e
citação legal claramente identificável por padrão de texto — não cobre
frases inventadas sem número nem citação (ex.: "o imóvel está em bom estado
de conservação" sem nenhum dado sobre isso no processo) nem datas inventadas
soltas sem contexto de leilão/valor. Continua sendo indispensável a revisão
humana completa antes de protocolar qualquer coisa — a checagem automática
é uma rede de segurança a mais pros erros mais perigosos (número
específico, citação legal), não uma garantia de rascunho 100% confiável.
Se esse tipo de invenção continuar incomodando na prática, a solução
estrutural é a API do Claude com chave própria (BYOK), que erra muito menos
nesse tipo de instrução — mas mesmo lá, revisão humana continua obrigatória.

Arquivo alterado: `app/utils/analise_processo_ia.py` (`_checar_grounding_rascunho`,
`_normalizar_valor_monetario`, reforço no `RASCUNHO_SYSTEM`, chamada da
checagem em `gerar_analise` só pro tipo `rascunho_peticao`). Testado no
sandbox local com o texto real reportado e com um cenário sintético
adicional, incluindo confirmação de que a checagem NÃO roda no tipo
`resumo` (só faz sentido pro rascunho de petição, onde o risco jurídico de
um dado inventado é maior).

## -19. Rascunho de petição travou num loop repetindo a mesma frase + resumo listou movimentações como se fossem prazos

**O que foi reportado:** depois da correção do estouro de contexto
(pendência nº -18), um novo teste de rascunho de petição devolveu a mesma
frase ("O processo foi analisado e concluído em 15 de outubro de 2024, com
publicação e remessa dos atos.") repetida dezenas de vezes seguidas até
estourar o limite de tokens — nenhuma petição de verdade foi gerada. Além
disso, no "Resumo dos autos" do mesmo processo, a seção PRAZOS PENDENTES
misturou o único prazo de verdade cadastrado com uma lista de
MOVIMENTAÇÕES (ex.: "15/10/2024: Conclusão", "07/08/2024: Publicação") como
se todas fossem prazos pendentes — o que é enganoso pra quem lê rápido
(dá a entender que há 11 prazos vencendo, quando só existe 1 registro real
de prazo; o resto são só eventos que já aconteceram).

**Causa nº 1 (o loop de repetição):** a biblioteca do modelo local
(llama-cpp-python) usa `repeat_penalty=1.0` por padrão quando esse parâmetro
não é passado explicitamente — ou seja, **nenhuma penalidade contra
repetição**. Confirmei isso direto na assinatura da função na biblioteca
instalada. Combinado com um processo de histórico repetitivo (várias
movimentações de texto quase idêntico — muitos "Ato ordinatório" seguidos,
por exemplo), o modelo pequeno tende a entrar num loop: uma vez que começa
a repetir uma frase, sem nada desencorajando isso, ele continua repetindo
até acabar o espaço de tokens da resposta. É um problema bem conhecido em
modelos GGUF pequenos rodando sem esse parâmetro configurado.

**Causa nº 2 (movimentação virando "prazo"):** o system prompt do resumo
pedia pra não duplicar listas entre seções (correção da pendência nº -15),
mas não deixava claro que os itens do bloco "Movimentações capturadas" do
contexto NUNCA deveriam aparecer na seção PRAZOS PENDENTES — o modelo, ao
ver várias linhas com data no contexto, colocou algumas na seção errada.

**Correção:**
1. `app/utils/ia_local.py` agora passa `repeat_penalty=1.2` explicitamente
   pro modelo (valor padrão bem estabelecido pra esse tipo de modelo pequeno
   — alto o suficiente pra cortar loops, sem deixar o texto estranho).
2. `montar_digest_processo` (`app/utils/analise_processo_ia.py`) ganhou uma
   função nova, `_agrupar_movimentacoes_repetidas`, que colapsa sequências
   de movimentações CONSECUTIVAS com o mesmo texto numa única linha (ex.:
   "Ato ordinatório — 5 ocorrências entre 02/08/2024 e 06/08/2024" em vez de
   5 linhas iguais) — isso ataca o problema por outro ângulo também: um
   contexto de entrada já repetitivo aumenta a chance do modelo repetir na
   saída, então reduzir a repetição na ENTRADA ajuda a prevenir o loop, além
   de economizar espaço no orçamento de caracteres do digest (mais dado de
   verdade cabe).
3. O rótulo do bloco de movimentações no digest e o system prompt do resumo
   (`RESUMO_SYSTEM`) agora deixam explícito: itens de movimentação (mesmo
   com data) vão SÓ na seção ÚLTIMOS ATOS RELEVANTES, nunca em PRAZOS
   PENDENTES — só o que vier do bloco "Prazos ainda em aberto" (prazos de
   verdade, cadastrados na tabela de Prazos) pode aparecer ali.

Como sempre com o modelo local (pequeno, roda de graça, sem GPU): essas
correções reduzem bastante a CHANCE desses dois problemas, mas não
eliminam 100% a possibilidade de o modelo cometer erros parecidos em outro
processo com características diferentes — é uma limitação de tamanho do
modelo, não algo que dê pra garantir sem trocar de modelo/provedor (modelo
"grande" local, ou API do Claude BYOK — ambos já documentados como opção em
rodadas anteriores).

Arquivos alterados: `app/utils/ia_local.py` (`repeat_penalty=1.2`) e
`app/utils/analise_processo_ia.py` (agrupamento de movimentações repetidas
+ rótulos/instruções mais explícitas sobre qual bloco vai em qual seção).
Testado no sandbox local: agrupamento de movimentações consecutivas
repetidas confirmado com um histórico simulado igual ao do processo real
reportado (várias "Ato ordinatório"/"Publicação" seguidas viram uma linha
só cada), e `repeat_penalty=1.2` confirmado sendo passado corretamente pra
chamada do modelo (não dá pra testar a geração de texto de verdade neste
ambiente sem o modelo/GPU, só a lógica ao redor dela).

## -18. Rascunho de petição só "ecoava" o próprio pedido de volta, sem gerar a peça — estouro da janela de contexto do modelo local

**O que foi reportado:** depois da correção do timeout (pendência nº -17),
um novo pedido de rascunho de petição — com uma instrução bem longa e
estruturada (9 itens pedindo verificação de penhora, leilão, hasta pública,
fundamentação no CPC etc.) — terminou mais rápido, mas o resultado foi só o
texto do próprio pedido "copiado" de volta, sem petição nenhuma gerada.

**Causa:** o modelo local roda numa janela de contexto pequena
(`IA_LOCAL_CONTEXT_SIZE=4096` tokens, ver `app/utils/ia_local.py`) — e esse
espaço é compartilhado entre TUDO: o system prompt com as instruções de como
montar a peça (`RASCUNHO_SYSTEM`), o digest com os dados reais do processo
(`montar_digest_processo`, até `LIMITE_PADRAO_CHARS=6000` caracteres) E a
instrução que o usuário escreveu — que não tinha limite nenhum de tamanho.
Antes desta correção, o código simplesmente mandava gerar até 1400 tokens de
resposta (`max_tokens`) sem checar se esse tanto ainda cabia depois de tudo
isso — com uma instrução tão detalhada quanto a usada no teste, o prompt
inteiro (system + digest + instrução) já passava perto ou além dos 4096
tokens disponíveis. Sem espaço de sobra pra gerar algo novo, o modelo
degenera nesse tipo de situação: em vez de travar ou dar erro, ele
simplesmente "continua" o texto mais recente que ainda está no seu campo de
visão — que, nesse caso, era o próprio pedido do usuário — daí "copiar de
volta" o prompt em vez de escrever a petição. Não é um bug de lógica na
peça em si, é estouro de contexto mesmo, sintoma bem conhecido em modelos
pequenos.

**Correção:** `ia_local.gerar_resposta` agora CONTA de verdade quantos
tokens o prompt (system + instrução) ocupa antes de pedir a geração, e
limita o `max_tokens` pedido ao espaço que realmente sobra na janela de
contexto — nunca mais pede mais do que cabe. Se mesmo uma resposta mínima
(150 tokens) não couber (instrução gigante demais mesmo assim), a geração
nem é tentada: aparece um aviso claro pedindo pra encurtar a instrução, em
vez de gerar uma resposta capenga ou o eco do próprio prompt. Também
adicionei uma notinha embaixo do campo de instrução, na tela do processo,
explicando esse limite compartilhado — pra já ficar claro de antemão, sem
precisar tentar e ver o erro.

Isso é uma limitação real e conhecida do modelo local (pequeno, de
propósito, pra rodar de graça no próprio servidor sem GPU) — já documentada
desde o início em `analise_processo_ia.py`. Pedidos muito longos e
detalhados (como o do teste, com 9 itens) têm mais chance de esbarrar nesse
limite, principalmente em processos com histórico grande (digest também
maior). Se isso for uma necessidade recorrente do escritório — instruções
bem detalhadas com frequência —, o caminho é a API do Claude com chave
própria (BYOK, "Minhas Integrações"), que tem uma janela de contexto bem
maior e não sofre desse limite.

Arquivos alterados: `app/utils/ia_local.py` (contagem de tokens do prompt +
`max_tokens` dinâmico + erro amigável quando não cabe) e
`app/templates/processos/detalhe.html` (nota explicativa no campo de
instrução). Testado no sandbox local com um modelo local simulado (3
cenários: prompt pequeno usa o `max_tokens` pedido normalmente; prompt
grande tem o `max_tokens` reduzido automaticamente pro espaço disponível;
prompt gigante demais levanta o aviso amigável em vez de gerar qualquer
coisa) — sem acesso ao modelo de verdade neste ambiente (sem GPU/o peso do
modelo não está aqui), mas a lógica de contagem/corte é a mesma
independente do modelo carregado.

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
