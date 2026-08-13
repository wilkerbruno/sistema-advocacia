# JusControl — Sistema de Gestão para Escritório de Advocacia

Sistema completo, do zero, para gestão de um escritório de advocacia com
**múltiplas unidades (filiais)**. Cada funcionário só vê e opera os dados da
sua própria unidade; o usuário **administrador** enxerga e acompanha tudo,
de todas as unidades, em um painel consolidado.

## O que o sistema resolve

Um escritório de advocacia precisa, no mínimo, de:

1. **Controle de acesso por unidade** — cada filial trabalha isolada, sem ver dados de outra.
2. **Gestão de processos** — cadastro completo (número CNJ, área, fase, comarca, vara, valor da causa, parte contrária).
3. **Linha do tempo do processo** — andamentos/movimentações registrados cronologicamente.
4. **Controle de prazos** — com prioridade, responsável e alerta de vencimento (evita perda de prazo, o maior risco de um escritório).
5. **Agenda de audiências** — presenciais, virtuais ou híbridas, com status.
6. **Gestão documental** — upload de petições, procurações, contratos e decisões vinculados ao processo.
7. **Cadastro de clientes** — pessoa física ou jurídica, com histórico de processos.
8. **Financeiro** — honorários, custas e despesas, com status de pagamento e visão de inadimplência.
9. **Tarefas internas** — atribuição de trabalho à equipe, com prioridade e prazo.
10. **Auditoria** — toda ação relevante (criação, edição, exclusão, login) fica registrada com usuário, IP e data.
11. **Notificações internas** — a pessoa responsável é avisada quando um prazo ou tarefa é atribuído a ela.
12. **Relatórios consolidados** — comparativo entre unidades, exclusivo do administrador.

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

## Como rodar localmente

```bash
cd sistema-advocacia
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edite o .env com as credenciais do seu MySQL

# crie o banco vazio antes (uma vez só):
mysql -u root -p -e "CREATE DATABASE sistema_advocacia CHARACTER SET utf8mb4;"

# cria as tabelas e popula dados de exemplo (2 unidades, 4 usuários, processos de teste)
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
