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
        partes.append("Prazos pendentes:\n" + "\n".join(linhas))

    movs = [m for m in processo.movimentacoes if not m.deletado_em][:limite_itens]
    if movs:
        linhas = [f"- {m.data.strftime('%d/%m/%Y')}: {m.texto_integral}" for m in movs]
        partes.append("Movimentações capturadas (mais recente primeiro):\n" + "\n".join(linhas))

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
        max_tokens = 700
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
