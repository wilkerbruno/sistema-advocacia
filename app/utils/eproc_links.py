"""
Links de conveniência pra consulta pública do eproc — PENDENCIAS.md, seção
-81. IMPORTANTE: isto NÃO é um conector de captura automática, do tipo
`ConectorEsajPublico`/`ConectorPjePublico` — não existe (e não vai
existir) um `ConectorEprocPublico` que busca sozinho.

Motivo (ver seções -80 e -92): todo sistema eproc que testei ao vivo está
atrás de Cloudflare Turnstile ("confirme que é humano"), ou — caso do
TJRS — de uma API própria protegida por **Altcha** (confirmado ao vivo,
seção -92: é um captcha "silencioso" de prova de trabalho — o app
resolve um desafio criptográfico em `<serviço>/auth/token` +
`/auth/submit` antes de poder chamar a API de consulta; mesma família de
proteção do Anubis, visto no TRT-23). Um robô nosso não passa por nenhum
dos dois, e eu não construo nada que tente passar (contornar CAPTCHA e
anti-bot está fora de cogitação, sem exceção, seja o desafio visível ou
silencioso).

O que ISTO faz: abre a página OFICIAL do tribunal, já com o número do
processo pronto, numa aba nova — pra um humano de verdade (o usuário
logado no JusControl) resolver o desafio e ver o resultado direto no
site do tribunal. Não captura nada de volta pro JusControl automaticamente
— é só uma conveniência pra não precisar copiar o número e procurar o
site certo na mão.

Dois formatos de tribunal, por causa de como o formulário real de cada um
funciona:
  - "link": o formulário do tribunal é GET — dá pra montar a URL direto
    (testado contra o portal unificado do TRF4).
  - "post": o formulário do tribunal é POST (a família "clássica" do
    eproc, `externo_controlador.php`) — não dá pra montar como link puro;
    quem usa isto precisa passar pela rota
    `governanca.abrir_eproc_auto_envio`, que renderiza uma página com um
    <form> oculto (com os MESMOS nomes de campo confirmados ao vivo no
    HTML real de cada tribunal — nunca inventados) que se auto-envia via
    JS. É exatamente o que o navegador do usuário faria se ele preenchesse
    e clicasse "Consultar" à mão — não é um jeito de pular o CAPTCHA: o
    Cloudflare intercepta a requisição do mesmo jeito (confirmado testando
    o TRF4 ao vivo — a mesma tela "confirme que é humano" aparece).

O TJRS entra como link solto pra a própria página de busca (sem número
pré-preenchido) porque é uma SPA em Angular, não um formulário HTML
comum — não tentei adivinhar um jeito de pré-preencher via URL sem
confirmar que o app aceita isso (mesma disciplina de nunca inventar
comportamento não confirmado que vale pros nomes de campo dos outros).
"""
from app.utils.cnj import validar_numero_cnj

# ---- Federal (segmento "4"): um único portal cobre TRF4 (2º grau) e a
# Justiça Federal de 1º grau no RS, SC e PR — testado ao vivo, formulário
# GET real, confirmado contra `consulta.trf4.jus.br` (ver seção -80). ----
_TRF4_ORIGENS = [
    ("TRF", "TRF4 — 2º grau"),
    ("RS", "Justiça Federal no RS (JFRS) — 1º grau"),
    ("SC", "Justiça Federal em SC (JFSC) — 1º grau"),
    ("PR", "Justiça Federal no PR (JFPR) — 1º grau"),
]


def links_eproc_federal(numero_cnj: str) -> list[dict]:
    """Segmento "4" (Justiça Federal) — TRF4/JFRS/JFSC/JFPR. Lista vazia se
    o número não for desse segmento (nunca adivinha; quem decide qual dos
    4 é o certo é o usuário, clicando)."""
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"] or validado["partes"]["segmento_codigo"] != "4":
        return []
    numero_fmt = validado["partes"]["formatado"]
    return [
        {
            "rotulo": rotulo,
            "tipo": "link",
            "url": (
                "https://consulta.trf4.jus.br/trf4/controlador.php"
                f"?acao=consulta_processual_valida_pesquisa&selForma=NU"
                f"&txtValor={numero_fmt}&selOrigem={origem}"
            ),
        }
        for origem, rotulo in _TRF4_ORIGENS
    ]


# ---- Estadual (segmento "8"): TJRS (SPA própria), TJSC e TJRJ (formulário
# clássico do eproc, POST — precisa da rota de auto-envio). TJTO e TJPR
# ficam de fora: TJTO desligou a consulta pública em 2020 (sobrecarga de
# robôs) e nunca reativou; TJPR ainda não abriu consulta pública do eproc
# (só Projudi) — ver seção -80. ----
_CAMPOS_FIXOS_COMUNS = {
    "acao": "processo_consulta_publica",
    "acao_origem": "principal",
    "acao_retorno": "processo_consulta_publica",
    "hdnInfraTipoPagina": "1",
    "sbmNovo": "Consultar",
    "txtNumChave": "",
    "txtNumChaveDocumento": "",
    "txtStrParte": "",
    "chkFonetica": "N",
    "txtCpfCnpj": "",
    "txtStrOAB": "",
    "cf-turnstile-response": "",
    "hdnInfraCaptcha": "0",
    "hdnInfraSelecoes": "Infra",
}

# NOTA (achado em uso real, PENDENCIAS.md seção -81): a primeira versão
# disto enviava por POST (é o método que o formulário real do tribunal
# declara) — só que na prática o Cloudflare, ao interceptar um POST pra
# mostrar/validar o desafio, não consegue "repetir" esse POST depois: o
# usuário volta pra tela em branco, sem o número preenchido, mesmo o
# desafio passando com sucesso. Troquei pra GET, usando os MESMOS nomes de
# campo reais (nunca inventados) — GET sobrevive ao redirecionamento do
# Cloudflare porque o estado inteiro já vai na própria URL. NÃO CONFIRMADO
# 100% que o backend do tribunal aceita busca via GET (só confirmei que o
# formulário real É POST) — é uma tentativa razoável e de baixo risco: se
# não funcionar, o pior caso é o mesmo problema de antes (cai numa tela em
# branco), não piora nada.
_FORMULARIOS = {
    "tjrj": {
        "nome": "TJRJ — eproc (sistema secundário; o principal do TJRJ é o PJe, botão acima)",
        "action_base": "https://eproc1g-cp.tjrj.jus.br/eproc/externo_controlador.php",
        "campo_numero": "txtNumProcesso",
        "metodo": "get",
        "campos_fixos": _CAMPOS_FIXOS_COMUNS,
    },
    "tjsc": {
        "nome": "TJSC — eproc (sistema principal do tribunal)",
        "action_base": "https://eprocwebcon.tjsc.jus.br/consulta1g/externo_controlador.php",
        "campo_numero": "txtNumProcesso",
        "metodo": "get",
        "campos_fixos": _CAMPOS_FIXOS_COMUNS,
    },
}


def formulario_post(slug: str) -> dict | None:
    """Usado pela rota de auto-envio (app/routes/governanca.py) — devolve
    None pra slug desconhecido. (Nome mantido por compatibilidade, mas o
    envio agora é por GET — ver nota acima.)"""
    return _FORMULARIOS.get(slug)


def links_eproc_estadual(numero_cnj: str) -> list[dict]:
    """Segmento "8" (Justiça Estadual) — TJRS, TJSC, TJRJ. Lista vazia se o
    número não for desse segmento."""
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"] or validado["partes"]["segmento_codigo"] != "8":
        return []
    links = [
        {
            "rotulo": "TJRS — eproc (sistema principal do tribunal; abre a busca, sem o número pré-preenchido)",
            "tipo": "link",
            "url": "https://consulta.tjrs.jus.br/consulta-processual/",
        },
    ]
    for slug, formulario in _FORMULARIOS.items():
        links.append({"rotulo": formulario["nome"], "tipo": "bridge", "slug": slug})
    return links
