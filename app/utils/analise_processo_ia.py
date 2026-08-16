"""
Análise de UM processo específico via Agente de IA (resumo dos autos ou
rascunho inicial de petição) — ver app/models/agente_ia.py::AnaliseProcessoIA
e a rota app/routes/processos.py::analise_ia.

Usa o mesmo motor local gratuito do Agente de IA de portfólio (ver
app/utils/ia_local.py) — mesmas limitações e mesma regra de nunca inventar
fato fora do que foi injetado no contexto. `montar_digest_processo` monta
esse contexto real a partir dos dados do processo no banco (nunca do
próprio modelo "lembrando" nada).

⚠️ O modelo local roda numa janela de contexto pequena (ver
IA_LOCAL_CONTEXT_SIZE, padrão 4096 tokens) — por isso o digest é cortado a
um orçamento de caracteres (`LIMITE_PADRAO_CHARS`); processos com histórico
muito longo têm as movimentações/decisões mais antigas omitidas, e isso é
sinalizado ao usuário (`digest_truncado`) em vez de escondido.
"""
from app.utils import ia_local

LIMITE_PADRAO_ITENS = 20  # nº máx. de andamentos/movimentações/decisões cada, mais recentes primeiro
LIMITE_PADRAO_CHARS = 6000  # orçamento aproximado de caracteres do digest


RESUMO_SYSTEM = (
    "Você é o assistente de operação jurídica interno de um escritório de advocacia. "
    "Sua tarefa agora é ler os dados reais de UM processo específico (fornecidos abaixo, extraídos "
    "diretamente do sistema do escritório) e produzir um resumo objetivo dos autos: situação atual, "
    "últimos atos relevantes, prazos pendentes e pontos de atenção. Responda em português do Brasil, "
    "de forma direta. Use APENAS as informações fornecidas no contexto abaixo — nunca invente fato, "
    "data, valor, lei ou movimentação que não esteja ali. Se um dado relevante para a pergunta não "
    "estiver disponível no contexto, diga isso explicitamente em vez de supor. Você não é advogado, e "
    "este resumo é um apoio para leitura rápida — não substitui a análise do processo pelo advogado "
    "responsável antes de qualquer decisão."
)

RASCUNHO_SYSTEM = (
    "Você é o assistente de operação jurídica interno de um escritório de advocacia, ajudando um "
    "advogado a preparar um RASCUNHO INICIAL de peça processual — nunca um texto pronto para "
    "protocolar. Use os dados reais do processo fornecidos abaixo como base factual, e o pedido do "
    "advogado (também abaixo) para decidir o tipo de peça e a linha argumentativa. Responda em "
    "português do Brasil, em formato de petição (endereçamento, qualificação das partes quando "
    "disponível nos dados, dos fatos, do direito, dos pedidos). Onde faltar informação para completar "
    "algo (número de artigo de lei, jurisprudência específica, dado não presente no contexto), escreva "
    "claramente '[REVISAR: ...]' explicando o que falta, em vez de inventar citação, número de lei, "
    "precedente ou fato — isso é mais importante do que parecer completo. Este é um rascunho gerado "
    "por um modelo de IA local, de porte pequeno: o advogado responsável DEVE revisar, corrigir e "
    "validar juridicamente cada trecho antes de usar, protocolar ou enviar a qualquer parte."
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
    propagar ia_local.ModeloIndisponivelError quando o modelo local não está
    pronto no servidor — quem chama decide como exibir cada um.

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

    resultado = ia_local.gerar_resposta(system, [{"role": "user", "content": pedido}], max_tokens=max_tokens)
    return resultado, truncado
