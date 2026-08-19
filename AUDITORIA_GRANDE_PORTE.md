# Auditoria completa do JusControl para operação de escritório de grande porte
**Data:** 19/08/2026

Você pediu para eu analisar o projeto inteiro e testar como se fosse um
escritório de advocacia real, apontando o que falta implementar ou
melhorar para suportar as atividades reais de um escritório de **grande
porte**. Fiz isso rodando seis auditorias independentes, uma por área
(gestão processual, financeiro/faturamento, clientes/documentos/LGPD,
segurança/permissões/auditoria, agente de IA/automações,
infraestrutura/escala/integrações), cada uma lendo o código-fonte real,
testando funcionalidades no meu ambiente de testes local sempre que
possível (com um banco temporário e login simulado, nunca no seu banco
de produção) e citando arquivo e comportamento exato por trás de cada
achado.

Este documento reúne os achados das seis auditorias em uma lista única,
organizada por prioridade — não por área — porque vários achados de áreas
diferentes na verdade apontam para o mesmo problema de fundo (por
exemplo: a falta de uma fila de processamento em segundo plano aparece
tanto na auditoria de infraestrutura quanto na de IA).

Importante: nada aqui foi corrigido ainda. Isso é um mapa do que existe e
do que falta, para você decidir prioridade e ritmo — não uma lista de
mudanças já aplicadas.

**Atualização:** os três itens da seção 1 (críticos) já foram corrigidos
e testados — ver `PENDENCIAS.md` seção -28 para o detalhe completo de
cada correção. O resto deste documento (seções 2, 3 e 4) continua
representando trabalho ainda não iniciado.

---

## 1. Nível crítico — risco de segurança ativo, hoje, em produção — ✅ CORRIGIDO (ver PENDENCIAS.md -28)

Estes três itens não são "recursos que faltam para crescer". São falhas
que já podem estar sendo exploradas agora, com o sistema exatamente como
está. Recomendo tratá-los antes de qualquer outra coisa desta lista.

### 1.1 A API do Data Lake (`/api/v1/*`) vaza dados de TODAS as empresas clientes, não só da que o token pertence

**Onde:** `app/routes/api_integracao.py`. O token de acesso
(`DATALAKE_API_TOKEN`, definido em `config.py:58`) é um único segredo
global — não existe um token por empresa. Até aí, razoável para uma
integração interna sua. O problema real é que, dentro das rotas dessa
API, as consultas (`Processo.query`, `Movimentacao.query` e as demais)
**não filtram por empresa**. Isso é diferente da API interna
(`app/routes/api.py`), que corretamente usa `aplicar_escopo_unidade` —
o mesmo padrão de isolamento multi-tenant usado em todo o resto do
sistema.

Na prática: quem tiver esse único token (por exemplo, se ele vazar de um
`.env`, de um log, de um repositório, ou for compartilhado com o parceiro
errado) consegue ler os processos, movimentações, decisões e prazos de
**qualquer empresa cliente cadastrada na plataforma**, não só da sua. Para
um sistema multi-tenant que hospeda dados de vários escritórios/clientes
diferentes, isso é uma quebra de confidencialidade entre clientes — o
tipo de falha que, se descoberta por um cliente afetado, vira problema
contratual e reputacional sério, além de possível violação de LGPD (dado
de terceiro exposto sem base legal para aquele destinatário específico
ver aquele dado).

**Correção recomendada:** cada empresa cliente que precisar dessa
integração deveria ter seu próprio token, associado a ela no banco (uma
tabela simples `empresa_id` + `token` + `ativo`, análoga ao padrão que já
existe para `Licenca`), e toda consulta dentro de `api_integracao.py`
passaria a filtrar por essa empresa — reaproveitando a mesma função
`aplicar_escopo_unidade`/equivalente que a API interna já usa. É uma
mudança de escopo pequeno e cirúrgico, sem redesenhar a API.

### 1.2 Não existe proteção CSRF em nenhum formulário do sistema

`Flask-WTF==1.2.1` está no `requirements.txt`, mas nunca é de fato
ativado — não há chamada a `CSRFProtect()` em nenhum lugar de
`app/__init__.py`, e nenhum template inclui `csrf_token()`. Isso significa
que todo formulário POST do sistema — cadastro de cliente, cadastro de
processo, lançamento financeiro, timesheet, telas de admin, telas de
plataforma/licenciamento — pode ser acionado por um site malicioso
externo enquanto um usuário logado do JusControl visita essa página em
outra aba, sem que ele perceba (esse é exatamente o ataque que CSRF
protege contra: o navegador envia o cookie de sessão automaticamente,
então o servidor não tem como saber que o pedido não veio do seu próprio
formulário).

**Correção recomendada:** ativar `CSRFProtect(app)` em `app/__init__.py`
e adicionar `{{ csrf_token() }}` (ou o helper equivalente do Flask-WTF)
em cada `<form>` dos templates. É mecânico, mas trabalhoso porque toca
praticamente todo template do sistema — vale planejar como uma rodada
dedicada, testando login e os fluxos principais depois, porque um form
esquecido simplesmente para de funcionar (403) em vez de falhar
silenciosamente, o que pelo menos facilita achar o que faltou.

### 1.3 `segredo_justica` (sigilo do processo) é só um rótulo visual — não restringe quem vê o processo

Testei isso na prática: criei um processo marcado como `segredo_justica`,
criei um usuário `funcionario` de uma unidade sem nenhuma relação com
aquele processo, logei como esse usuário e abri a tela do processo
diretamente pela URL — **HTTP 200, acesso liberado**. O campo hoje só é
consumido em um lugar: a rotina de captura de movimentações usa ele para
decidir se pula o monitoramento automático daquele processo. Ele não tem
nenhum efeito sobre quem pode visualizar, editar ou exportar o processo.

Isso importa especialmente para um escritório de grande porte porque
esse é exatamente o cenário em que "segredo de justiça" e "muralha ética"
(ethical wall) entre equipes/sócios deixam de ser um detalhe e passam a
ser operacionalmente necessários — casos sensíveis, conflitos de
interesse entre clientes do mesmo escritório, e informações que só um
subconjunto específico de advogados deveria enxergar. Hoje o controle de
acesso do sistema para em nível de unidade (filial); não existe nenhum
mecanismo de lista de acesso por processo individual.

**Correção recomendada:** um modelo simples de "acesso restrito por
processo" (uma tabela associando `Processo` a uma lista de usuários
autorizados, além do dono/responsável), consultado no mesmo ponto onde
hoje se verifica acesso por unidade — quando `segredo_justica=True` (ou
um novo campo `acesso_restrito`, mais explícito), só quem está na lista
(mais admins da empresa) consegue abrir o processo.

---

## 2. Alto impacto — o que trava a operação em escala de um escritório grande

Estes não são falhas de segurança ativa, mas são lacunas que um
escritório pequeno pode não sentir e um escritório grande sente todo dia.

### 2.1 Zero verificação de conflito de interesses

Não existe, em nenhum ponto do cadastro de cliente ou de processo,
nenhuma checagem cruzando partes envolvidas (autor, réu, terceiros) contra
a base de clientes/processos já existente para alertar sobre possível
conflito. Para um escritório pequeno isso é gerenciável de memória; para
um escritório de grande porte, com centenas ou milhares de processos e
múltiplos sócios, é exatamente o tipo de controle que deveria ser
automático — inclusive por exigência de compliance/OAB em determinados
contextos. Um primeiro passo realista (sem tentar resolver o problema
inteiro de uma vez) seria: ao cadastrar uma parte adversa em um processo
novo, buscar automaticamente se esse nome/CPF/CNPJ já aparece como
cliente ou parte em outro processo da mesma empresa, e mostrar um aviso
não-bloqueante para revisão manual.

### 2.2 Nenhuma ferramenta de conformidade com a LGPD

Não existe: exportação de dados de um titular (cliente) mediante
solicitação, anonimização/eliminação de dados (direito ao esquecimento),
registro de consentimento, um equivalente a ROPA (registro de operações
de tratamento) nem um fluxo de notificação de incidente. Para um
escritório de grande porte — que provavelmente processa dados pessoais
sensíveis de milhares de titulares (clientes e partes de processos) — a
ausência completa dessas ferramentas é um risco de compliance real, não
hipotético, especialmente combinado com o achado 1.1 (vazamento entre
tenants) e a ausência de qualquer registro formal de quem acessou o quê
além do log de autenticação básico que já existe.

Recomendo pelo menos três coisas num primeiro momento: uma tela de
exportação dos dados de um cliente em formato legível (JSON ou PDF), uma
rotina de anonimização (substituir nome/CPF/contato por um placeholder
mantendo o histórico jurídico intacto, já que o processo em si não pode
simplesmente sumir) e um registro simples de bases legais/consentimento
por cliente.

### 2.3 Nenhuma fila de processamento em segundo plano (Celery/RQ/Huey ou equivalente)

Toda geração de IA e qualquer operação potencialmente longa roda de forma
síncrona, dentro do próprio ciclo de requisição do gunicorn. Isso já
causou um incidente documentado em produção (`WORKER TIMEOUT`, ver
`PENDENCIAS.md` seção -17) e, na configuração atual (2 workers do
gunicorn, RAM limitada pelo modelo de IA local rodando no mesmo
processo), na prática limita o sistema a **uma geração de IA por vez em
todo o sistema**, com qualquer usuário de qualquer empresa esperando na
fila atrás dele. Para um escritório de grande porte com múltiplos
advogados usando o agente de IA ao mesmo tempo, isso é um gargalo direto
de produtividade, não um detalhe técnico.

**Correção recomendada:** introduzir uma fila (RQ é o mais simples de
operar em cima do que já existe, sem exigir um broker pesado como
RabbitMQ — só precisa de Redis, que é leve de rodar no mesmo EasyPanel) e
mover para lá qualquer geração de IA e qualquer rotina de captura em
lote, devolvendo a resposta imediatamente com um status "processando" e
atualizando a tela quando terminar (polling simples já resolve, não
precisa de WebSocket).

### 2.4 Timesheet e faturamento estão desconectados — não existe pipeline de horas para fatura

Verifiquei diretamente na estrutura do banco (reflexão dos modelos em
tempo de execução) que não existe nenhuma chave estrangeira ligando
`Apontamento` (timesheet) a `Lancamento` (financeiro), em nenhuma
direção. Isso significa que hoje um advogado pode registrar horas
trabalhadas, mas não existe nenhum caminho no sistema que transforme essa
hora registrada em um item de uma cobrança real — é um trabalho manual
por fora do sistema. Também não existe nenhuma segregação de conta de
terceiros (valores que pertencem ao cliente, não ao escritório, como
depósitos judiciais ou valores recebidos em nome do cliente) — para um
escritório de grande porte, com volume de valores de terceiros
transitando, essa segregação contábil costuma ser exigida por norma da
OAB/compliance, não só uma boa prática.

**Correção recomendada, em duas partes independentes:** (a) adicionar um
vínculo opcional de `Apontamento` a um `Lancamento`/fatura, com uma tela
de "gerar cobrança a partir das horas do período" que soma apontamentos
não faturados de um cliente/processo; (b) uma conta de terceiros separada
(mesmo que seja só um novo tipo de lançamento com um saldo isolado do
caixa do escritório), sem misturar com o financeiro operacional normal.

### 2.5 Persistência de documentos enviados não está confirmada como durável

Não há diretiva `VOLUME` no `Dockerfile` nem arquivo `docker-compose`
versionado. Isso não é necessariamente um problema — pode ser que você já
tenha configurado um volume manualmente no painel do EasyPanel, fora do
repositório, o que eu não tenho como verificar por aqui. Mas, se não
houver, qualquer redeploy do container pode apagar os documentos de
processo já enviados por clientes/advogados, sem aviso. Dado o risco (é
literalmente a evidência documental do escritório), vale confirmar
diretamente no painel do EasyPanel se existe um volume persistente
apontando para a pasta de uploads, e não assumir que sim.

---

## 3. Médio impacto — recursos que diferenciam um escritório de grande porte, mas não bloqueiam a operação hoje

Vale lembrar que várias funcionalidades típicas de escritório grande já
foram implementadas em rodadas anteriores desta conversa e continuam de
pé, confirmadas nesta auditoria: agenda integrada de prazos/audiências/
tarefas, BI com taxa de sucesso e tempo médio de processo, produtividade
por advogado com timesheet, contingenciamento jurídico formal (motor de
prazos com máquina de estados e exigência de evidência para baixa) e os
agentes de IA de operação/gestão/negócios. Os itens abaixo são o que
ainda falta além disso.

- **Alçada/aprovação em múltiplos níveis:** hoje não existe um fluxo de
  aprovação (por exemplo, lançamento financeiro acima de um valor
  precisar de aprovação de um sócio) — comum em escritórios grandes com
  estrutura societária mais complexa.
- **Relatórios por centro de custo/área de prática:** o BI existente
  mede desfecho e tempo médio, mas não segmenta por área do direito
  (trabalhista, cível, tributário etc.) nem por unidade de negócio —
  útil para escritórios organizados em departamentos.
- **Auditoria de acesso a documentos sensíveis:** existe log de ações
  administrativas (`registrar_log`), mas não um log específico de
  "quem abriu/baixou qual documento de qual processo", que costuma ser
  exigido em auditorias de compliance de escritórios grandes.
- **Onboarding/desligamento de usuário formal:** não existe um checklist
  ou fluxo estruturado para desativar acessos de um advogado que sai do
  escritório (hoje depende de lembrar de marcar `ativo=False`
  manualmente).
- **Exportação/portabilidade de dados em massa:** não há uma rotina de
  exportação completa de todos os processos/documentos de um cliente
  específico de uma vez (relevante tanto para LGPD quanto para quando um
  cliente encerra o contrato e pede os autos de volta).

---

## 4. Como eu sugiro sequenciar isso

Não tentei transformar isso em um cronograma com datas — isso depende de
quanto tempo você quer dedicar e de quanto risco está disposto a
carregar enquanto isso. Mas a ordem de prioridade que uso para organizar
esta lista é a ordem que eu recomendaria seguir: primeiro os três itens
da seção 1 (são risco de segurança já existente, não "melhoria futura"),
depois a fila de processamento em segundo plano e o vínculo
timesheet→faturamento da seção 2 (são os que mais imediatamente travam
uso em volume), depois LGPD e conflito de interesses (mais trabalho de
modelagem, mas sem eles rodando um escritório grande de verdade fica
exposto), e só depois os itens da seção 3.

Se quiser, posso pegar qualquer um destes itens — inclusive mais de um ao
mesmo tempo — e implementar do mesmo jeito que fiz com o sistema de
módulos e a gestão de preços: desenhando a solução, testando no meu
ambiente local antes de qualquer entrega, e te avisando claramente do que
mudou.
