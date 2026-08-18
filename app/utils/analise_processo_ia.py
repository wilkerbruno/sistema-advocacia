"""
Análise de UM processo específico via Agente de IA (resumo dos autos ou
rascunho inicial de petição) — ver app/models/agente_ia.py::AnaliseProcessoIA
e a rota app/routes/processos.py::analise_ia.

Usa o mesmo roteador de provedor do Agente de IA de portfólio (ver
app/utils/agente_ia_router.py — modelo local gratuito por padrão, ou a API
do Claude com chave própria da empresa se ela tiver escolhido isso em
"Minhas Integrações") — mesmas limitações e mesma regra de nunca inventar
fato fora do que foi injetado no contexto. `montar_digest_processo` monta
esse contexto real a partir dos dados do processo no banco (nunca do
próprio modelo "lembrando" nada).

⚠️ Quando o provedor é o modelo local, ele roda numa janela de contexto
pequena (ver IA_LOCAL_CONTEXT_SIZE, padrão 4096 tokens) — por isso o
digest é cortado a um orçamento de caracteres (`LIMITE_PADRAO_CHARS`);
processos com histórico muito longo têm as movimentações/decisões mais
antigas omitidas, e isso é sinalizado ao usuário (`digest_truncado`) em
vez de escondido. Empresas usando a API do Claude (BYOK) têm uma janela de
contexto bem maior — o mesmo corte se aplica hoje por simplicidade, mas dá
pra revisitar se isso incomodar na prática.
"""
from app.utils import agente_ia_router

LIMITE_PADRAO_ITENS = 20  # nº máx. de andamentos/movimentações/decisões cada, mais recentes primeiro
# Orçamento aproximado de caracteres do digest — calibrado para caber com
# folga dentro de IA_LOCAL_CONTEXT_SIZE=4096 (config.py, padrão do modelo
# "pequeno") junto com o prompt estruturado (esqueleto de petição) e a
# resposta. Se um dia ativar o modelo "grande" com IA_LOCAL_CONTEXT_SIZE
# maior (ver PENDENCIAS.md, seção -6), pode valer a pena subir este valor.
LIMITE_PADRAO_CHARS = 6000


def _agrupar_movimentacoes_repetidas(movs):
    """
    Agrupa sequências de movimentações CONSECUTIVAS com o mesmo texto (ex.:
    vários "Ato ordinatório" seguidos, comum em processos antigos com muito
    trâmite burocrático repetitivo — ver pendência nº -14 do PENDENCIAS.md)
    numa única linha ("Ato ordinatório — 5 ocorrências entre X e Y") em vez
    de uma linha idêntica repetida várias vezes.

    Isso ajuda em duas frentes: economiza espaço no orçamento de caracteres
    do digest (mais itens de verdade cabem), e reduz o "efeito gatilho" de
    repetição no modelo local — texto de entrada já repetitivo aumenta a
    chance dele entrar num loop copiando a mesma frase até estourar o limite
    de tokens em vez de gerar conteúdo novo (ver `repeat_penalty` em
    app/utils/ia_local.py, que ataca o mesmo problema por outro ângulo).

    `movs` já vem ordenado mais recente primeiro (ver chamada abaixo) —
    mantém essa ordem, só colapsa repetições ADJACENTES (não reordena nem
    agrupa ocorrências que não são seguidas uma da outra, pra não perder o
    "esse ato aconteceu de novo bem depois" como sinal).
    """
    linhas = []
    i = 0
    while i < len(movs):
        texto = movs[i].texto_integral
        grupo = [movs[i]]
        j = i + 1
        while j < len(movs) and movs[j].texto_integral == texto:
            grupo.append(movs[j])
            j += 1

        if len(grupo) == 1:
            linhas.append(f"- {movs[i].data.strftime('%d/%m/%Y')}: {texto}")
        else:
            data_mais_recente = grupo[0].data.strftime("%d/%m/%Y")
            data_mais_antiga = grupo[-1].data.strftime("%d/%m/%Y")
            linhas.append(f"- {texto} — {len(grupo)} ocorrências entre {data_mais_antiga} e {data_mais_recente}")
        i = j
    return linhas


RESUMO_SYSTEM = (
    "Você é o assistente de operação jurídica interno de um escritório de advocacia. "
    "Sua tarefa agora é ler os dados reais de UM processo específico (fornecidos abaixo, extraídos "
    "diretamente do sistema do escritório) e produzir um resumo objetivo dos autos. Responda em "
    "português do Brasil, de forma direta, e estruture a resposta EXATAMENTE com estas seções, "
    "nesta ordem (pule uma seção só se não houver nenhuma informação para ela, mas mantenha o título "
    "e escreva '—'):\n\n"
    "SITUAÇÃO ATUAL\n"
    "ÚLTIMOS ATOS RELEVANTES\n"
    "PRAZOS PENDENTES\n"
    "PONTOS DE ATENÇÃO\n\n"
    "Regra de formato muito importante: CADA informação do contexto só pode aparecer em UMA seção, "
    "a mais apropriada — nunca repita a mesma lista ou os mesmos itens em duas seções diferentes. Em "
    "especial, a seção SITUAÇÃO ATUAL deve ser um parágrafo curto (2 a 4 frases corridas, sem listas e "
    "sem repetir a lista de prazos) descrevendo em que fase o processo está agora; a lista completa de "
    "prazos pendentes (com datas) vai APENAS na seção PRAZOS PENDENTES — se quiser mencionar um prazo em "
    "SITUAÇÃO ATUAL, cite no máximo o mais próximo, nunca a lista inteira de novo. IMPORTANTE: PRAZOS "
    "PENDENTES só pode conter itens que vieram do bloco 'Prazos ainda em aberto' do contexto — nunca "
    "coloque ali um item do bloco 'Histórico de movimentações', mesmo que ele tenha data; movimentação "
    "não é a mesma coisa que prazo, e listar uma como se fosse a outra é um erro de informação, não só "
    "de formatação. Seja conciso: cada "
    "seção deve ter poucas linhas, não parágrafos longos — o objetivo é leitura rápida, não um relatório "
    "completo.\n\n"
    "Use APENAS as informações fornecidas no contexto abaixo — nunca invente fato, data, valor, lei ou "
    "movimentação que não esteja ali. Se um dado relevante não estiver disponível no contexto, escreva "
    "isso explicitamente na seção correspondente em vez de supor ou pular em silêncio. Você não é "
    "advogado, e este resumo é um apoio para leitura rápida — não substitui a análise do processo pelo "
    "advogado responsável antes de qualquer decisão."
)

RASCUNHO_SYSTEM = (
    "Você é o assistente de operação jurídica interno de um escritório de advocacia, ajudando um "
    "advogado a preparar um RASCUNHO INICIAL de peça processual — nunca um texto pronto para "
    "protocolar. Use os dados reais do processo fornecidos abaixo como base factual, e o pedido do "
    "advogado (também abaixo) para decidir o tipo de peça e a linha argumentativa. Responda em "
    "português do Brasil.\n\n"
    "Estruture a peça EXATAMENTE nesta ordem, adaptando os títulos internos apenas quando o tipo de "
    "peça pedida exigir (ex.: petição inicial não tem seção de resposta a fatos alheios):\n\n"
    "1. Endereçamento: 'EXCELENTÍSSIMO(A) SENHOR(A) DOUTOR(A) JUIZ(A) DE DIREITO DA [vara/tribunal, "
    "conforme os dados abaixo — se não houver, escreva [REVISAR: vara/tribunal]]'\n"
    "2. Um parágrafo de abertura identificando as partes (usando os dados fornecidos; o que faltar, "
    "marque como [REVISAR: ...]) e o número do processo, e nomeando o TIPO DE PEÇA em maiúsculas.\n"
    "3. 'I — DOS FATOS': narrativa objetiva dos fatos, baseada só no que está nos dados do processo.\n"
    "4. 'II — DO DIREITO': fundamentação jurídica. Toda citação de lei, artigo, súmula ou precedente "
    "que você não tiver certeza absoluta de que está correta deve vir como "
    "'[REVISAR: confirmar fundamento — <do que trata>]' em vez de um número ou nome inventado.\n"
    "5. 'III — DOS PEDIDOS': lista objetiva do que está sendo requerido ao juízo.\n"
    "6. Fecho padrão ('Termos em que, pede deferimento.', local e data como [REVISAR: data], e "
    "'[REVISAR: nome do(a) advogado(a) e número da OAB]').\n"
    "7. Ao final, OBRIGATORIAMENTE uma seção separada por uma linha '---' com o título "
    "'PONTOS QUE PRECISAM DE REVISÃO HUMANA ANTES DE PROTOCOLAR', listando em poucas linhas cada "
    "'[REVISAR: ...]' usado acima, para o advogado bater o olho rápido sem precisar reler tudo.\n\n"
    "Regra mais importante de todas: nunca invente citação, número de lei, jurisprudência, data ou "
    "fato que não esteja nos dados fornecidos — marcar como [REVISAR: ...] é sempre melhor do que "
    "parecer completo e estar errado. Este é um rascunho gerado por um modelo de IA local, de porte "
    "pequeno: o advogado responsável DEVE revisar, corrigir e validar juridicamente cada trecho antes "
    "de usar, protocolar ou enviar a qualquer parte."
)


def montar_digest_processo(processo, limite_itens=LIMITE_PADRAO_ITENS, limite_chars=LIMITE_PADRAO_CHARS):
    """Monta o texto de contexto real do processo injetado no prompt. Devolve
    (texto, truncado) — truncado=True quando o histórico teve que ser cortado."""
    partes = [
        f"Processo {processo.numero_processo or processo.numero_interno or ('#' + str(processo.id))} — "
        f"área: {processo.area_direito}, classe: {processo.classe_processual or '—'}, "
        f"assunto: {processo.assunto_cnj or '—'}.",
        f"Tribunal/vara: {processo.tribunal or '—'} / {processo.vara or '—'}. Fase: {processo.fase or '—'}. "
        f"Estado atual: {processo.estado_negocio_atual or '—'}.",
        f"Cliente: {processo.cliente.nome if processo.cliente else '—'} (polo: {processo.polo_cliente or '—'}). "
        f"Parte contrária: {processo.parte_contraria or '—'}.",
    ]
    if processo.valor_causa:
        partes.append(f"Valor da causa: R$ {processo.valor_causa}.")

    prazos_pendentes = [p for p in processo.prazos if p.status != "cumprido" and not p.deletado_em]
    if prazos_pendentes:
        linhas = [f"- {p.descricao} (vence {p.data_vencimento.strftime('%d/%m/%Y')}, status: {p.status})"
                  for p in prazos_pendentes[:10]]
        # Nome deliberadamente diferente do título da seção "PRAZOS PENDENTES"
        # pedida no system prompt (RESUMO_SYSTEM) — o modelo local (pequeno)
        # tende a "copiar" de volta um bloco do contexto quando o rótulo bate
        # com o título de seção pedido, duplicando a lista em duas seções.
        partes.append("Prazos ainda em aberto no cadastro (usar só na seção PRAZOS PENDENTES da "
                       "resposta, não repetir em nenhuma outra seção):\n" + "\n".join(linhas))

    movs = [m for m in processo.movimentacoes if not m.deletado_em][:limite_itens]
    if movs:
        linhas = _agrupar_movimentacoes_repetidas(movs)
        # Nome do rótulo evita colidir com o título de seção "ÚLTIMOS ATOS
        # RELEVANTES" pedido no prompt (mesmo motivo do rótulo de prazos
        # acima) — e explica de onde cada seção da resposta deve vir, pra
        # não misturar item de movimentação com item de prazo na resposta
        # (já aconteceu: o modelo listou movimentações como se fossem
        # prazos pendentes, com data e tudo, dentro da seção errada).
        partes.append("Histórico de movimentações capturadas, mais recente primeiro (usar só na seção "
                       "ÚLTIMOS ATOS RELEVANTES da resposta — isto aqui NÃO são prazos, mesmo tendo "
                       "data; não colocar nenhum destes itens na seção PRAZOS PENDENTES):\n"
                       + "\n".join(linhas))

    decisoes = list(processo.decisoes)[:limite_itens]
    if decisoes:
        linhas = [f"- {d.data.strftime('%d/%m/%Y') if d.data else '—'} ({d.tipo or 'decisão'}): "
                  f"{(d.tese or d.inteiro_teor or '(sem teor registrado)')[:400]}" for d in decisoes]
        partes.append("Decisões:\n" + "\n".join(linhas))

    andamentos = list(processo.andamentos)[:limite_itens]
    if andamentos:
        linhas = [f"- {a.data.strftime('%d/%m/%Y')} ({a.tipo}): {a.descricao}" for a in andamentos]
        partes.append("Andamentos registrados pela equipe:\n" + "\n".join(linhas))

    texto = "\n\n".join(partes)
    truncado = False
    if len(texto) > limite_chars:
        texto = texto[:limite_chars]
        truncado = True
        texto += ("\n\n[...histórico truncado por limite de tamanho do contexto do modelo local — parte "
                  "das movimentações/decisões/andamentos mais antigos foi omitida...]")

    return texto, truncado


def gerar_analise(processo, tipo, instrucao=None):
    """
    Gera o resumo ou rascunho de petição para `processo`. Levanta ValueError
    para erro de uso (tipo inválido, instrução obrigatória faltando) e deixa
    propagar agente_ia_router.ProvedorIAIndisponivelError quando o provedor
    de IA configurado para a empresa do processo (modelo local ou Claude
    BYOK) não está pronto — quem chama decide como exibir isso.

    Devolve (resultado_texto, digest_truncado).
    """
    if tipo not in ("resumo", "rascunho_peticao"):
        raise ValueError(f"Tipo de análise desconhecido: {tipo}")

    digest, truncado = montar_digest_processo(processo)

    if tipo == "resumo":
        system = RESUMO_SYSTEM + "\n\nDados do processo:\n" + digest
        pedido = instrucao.strip() if instrucao and instrucao.strip() else "Resuma a situação atual deste processo."
        # 700 tokens vinha sendo pouco pra caber as 4 seções pedidas (RESUMO_SYSTEM)
        # sem cortar no meio de frase quando o processo tem vários prazos/andamentos
        # — o digest (até LIMITE_PADRAO_CHARS=6000 chars, ~1500-2000 tokens) mais o
        # system prompt (~300 tokens) ainda deixam folga confortável dentro da janela
        # de contexto do modelo local (IA_LOCAL_CONTEXT_SIZE=4096, ver ia_local.py)
        # pra uma resposta maior.
        max_tokens = 1100

    else:
        if not instrucao or not instrucao.strip():
            raise ValueError("Descreva o que a petição precisa fazer (ex.: \"contestação alegando decadência\").")
        system = RASCUNHO_SYSTEM + "\n\nDados do processo:\n" + digest
        pedido = instrucao.strip()
        max_tokens = 1400

    empresa = processo.unidade.empresa if processo.unidade else None
    resultado = agente_ia_router.gerar_resposta(
        empresa, system, [{"role": "user", "content": pedido}], max_tokens=max_tokens
    )
    return resultado, truncado
