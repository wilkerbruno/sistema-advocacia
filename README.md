# JusControl — Sistema de Gestão para Escritório de Advocacia

Sistema completo, do zero, para gestão de um escritório de advocacia com
**múltiplas unidades (filiais)**. Cada funcionário só vê e opera os dados da
sua própria unidade; o usuário **administrador** enxerga e acompanha tudo,
de todas as unidades, em um painel consolidado.

## O que o sistema resolve

Este projeto nasceu como um sistema de gestão de escritório multiunidade e
foi estendido para cobrir o **briefing de governança de carteira
processual** (captura automática, motor de prazos, máquina de estados,
jurimetria, cofre de senha de processo). Resumo do que cada parte resolve:

1. **Controle de acesso por unidade** — cada filial trabalha isolada, sem ver dados de outra.
2. **Gestão de processos** — cadastro completo (número CNJ, área, fase, comarca, vara, valor da causa, parte contrária, classe/assunto CNJ).
3. **Captura e rastreabilidade** — `Movimentacao` e `Publicacao` guardam o dado bruto vindo da fonte oficial (tribunal/provedor), com hash de deduplicação e origem, separado do `Andamento` (nota manual da equipe).
4. **Máquina de estados** — `MapaEstadoTPU` traduz código processual (TPU/CNJ) em estado de negócio editável por tela, e `HistoricoEstadoProcesso` registra cada transição com data (mede tempo por fase).
5. **Motor de próxima ação** — `RegraProximaAcao` é uma tabela de regras (ato → ação exigida → prazo base → responsável sugerido), nunca hardcoded no código.
6. **Motor de prazos com evidência de cumprimento** — `Prazo` agora tem `data_inicial`, `calculo_automatico`, `data_original_calculada` (auditoria de qualquer alteração manual da data fatal) e só fecha como `cumprido` quando há `evidencia_movimentacao_id` ou `evidencia_documento_id` — nunca só o clique de "concluído".
7. **Camada de jurimetria** — `Decisao` guarda tipo, órgão julgador, relator, resultado e tese de cada decisão capturada.
8. **Cofre de senha de processo** — `SenhaProcesso` grava a senha do processo sempre criptografada (nunca texto puro), com log de quem cadastrou e do último acesso.
9. **Observabilidade da captura automática** — `LogCaptura` registra cada execução das rotinas de coleta (fonte, status, duração), base para alertar quando uma captura falha.
10. **Calendário forense** — `Feriado` (nacional, por tribunal, ou período como o recesso forense) alimenta o cálculo de prazos em dias úteis.
11. **Governança/auditoria** — `LogAtividade` já existia; `Movimentacao` e `Prazo` agora têm `deletado_em` (soft delete) — nenhum desses registros é apagado fisicamente, conforme exigido no briefing.
12. **Cadastro de clientes, financeiro (honorários/custas/despesas) e tarefas internas** — como antes.


## Modelo de permissões (RBAC)

| Papel          | Escopo de dados         | Pode fazer |
|----------------|--------------------------|------------|
| **admin**      | Todas as unidades        | Vê e gerencia tudo: unidades, usuários, processos, financeiro e relatórios consolidados de todas as filiais. |
| **gestor**     | Somente a própria unidade| Gerencia a equipe e os dados da sua unidade. |
| **advogado**   | Somente a própria unidade| Cria e edita processos, clientes, prazos, audiências e documentos da sua unidade. |
| **funcionario**| Somente a própria unidade| Uso operacional (secretaria/estagiário) restrito à sua unidade. |

A regra de escopo está centralizada em `app/utils/acesso.py`
(`aplicar_escopo_unidade`, `checar_acesso_unidade_ou_403`), então toda tela
nova só precisa reutilizar essas funções — o isolamento entre unidades nunca
fica "solto" espalhado pelas rotas.

## Arquitetura

```
sistema-advocacia/
├── app/
│   ├── models/          # Unidade, Usuario, Cliente, Processo (+ Andamento, Prazo,
│   │                       Audiencia, Documento), Lancamento, Tarefa, LogAtividade, Notificacao
│   ├── routes/           # blueprints: auth, dashboard, clientes, processos,
│   │                       financeiro, tarefas, admin, api
│   ├── utils/             # acesso.py (RBAC + escopo por unidade), notificacoes.py (log + avisos)
│   ├── templates/         # Jinja2 + Bootstrap 5, identidade visual própria
│   └── static/css/estilo.css
├── config.py              # variáveis de ambiente / configuração do MySQL
├── run.py                 # ponto de entrada (Flask dev server)
├── seed.py                # cria as tabelas e popula dados de exemplo
├── requirements.txt
├── Dockerfile              # pronto para deploy no EasyPanel
└── .env.example
```

**Stack**: Python + Flask + SQLAlchemy + MySQL (PyMySQL) + Flask-Login,
seguindo o mesmo padrão dos seus outros sistemas (Smart Condominium,
Nosso Lar Finanças). Frontend em Jinja2 + Bootstrap 5 com CSS próprio
(paleta tinta/papel/latão, tipografia serifada para títulos) — sem build
step de frontend, fácil de manter e hospedar.

## Conectando ao banco MySQL e criando as tabelas

O `.env` já está configurado com a string de conexão que você passou:

```
DATABASE_URL=mysql://mysql:v4a79u40ldb5tf923lnt@easypanel.pontocomdesconto.com.br:3333/JusControl
```

`config.py` aceita tanto esse formato (`mysql://...`, o que o EasyPanel
mostra na tela) quanto `mysql+pymysql://...` (o que o SQLAlchemy exige) —
a conversão é automática.

Para criar todas as tabelas nesse banco:

```bash
pip install -r requirements.txt
python criar_tabelas.py
```

O script:
- testa a conexão antes de tentar qualquer coisa;
- cria o banco `JusControl` se ainda não existir;
- cria as 21 tabelas do modelo de dados (`db.create_all()` — seguro rodar
  mais de uma vez, não apaga nem recria tabelas já existentes);
- popula duas tabelas de referência exigidas pelo motor de governança:
  o mapa código-TPU → estado de negócio e as regras de próxima ação
  (ato capturado → ação exigida → prazo → responsável sugerido).

Também é possível apontar para outro banco sem tocar no `.env`:

```bash
python criar_tabelas.py --database-url "mysql://usuario:senha@host:porta/banco"
```

> **Importante**: testei a conexão a partir deste ambiente de geração de
> código e ela não é alcançável daqui — este sandbox só tem acesso a uma
> lista restrita de domínios (PyPI, GitHub, npm etc.) e
> `easypanel.pontocomdesconto.com.br` não faz parte dela; a tentativa deu
> timeout de rede, não erro de credencial ou de schema. A lógica de
> parsing da URL e de criação das tabelas foi validada localmente com
> SQLite (schema idêntico, 21 tabelas, sem erros). Rode `python
> criar_tabelas.py` a partir da sua máquina, de um terminal do próprio
> EasyPanel, ou de um servidor que tenha rede até esse host. Se aparecer
> erro, me mande a mensagem que eu ajusto.

## Como rodar localmente (com um banco MySQL de teste próprio)

```bash
cd sistema-advocacia
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edite o .env com as credenciais do seu MySQL

# crie as tabelas (equivalente ao passo acima, mas em outro banco):
python criar_tabelas.py

# cria dados de exemplo (2 unidades, 4 usuários, processos de teste)
python seed.py

python run.py
```

Acesse `http://localhost:5000`. Logins criados pelo seed:

| Papel     | E-mail                          | Senha    | Unidade |
|-----------|----------------------------------|----------|---------|
| admin     | admin@escritorio.com.br          | admin123 | todas   |
| gestor    | gestora.sp@escritorio.com.br     | 123456   | SP-01   |
| advogado  | advogado.sp@escritorio.com.br    | 123456   | SP-01   |
| advogado  | advogada.rj@escritorio.com.br    | 123456   | RJ-01   |

**Troque essas senhas antes de qualquer uso real.**

## Deploy no EasyPanel

1. Suba o repositório para o GitHub.
2. No EasyPanel, crie um serviço MySQL e um app a partir do `Dockerfile`.
3. Configure as variáveis de ambiente do `.env.example` no serviço do app
   (apontando `DB_HOST` etc. para o serviço MySQL do EasyPanel).
4. Rode `python seed.py` uma vez (via terminal do container) para criar as tabelas.
5. Configure um volume persistente apontando para `/app/uploads`, para os
   documentos dos processos não se perderem em um redeploy.

## Próximos passos sugeridos

- Integração com Evolution API para enviar lembretes de prazo/audiência por WhatsApp.
- Emissão de recibo/nota de honorários em PDF a partir do módulo financeiro.
- Consulta automática de movimentações via DataJud/tribunais (webscraping ou API pública do CNJ).
- App mobile (ou PWA) consumindo os mesmos endpoints, no padrão do que você já fez no Smart Condominium.
- Fila de notificação por e-mail além da notificação interna já implementada.

## Aderência ao briefing de governança de carteira processual

Atualização (13/08/2026): a maior parte do que dava para implementar sem
depender de terceiros foi feita nesta rodada. Veja `PENDENCIAS.md` para o
detalhamento item a item do que está pronto, o que está bloqueado e por quê.

Resumo rápido do que passou a funcionar de verdade (não só schema):
- Cadastro de processo por número CNJ, com validação real do dígito
  verificador (módulo 97) — `app/utils/cnj.py`.
- Motor de prazos calculando data fatal em dias úteis, lendo a tabela de
  feriados/recesso forense — `app/utils/prazos_engine.py`.
- Fechamento de prazo como "cumprido" agora **exige evidência** (movimentação
  capturada ou documento anexado) — não é mais possível fechar só no clique.
- Máquina de estados traduzindo automaticamente o código TPU da movimentação
  para o estado de negócio, com fila de triagem para código não mapeado —
  `app/utils/estado_processual_engine.py`.
- Motor de próxima ação criando prazo automaticamente a partir de uma
  movimentação (por código TPU ou por texto do ato).
- Cofre de senha de processo com criptografia Fernet real (cifra/decifra) —
  `app/utils/cofre.py`.
- Fila de intimações, painel de governança (prazos por janela, processos
  parados, exposição financeira, movimentações críticas, não monitoráveis) e
  tela de métricas de governança — `app/routes/governanca.py`.
- Auditoria com filtro por usuário e período.
- Export CSV autenticado (processos/movimentações/decisões/prazos) para uso
  em Data Lake externo.
- Preview do relatório semanal (o envio automático continua bloqueado — ver `PENDENCIAS.md`).

**Bloqueado nesta rodada** (não é falta de código, é decisão/credencial que
só você resolve): captura automática real (Judit/Escavador/Digesto/Codilo),
envio de e-mail/WhatsApp, integração com o For Legal / Data Lake do
escritório, Google OAuth, e a divergência de arquitetura entre o "sistema
interno multiunidade" que existe e o "observatório single-user read-only"
descrito na fase 1 do briefing. Detalhes e o porquê de cada um em `PENDENCIAS.md`.

**Importante sobre teste:** este ambiente de geração de código não tem
acesso de rede ao MySQL do EasyPanel (só alcança PyPI/GitHub/npm), então a
validação foi feita com checagem de sintaxe (`py_compile`) e inicialização
do app sem conectar a nenhum banco. Nenhuma tabela nova foi criada — o
schema já existente sustenta tudo o que foi implementado, então
`python criar_tabelas.py` não precisa rodar de novo por causa disso. Mas
vale testar o fluxo completo (cadastrar por CNJ, registrar movimentação,
fechar prazo com evidência, cofre de senha) no seu ambiente antes de usar
em produção — não ficou testado contra o banco real.
