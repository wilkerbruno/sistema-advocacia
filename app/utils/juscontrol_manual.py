"""
Base de conhecimento do chat de suporte flutuante (pedido explícito do
usuário: "um botão flutuante com ícone de suporte com um chat aonde o
cliente pergunta algo sobre o sistema e uma IA que responde tudo sobre o
JusControl, como tudo funciona, e tudo mais").

Por que este arquivo existe em vez de reaproveitar o README.md: o README
é técnico, escrito pra outro desenvolvedor continuar o projeto (fala de
Docker, RQ, SQLAlchemy...) — não serve pra responder um advogado
perguntando "como eu cadastro um processo?". PENDENCIAS.md, pelo mesmo
motivo (e por ser um log interno de decisões, não documentação), também
não é apropriado. Este conteúdo foi escrito do zero, pensando em quem usa
o sistema no dia a dia.

Cada tópico tem `palavras_chave` (usadas por app/utils/suporte_ia.py pra
decidir quais tópicos são relevantes pra cada pergunta, por sobreposição
de palavras — busca simples, deliberadamente SEM depender de embeddings
nem de nenhuma chave de API paga, pra funcionar do mesmo jeito em
qualquer empresa cliente, com ou sem BYOK configurado) e `conteudo` (o
texto de verdade, que vai pro prompt do modelo quando o tópico é
escolhido).

Ao adicionar uma funcionalidade nova ao sistema, vale voltar aqui e somar
um tópico (ou ajustar um existente) — senão o chat de suporte vai honesta
e corretamente dizer que não sabe responder sobre ela.
"""

TOPICOS = [
    {
        "id": "visao_geral",
        "titulo": "Visão geral do JusControl",
        "palavras_chave": ["o que é", "jus control", "juscontrol", "sistema", "começar", "primeiros passos",
                            "menu", "onde fica", "como funciona", "módulos", "navegar"],
        "conteudo": (
            "O JusControl é um sistema de gestão para escritórios de advocacia: processos, prazos, "
            "clientes, financeiro, agenda e um agente de inteligência artificial, tudo num só lugar. "
            "O menu lateral tem três grupos: 'Operação' (Painel, Processos, Clientes, Rotina — que reúne "
            "Tarefas, Agenda e Timesheet —, Agente de IA e Financeiro), 'Governança de carteira' (Painel e "
            "filas, Entrada de processos, Regras e parâmetros — normalmente só administradores) e "
            "'Configurações' (Minha conta, Minha empresa e, só para quem administra a plataforma, "
            "Plataforma). O sino no topo da tela mostra avisos e notificações não lidas. Cada usuário só "
            "enxerga os dados da própria unidade (ou de todas as unidades da empresa, se for "
            "administrador) — nunca dados de outro escritório cliente do sistema."
        ),
    },
    {
        "id": "painel",
        "titulo": "Painel (tela inicial)",
        "palavras_chave": ["painel", "dashboard", "tela inicial", "resumo", "indicadores", "gráfico",
                            "prazos em atenção", "prazos perdidos"],
        "conteudo": (
            "O Painel é a tela que abre depois do login: um resumo rápido do escritório (ou da sua "
            "unidade). Mostra quantos processos ativos existem e a soma do valor de causa, quantos "
            "clientes ativos, quantos prazos estão 'em atenção' (vencendo nos próximos 5 dias) e quantos "
            "estão 'perdidos' (já venceram e ainda seguem pendentes), as próximas audiências, tarefas "
            "pendentes e dois gráficos (processos por status e por área do direito). Administradores "
            "também veem um comparativo entre as unidades da empresa."
        ),
    },
    {
        "id": "processos_cadastro",
        "titulo": "Cadastrar e gerenciar processos",
        "palavras_chave": ["processo", "cadastrar processo", "novo processo", "cnj", "número do processo",
                            "distribuição", "área do direito", "importar", "importação em lote", "csv"],
        "conteudo": (
            "Um processo pode ser cadastrado de três formas, todas em 'Entrada de processos' (menu "
            "Governança): (1) digitando o número CNJ — o sistema tenta buscar automaticamente os dados "
            "públicos do processo nos sistemas dos tribunais (e-SAJ, PJe) e no DataJud (CNJ), e mostra "
            "uma pré-visualização antes de salvar; (2) manualmente, preenchendo os campos à mão; (3) por "
            "importação em lote (arquivo CSV com vários números CNJ de uma vez). Só o cadastro via DataJud "
            "deixa o processo marcado como 'monitorável', com atualização automática periódica — os "
            "outros caminhos trazem os dados uma única vez, sem acompanhamento automático depois (o "
            "sistema sempre avisa quando um processo não está sendo monitorado automaticamente, e por "
            "quê). Cada processo tem abas para andamentos/movimentações, prazos, audiências, documentos e "
            "'Análise IA'. Também dá para gerar um PDF-resumo, um relatório estruturado (partes, pedidos, "
            "causa de pedir, decisões, incidentes pendentes) e calcular custas processuais."
        ),
    },
    {
        "id": "processos_prazos",
        "titulo": "Prazos processuais — como fecham",
        "palavras_chave": ["prazo", "prazos", "vencimento", "cumprido", "cumprir prazo", "evidência",
                            "perdido", "marcar prazo", "aguardando evidência"],
        "conteudo": (
            "Regra importante: um prazo só pode ser fechado como 'cumprido' quando há uma EVIDÊNCIA "
            "anexada — uma movimentação capturada automaticamente do tribunal, ou um documento anexado "
            "comprovando o cumprimento. O botão comum de mudar status não permite marcar 'cumprido' "
            "direto, exatamente para evitar que um prazo seja dado como feito sem prova nenhuma. O "
            "sistema pode sugerir automaticamente qual movimentação parece evidenciar o cumprimento "
            "(por palavra-chave), mas nunca fecha um prazo sozinho — sempre precisa de confirmação "
            "humana. Prazos vindos do histórico do tribunal (de antes do processo ter sido cadastrado no "
            "sistema) usam um status neutro ('histórico anterior') em vez de 'cumprido' ou 'perdido', já "
            "que o escritório não acompanhou aquele prazo em tempo real. Quando um processo é suspenso e "
            "depois reativado, todos os prazos em aberto são automaticamente empurrados pelo tanto de "
            "dias que ele ficou suspenso."
        ),
    },
    {
        "id": "processos_sigilo",
        "titulo": "Sigilo (segredo de justiça) e análise com IA",
        "palavras_chave": ["segredo de justiça", "sigiloso", "sigilo", "análise ia", "resumo dos autos",
                            "rascunho de petição", "quem pode ver", "acesso restrito"],
        "conteudo": (
            "Um processo pode ser marcado como 'segredo de justiça' — nesse caso, só quem tem acesso "
            "explícito (concedido por um administrador na edição do processo), o responsável pelo caso, "
            "quem cadastrou, ou um administrador, consegue abrir o processo; para os demais, ele "
            "simplesmente não aparece. A aba 'Análise IA', dentro de um processo, usa o Agente de IA para "
            "gerar um resumo dos autos (situação atual, a partir dos dados reais do processo) ou um "
            "rascunho de petição (você descreve o que a peça precisa fazer, e o agente monta um rascunho "
            "inicial usando os dados reais do processo). Um rascunho de petição exige antes 'delimitar o "
            "objeto' (matéria de fato, matéria de direito, tese a sustentar, resultado pretendido) — o "
            "sistema não deixa gerar uma peça sem essa base. Todo resultado de IA é sempre um RASCUNHO "
            "para revisão humana, nunca uma peça pronta para protocolar sem conferir."
        ),
    },
    {
        "id": "clientes",
        "titulo": "Clientes e Leads",
        "palavras_chave": ["cliente", "cadastrar cliente", "lead", "leads", "funil", "lgpd", "exportar dados",
                            "anonimizar", "direito ao esquecimento", "due diligence"],
        "conteudo": (
            "Em 'Clientes' você cadastra pessoas físicas ou jurídicas, edita dados, inativa (sem apagar) e, "
            "se o escritório tiver contratado o serviço, faz uma checagem de due diligence (busca dados "
            "públicos por CPF/CNPJ). Dá para exportar todos os dados de um cliente (conformidade LGPD, "
            "'portabilidade de dados') em JSON, ou um pacote completo em .zip com os documentos anexados "
            "aos processos dele. A anonimização ('direito ao esquecimento') apaga os dados pessoais do "
            "cliente mas mantém processos e lançamentos financeiros vinculados — é uma ação irreversível, "
            "só disponível para administradores. 'Leads' é um funil simples (cadastro → etapas → "
            "conversão em cliente) para controlar oportunidades antes de virarem cliente de fato."
        ),
    },
    {
        "id": "financeiro",
        "titulo": "Financeiro — lançamentos, contas a receber/pagar e conta de terceiros",
        "palavras_chave": ["financeiro", "lançamento", "receita", "despesa", "honorário", "pagamento",
                            "conta de terceiros", "comprovante", "multa", "juros", "exportar planilha",
                            "aprovação", "alçada", "recibo", "retainer", "êxito"],
        "conteudo": (
            "O módulo Financeiro tem duas abas: 'Caixa do escritório' (receita e despesa própria do "
            "escritório) e 'Conta de terceiros' (dinheiro que passa pelo escritório mas não é receita/"
            "despesa própria — ex: depósito judicial, valor a repassar a um cliente), sempre separadas "
            "para nunca inflar o resultado real do escritório. Em 'Novo lançamento' dá para escolher a "
            "natureza (receita/despesa), o modelo de cobrança (fixo, êxito — percentual sobre um "
            "valor-base — ou retainer/mensalidade recorrente), anexar um comprovante (nota fiscal, "
            "recibo), e configurar multa (valor fixo em R$ ou percentual) e juros de atraso (ao dia ou ao "
            "mês) — usados para calcular quanto está devido, atualizado, quando o lançamento vence sem "
            "ser pago. A listagem tem botão para 'Exportar planilha' (baixa um .xlsx com os dados "
            "filtrados na tela) e mostra a data de pagamento de cada lançamento. Uma despesa acima de um "
            "valor configurado pela empresa pode exigir aprovação de um ou dois níveis (alçada, definida "
            "em Minha empresa) antes de poder ser marcada como paga. Um lançamento pago gera recibo em "
            "PDF."
        ),
    },
    {
        "id": "agente_ia",
        "titulo": "Agente de IA (chat interno)",
        "palavras_chave": ["agente de ia", "agente ia", "chat ia", "inteligência artificial", "assistente",
                            "operação", "gestão", "negócios", "persona", "modelo local", "claude", "gemini"],
        "conteudo": (
            "O Agente de IA (menu Operação) tem três 'personas', cada uma pensada para um público: "
            "Operação (para advogados — resumos, prazos, organização do dia a dia), Gestão (para o "
            "controller — risco de prazo, gargalos, processos parados) e Negócios (para os sócios — "
            "leitura da carteira, receita, oportunidades; só visível a quem tem acesso financeiro "
            "liberado). Cada resposta é embasada em dados reais do escritório no escopo de quem "
            "pergunta — o agente também pode consultar, durante a própria conversa, processos, clientes, "
            "prazos, tarefas e (se você tiver acesso financeiro) números financeiros específicos, "
            "sempre respeitando as mesmas permissões de qualquer outra tela do sistema. Por padrão roda "
            "um modelo de IA local, gratuito, dentro do próprio servidor (mais simples, mas mais "
            "limitado); em 'Minha empresa > Integrações', é possível cadastrar uma chave própria da API "
            "do Claude ou do Gemini para respostas de qualidade melhor (nesse caso, a cobrança é direta "
            "do provedor escolhido para a empresa, não pelo JusControl). O agente NUNCA substitui a "
            "análise jurídica de um advogado — qualquer sugestão de mérito deve ser sempre validada por "
            "um humano antes de qualquer decisão."
        ),
    },
    {
        "id": "captacao_entrada",
        "titulo": "Entrada de processos e captação por OAB",
        "palavras_chave": ["entrada de processos", "captação", "oab", "intimação", "fila de triagem",
                            "djen", "monitoramento automático", "publicação"],
        "conteudo": (
            "Em 'Entrada de processos' (menu Governança) existem três formas de cadastrar processo: por "
            "número CNJ (com busca automática), manual, ou importação em lote por CSV. Além disso, a "
            "'Captação por OAB' permite cadastrar o(s) número(s) de OAB monitorados do escritório: o "
            "sistema busca periodicamente intimações publicadas em nome dessas OABs. Quando uma "
            "intimação chega e o número do processo dela ainda não está cadastrado (ou não bate com "
            "nenhum processo existente), ela cai na 'Fila de triagem' — lá, um humano decide: vincular a "
            "um processo já cadastrado, cadastrar um processo novo a partir daquele número, ou ignorar "
            "(sempre com um motivo registrado). Nada disso substitui a conferência humana das "
            "publicações — é um apoio para não perder nada, não uma automação sem supervisão."
        ),
    },
    {
        "id": "regras_parametros",
        "titulo": "Regras e parâmetros (governança)",
        "palavras_chave": ["regras", "parâmetros", "regras de próxima ação", "mapa de estado", "tpu",
                            "conflito de interesses", "modelos de peça", "tabela de custas", "custas"],
        "conteudo": (
            "Área restrita a administradores, com cinco abas: 'Regras de próxima ação' (define, para cada "
            "tipo de ato/movimentação capturado, qual prazo deve ser gerado automaticamente — em dias "
            "corridos ou úteis); 'Mapa de estado' (traduz o código/texto de uma movimentação do tribunal "
            "para uma fase de negócio do processo); 'Verificação de conflitos' (varre toda a carteira "
            "cruzando parte contrária com cliente, para apontar possíveis conflitos de interesse — só "
            "roda quando pedido explicitamente, por ser uma consulta mais pesada); 'Modelos de peças' "
            "(biblioteca de modelos de petição por tipo/área, usada automaticamente pelo Agente de IA "
            "quando faz sentido); e 'Tabela de custas' (regras de cálculo de custas processuais por "
            "tribunal, usadas na aba de custas de cada processo). Nenhum prazo legal vem pré-cadastrado "
            "no sistema — cabe ao escritório configurar os prazos corretos conforme a legislação e o "
            "rito aplicável; todo cálculo automático mostra a memória de cálculo, para conferência antes "
            "de qualquer providência."
        ),
    },
    {
        "id": "painel_governanca",
        "titulo": "Painel de governança e filas",
        "palavras_chave": ["painel de governança", "processos parados", "produtividade", "contingenciamento",
                            "exposição financeira", "relatório semanal", "métricas"],
        "conteudo": (
            "Reúne, em abas, uma visão de gestão da carteira inteira: prazos por janela de vencimento (7/"
            "15 dias, e já vencidos), processos parados sem movimentação há 30/60/90 dias, exposição "
            "financeira por fase e por classificação de risco, movimentações críticas das últimas 24 "
            "horas, e a lista de processos que não estão sendo monitorados automaticamente. Também tem "
            "'Produtividade da equipe' (horas apontadas, faturáveis) e 'Contingenciamento' (classificação "
            "de risco/provisionamento de cada processo). Um relatório semanal pode ser gerado em texto "
            "para envio manual, e os dados de processos/movimentações/decisões/prazos podem ser "
            "exportados em CSV para uso externo."
        ),
    },
    {
        "id": "agenda_rotina",
        "titulo": "Rotina: Tarefas, Agenda e Timesheet",
        "palavras_chave": ["rotina", "tarefa", "tarefas", "agenda", "compromisso", "audiência", "timesheet",
                            "apontamento de hora", "horas trabalhadas", "lembrete"],
        "conteudo": (
            "O menu 'Rotina' reúne três abas: Tarefas (a fazer, com prioridade e responsável), Agenda "
            "(calendário mensal que combina prazos, audiências, tarefas e compromissos livres — reunião, "
            "evento — com lembrete configurável por e-mail e, se o escritório tiver conectado um número "
            "de WhatsApp em Integrações, também por WhatsApp) e Timesheet (apontamento de horas "
            "trabalhadas, com ou sem vínculo a um processo, marcando se a hora é faturável ou não — essas "
            "horas podem depois virar uma cobrança no Financeiro, em 'Gerar cobrança a partir de horas')."
        ),
    },
    {
        "id": "minha_conta",
        "titulo": "Minha conta — preferências e autenticação em duas etapas",
        "palavras_chave": ["minha conta", "senha", "autenticador", "2fa", "duas etapas", "totp",
                            "favorito", "tutorial", "perfil"],
        "conteudo": (
            "Em 'Minha conta' (acessível por qualquer usuário) dá para trocar a senha, escolher qual grupo "
            "do menu lateral abre automaticamente por padrão (seu 'favorito'), rever o tutorial guiado de "
            "primeiro acesso, e configurar a autenticação em duas etapas (2FA/TOTP) — um código gerado "
            "por um aplicativo autenticador (Google Authenticator, Authy ou similar), somado à senha, "
            "para reforçar a segurança do login. Reconfigurar o autenticador exige digitar a senha atual "
            "de novo."
        ),
    },
    {
        "id": "minha_empresa",
        "titulo": "Minha empresa — unidades, equipe, auditoria e integrações",
        "palavras_chave": ["minha empresa", "unidade", "unidades", "equipe", "usuários", "auditoria",
                            "log", "alçada de aprovação", "licença", "integrações", "chave de api",
                            "byok", "whatsapp", "datajud"],
        "conteudo": (
            "Área de administração da empresa (admin/gestor), com abas: Unidades (filiais do escritório), "
            "Equipe (cadastro e permissões de usuários — papéis admin/gestor/advogado/funcionário, e "
            "concessão pontual de acesso financeiro para quem não é admin/gestor), Relatórios, Auditoria "
            "(log de toda atividade relevante no sistema, filtrável), Alçada de aprovação (define a "
            "partir de qual valor uma despesa precisa de aprovação de 1 ou 2 níveis antes de ser paga), "
            "Minha licença e módulos (plano contratado), e Integrações — onde a empresa pode cadastrar "
            "chave própria da API do Claude ou do Gemini (para o Agente de IA responder com um modelo "
            "maior/melhor), chave própria do DataJud, ou conectar um número de WhatsApp próprio (para "
            "lembretes de compromisso). Toda chave cadastrada fica cifrada — nunca é reexibida depois de "
            "salva, só 'cadastrada: sim/não'."
        ),
    },
    {
        "id": "seguranca_privacidade",
        "titulo": "Segurança, permissões e privacidade dos dados",
        "palavras_chave": ["segurança", "privacidade", "quem vê meus dados", "permissão", "papel",
                            "acesso", "multi-tenant", "outro escritório", "lgpd"],
        "conteudo": (
            "Cada empresa cliente do JusControl só enxerga os próprios dados — nunca de outro escritório. "
            "Dentro de uma mesma empresa, um usuário comum só vê os dados da própria unidade; um "
            "administrador da empresa vê todas as unidades DA PRÓPRIA empresa; só o administrador da "
            "empresa DONA da plataforma (suporte técnico do próprio JusControl) tem uma visão entre "
            "empresas diferentes, para fins de suporte. Acesso a dados financeiros é restrito por padrão "
            "(admin e gestor sempre têm; qualquer outro papel só com concessão explícita). Processos "
            "marcados como sigilosos (segredo de justiça) ficam ainda mais restritos. Senhas nunca ficam "
            "visíveis para ninguém (nem para o suporte), e chaves de API cadastradas em Integrações ficam "
            "cifradas no banco de dados."
        ),
    },
    {
        "id": "suporte",
        "titulo": "Como pedir ajuda / falar com o suporte de verdade",
        "palavras_chave": ["suporte", "ajuda", "contato", "falar com humano", "atendente", "bug", "erro",
                            "não sei", "não consigo", "problema"],
        "conteudo": (
            "Este chat responde dúvidas sobre como o JusControl funciona, com base neste próprio conteúdo "
            "de ajuda — ele NÃO acessa seus dados nem executa nenhuma ação no sistema (não cadastra, não "
            "edita, não apaga nada), e não é um substituto do suporte técnico humano do escritório que "
            "administra sua conta. Se a dúvida for sobre um erro específico na tela, um dado que parece "
            "errado, ou algo que não está coberto aqui, a orientação certa é procurar o administrador da "
            "sua empresa no sistema ou o suporte técnico responsável pelo seu contrato."
        ),
    },
]


def todas_palavras_chave():
    """Usado só por testes, pra checar que nenhum tópico ficou sem palavra-chave."""
    return {t["id"]: t["palavras_chave"] for t in TOPICOS}
