"""
Ferramentas do Agente de IA (tool-calling) — pedido explícito do usuário
("quero melhorar o agente local... treinar ele para resolver tudo que
depende de um agente de forma mais eficaz"). "Treinar" (ajustar os pesos
do modelo) não é viável neste ambiente (sem GPU, sem dado de treino, e o
modelo já roda só na CPU do servidor de produção) — o que É viável, e
resolve o mesmo problema de fundo, é dar ao agente ACESSO REAL aos dados
do sistema durante a própria conversa, em vez de ele só receber um resumo
fixo pré-carregado uma vez no início (ver `_contexto_operacao` etc. em
app/routes/agente_ia.py, que continuam existindo — as ferramentas aqui são
um COMPLEMENTO, pra quando a pergunta precisa de um dado mais específico
do que aquele resumo cobre).

Mecanismo: um laço "ReAct" simples, implementado no nível de TEXTO/PROMPT
(não a API nativa de function-calling de nenhum provedor específico) —
funciona igual pros três provedores possíveis (local via llama-cpp-python,
Claude BYOK, Gemini BYOK — ver app/utils/agente_ia_router.py), porque cada
um já aceita a mesma lista simples de {"role", "content"}. O system prompt
(ver `FERRAMENTAS_INSTRUCOES` em app/routes/agente_ia.py) instrui o modelo
a responder com APENAS um bloco JSON `{"ferramenta": "...", "argumentos":
{...}}` quando quiser consultar um dado; app/jobs/ia_jobs.py reconhece esse
padrão (`extrair_chamada_ferramenta`), executa a ferramenta aqui
(`executar_ferramenta`) e devolve o resultado como uma nova mensagem, num
laço de até `MAX_ITERACOES_FERRAMENTAS` rodadas, até o modelo responder em
texto livre — essa é a resposta final mostrada ao usuário. Um modelo
pequeno (1.5B/4B) não segue esse formato 100% das vezes; quando não segue,
o texto simplesmente não bate com o padrão esperado e é tratado como
resposta final normal (degrada bem, nunca trava a conversa).

Segurança (ponto crítico): toda ferramenta é OBRIGATORIAMENTE filtrada
pelo MESMO escopo de unidade/empresa que qualquer tela do sistema usa
(`aplicar_escopo_unidade`/`usuario_pode_ver_processo`/
`filtrar_processos_visiveis`, todas em app/utils/acesso.py — nenhuma
lógica de escopo é duplicada aqui). Como isso roda dentro do worker de
fila (sem sessão HTTP/`current_user`, ver app/jobs/ia_jobs.py), cada
função aqui recebe explicitamente um `Usuario` de verdade (carregado do
banco pelo próprio worker, dentro do `app_context`) e repassa pra
`usuario=` nessas funções do app/utils/acesso.py — nunca lê `current_user`
diretamente, e nunca monta uma query sem passar por uma dessas funções.
"""
import json
import re
from datetime import date, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models import Processo, Prazo, Tarefa, Cliente, Lancamento
from app.utils.acesso import aplicar_escopo_unidade, filtrar_processos_visiveis
from app.utils.calculo_custas import ordem_nulls_last
from app.utils.financeiro_util import filtro_conta_terceiros

MAX_ITERACOES_FERRAMENTAS = 4
MAX_RESULTADOS = 8


def _moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _escopo_processos_visiveis(usuario):
    query = aplicar_escopo_unidade(Processo.query, Processo, usuario=usuario)
    return filtrar_processos_visiveis(query, usuario=usuario)


def _fmt_processo(p):
    numero = p.numero_processo or p.numero_interno or f"#{p.id}"
    return (f"processo {numero} — cliente: {p.cliente.nome if p.cliente else '—'}, "
            f"área: {p.area_direito or '—'}, status: {p.status}, fase: {p.fase or '—'}")


def ferramenta_buscar_processos(usuario, termo=None, **_ignorados):
    if not termo or not str(termo).strip():
        return "Informe um termo de busca (número do processo ou nome do cliente)."
    padrao = f"%{str(termo).strip()}%"
    resultados = (
        _escopo_processos_visiveis(usuario)
        .join(Cliente, isouter=True)
        .filter(db.or_(
            Processo.numero_processo.ilike(padrao),
            Processo.numero_interno.ilike(padrao),
            Cliente.nome.ilike(padrao),
        ))
        .limit(MAX_RESULTADOS).all()
    )
    if not resultados:
        return f"Nenhum processo encontrado para \"{termo}\"."
    return "\n".join(f"- {_fmt_processo(p)}" for p in resultados)


def ferramenta_buscar_cliente(usuario, termo=None, **_ignorados):
    if not termo or not str(termo).strip():
        return "Informe um termo de busca (nome do cliente)."
    clientes = (
        aplicar_escopo_unidade(Cliente.query, Cliente, usuario=usuario)
        .filter(Cliente.nome.ilike(f"%{str(termo).strip()}%"))
        .limit(MAX_RESULTADOS).all()
    )
    if not clientes:
        return f"Nenhum cliente encontrado para \"{termo}\"."
    linhas = []
    for c in clientes:
        qtd = _escopo_processos_visiveis(usuario).filter(Processo.cliente_id == c.id).count()
        linhas.append(f"- {c.nome} ({'ativo' if c.ativo else 'inativo'}), {qtd} processo(s) visível(is) por este usuário.")
    return "\n".join(linhas)


def ferramenta_consultar_prazos(usuario, dias=7, status="pendente", **_ignorados):
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        dias = 7
    dias = max(1, min(dias, 90))

    hoje = date.today()
    query = Prazo.query.join(Processo).filter(Prazo.deletado_em.is_(None))
    query = aplicar_escopo_unidade(query, Processo, usuario=usuario)
    if status and status in Prazo.STATUS:
        query = query.filter(Prazo.status == status)
    query = query.filter(Prazo.data_vencimento <= hoje + timedelta(days=dias))
    prazos = query.order_by(Prazo.data_vencimento).limit(MAX_RESULTADOS).all()
    if not prazos:
        return f"Nenhum prazo com status \"{status or 'qualquer'}\" vencendo nos próximos {dias} dia(s)."
    linhas = []
    for p in prazos:
        numero = p.processo.numero_processo or p.processo.numero_interno or f"#{p.processo_id}"
        linhas.append(f"- \"{p.descricao}\" (processo {numero}), vence em "
                       f"{p.data_vencimento.strftime('%d/%m/%Y')}, status: {p.status}.")
    return "\n".join(linhas)


def ferramenta_consultar_tarefas(usuario, status=None, **_ignorados):
    query = aplicar_escopo_unidade(Tarefa.query, Tarefa, usuario=usuario)
    if status and status in Tarefa.STATUS:
        query = query.filter(Tarefa.status == status)
    tarefas = query.order_by(*ordem_nulls_last(Tarefa.data_vencimento)).limit(MAX_RESULTADOS).all()
    if not tarefas:
        return f"Nenhuma tarefa com status \"{status or 'qualquer'}\" encontrada."
    linhas = []
    for t in tarefas:
        venc = t.data_vencimento.strftime("%d/%m/%Y") if t.data_vencimento else "sem vencimento"
        linhas.append(f"- \"{t.titulo}\" — status: {t.status}, prioridade: {t.prioridade}, vencimento: {venc}.")
    return "\n".join(linhas)


def ferramenta_consultar_financeiro(usuario, natureza=None, status=None, conta="operacional", **_ignorados):
    # Mesma checagem de acesso financeiro do resto do sistema (ver
    # PENDENCIAS.md, seção -45 — `Usuario.pode_ver_financeiro`) — sem isso,
    # qualquer usuário conseguiria contornar a restrição da persona
    # "Negócios" (ver app/routes/agente_ia.py) só pedindo pra outra
    # persona usar esta ferramenta.
    if not usuario.pode_ver_financeiro:
        return "Este usuário não tem acesso a dados financeiros — não é possível consultar."

    eh_terceiros = conta == "terceiros"
    query = aplicar_escopo_unidade(Lancamento.query, Lancamento, usuario=usuario).filter(
        filtro_conta_terceiros(eh_terceiros)
    )
    if natureza in Lancamento.NATUREZAS:
        query = query.filter(Lancamento.natureza == natureza)
    if status in Lancamento.STATUS:
        query = query.filter(Lancamento.status == status)

    qtd = query.count()
    total = query.with_entities(func.coalesce(func.sum(Lancamento.valor), 0)).scalar()

    partes = [f"conta: {'terceiros' if eh_terceiros else 'operacional'}"]
    if natureza:
        partes.append(f"natureza: {natureza}")
    if status:
        partes.append(f"status: {status}")
    return f"{qtd} lançamento(s) ({', '.join(partes)}), soma: {_moeda(total)}."


FERRAMENTAS = {
    "buscar_processos": dict(
        descricao="Busca processos por número (CNJ ou interno) ou nome do cliente.",
        argumentos='{"termo": "texto a buscar"}',
        executar=ferramenta_buscar_processos,
    ),
    "buscar_cliente": dict(
        descricao="Busca clientes pelo nome e mostra quantos processos cada um tem.",
        argumentos='{"termo": "nome ou parte do nome"}',
        executar=ferramenta_buscar_cliente,
    ),
    "consultar_prazos": dict(
        descricao='Lista prazos processuais vencendo num período, filtrando por status '
                   '(padrão: "pendente").',
        argumentos='{"dias": 7, "status": "pendente"}',
        executar=ferramenta_consultar_prazos,
    ),
    "consultar_tarefas": dict(
        descricao="Lista tarefas internas, opcionalmente filtrando por status.",
        argumentos='{"status": "pendente"}',
        executar=ferramenta_consultar_tarefas,
    ),
    "consultar_financeiro": dict(
        descricao="Soma lançamentos financeiros por natureza/status/conta — só funciona "
                   "para quem tem acesso financeiro liberado.",
        argumentos='{"natureza": "receita", "status": "pendente", "conta": "operacional"}',
        executar=ferramenta_consultar_financeiro,
    ),
}


def descricao_ferramentas_para_prompt():
    return "\n".join(f"- {nome}({info['argumentos']}): {info['descricao']}"
                      for nome, info in FERRAMENTAS.items())


def extrair_chamada_ferramenta(texto):
    """
    Reconhece se a resposta do modelo é uma CHAMADA de ferramenta — um
    bloco JSON, sem nenhum outro texto visível em volta (é assim que o
    system prompt instrui, ver FERRAMENTAS_INSTRUCOES em
    app/routes/agente_ia.py) — em vez de uma resposta final em texto livre
    pro usuário. Deliberadamente rígido: só reconhece quando o texto
    INTEIRO (depois de tirar espaço em volta) é um objeto JSON válido com
    a chave "ferramenta", pra uma resposta normal que por acaso mencione
    chaves/JSON nunca ser confundida com uma chamada de verdade.

    Devolve None quando não é uma chamada (o texto deve ser tratado como
    resposta final), ou {"ferramenta": str, "argumentos": dict}.
    """
    if not texto:
        return None
    texto_limpo = texto.strip()
    # Modelos pequenos às vezes envolvem o JSON num bloco ```json ... ```
    # mesmo instruídos a não fazer isso — tolera esse único formato a mais.
    if texto_limpo.startswith("```"):
        texto_limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto_limpo, flags=re.IGNORECASE).strip()

    if not (texto_limpo.startswith("{") and texto_limpo.endswith("}")):
        return None
    try:
        dado = json.loads(texto_limpo)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(dado, dict) or "ferramenta" not in dado:
        return None

    argumentos = dado.get("argumentos")
    if not isinstance(argumentos, dict):
        argumentos = {}
    return {"ferramenta": dado.get("ferramenta"), "argumentos": argumentos}


def executar_ferramenta(chamada, usuario):
    """Executa a ferramenta pedida e devolve o resultado como texto — nunca
    levanta exceção pra fora (um erro de ferramenta vira texto explicando
    o erro, pro modelo conseguir seguir a conversa em vez de travar)."""
    nome = chamada.get("ferramenta")
    argumentos = chamada.get("argumentos") or {}
    info = FERRAMENTAS.get(nome)
    if not info:
        return f"Ferramenta \"{nome}\" não existe. Ferramentas disponíveis: {', '.join(FERRAMENTAS)}."
    try:
        return info["executar"](usuario, **argumentos)
    except Exception:
        return f"Não foi possível executar a ferramenta \"{nome}\" com os argumentos informados."
