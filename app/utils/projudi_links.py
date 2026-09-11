"""
Links de conveniência pra consulta pública do Projudi — PENDENCIAS.md,
seção -84 (pesquisa) e -85 (implementação). Mesma disciplina do
app/utils/eproc_links.py: isto NÃO é um conector de captura automática —
não existe (e não vai existir) um "ConectorProjudiPublico" que busca
sozinho.

Motivo (seção -84): testei ao vivo TJPR, TJAM e TJGO — os três têm
alguma camada de proteção anti-bot (reCAPTCHA de imagem no TJPR,
reCAPTCHA + firewall de aplicação no TJAM, Cloudflare já no carregamento
da página no TJGO). Um robô nosso não passa por nenhum dos três, e eu
não construo nada que tente passar.

O que ISTO faz: abre a página OFICIAL do tribunal, já com o número do
processo pronto quando o formulário permite, numa aba nova — pra um
humano de verdade (o usuário logado no JusControl) resolver o captcha e
ver o resultado direto no site do tribunal. Não captura nada de volta.

Dois tribunais cobertos, tratados diferente por causa do que confirmei
ao vivo em cada um:

- **TJPR**: o formulário real (`consultaPublica.do`, POST) confirmei no
  HTML que Comarca/Juízo NÃO são obrigatórios pra busca por número do
  processo — só pra busca sem esses dados. Dá pra pré-preencher só o
  número e mandar por uma ponte de auto-envio (`tipo: "bridge"`), igual
  ao padrão do TJSC/TJRJ no eproc — mesmos nomes de campo reais
  confirmados ao vivo, nunca inventados. IMPORTANTE: diferente do
  eproc/Cloudflare, aqui o captcha é um reCAPTCHA do Google que só
  aparece via JavaScript da própria página do tribunal ao clicar
  "Pesquisar" — não confirmei se o formulário aceita a busca vindo de
  uma ponte de auto-envio de outra origem (sem o reCAPTCHA carregado) ou
  se o servidor recusa por faltar o token do captcha. É uma tentativa
  razoável e de baixo risco: pior caso, a pessoa cai numa tela de erro e
  precisa refazer a busca à mão direto no site — não é pior do que não
  ter o botão. Avise se isso acontecer que eu reviso a abordagem.
- **TJAM**: ao tentar uma busca de teste ao vivo, a requisição foi
  rejeitada na hora por um firewall de aplicação ("The requested URL was
  rejected") — sinal de que esse tribunal é mais agressivo detectando
  automação, inclusive vinda de fora do fluxo normal de navegação. Por
  isso o TJAM entra só como link solto (`tipo: "link"`) pra página de
  busca, sem tentar pré-preencher — mesma cautela usada pro TJRS no
  eproc.

**TJGO fica de fora** (seção -84): bloqueou já no carregamento simples
da página de busca nos testes — não dá nem pra confiar que um link solto
abre de forma consistente.
"""
from app.utils.cnj import validar_numero_cnj

# ---- TJPR: formulário clássico do Projudi (`consultaPublica.do`), POST,
# confirmado ao vivo (PENDENCIAS.md seção -84) — Comarca/Juízo não são
# obrigatórios pra busca por número do processo. ----
_CAMPOS_FIXOS_TJPR = {
    "processoPageSize": "20",
    "processoPageNumber": "1",
    "processoSortColumn": "p.numeroUnico",
    "processoSortOrder": "asc",
    "codVaraEscolhida": "",
    "opcaoConsultaPublica": "1",  # "1" = Primeira Instância (padrão já selecionado no formulário real)
    "flagNumeroUnico": "true",  # número único (padrão já selecionado no formulário real)
    "codTribunal": "1",
    "codComarca": "-1",  # "Selecione Para Busca" — não exigido pra busca por número
    "codVara": "",
    "tipoCompetencia": "",
    "turma": "",
    "nomeParte": "",
    "cpfCnpj": "",
    "loginAdvogado": "",
    "nomeAdvogado": "",
    "oab": "",
    "oabComplemento": "N",
    "oabUF": "PR",
}

_FORMULARIOS = {
    "tjpr": {
        "nome": "TJPR — Projudi",
        "action_base": "https://consulta.tjpr.jus.br/projudi_consulta/processo/consultaPublica.do?actionType=pesquisar",
        "campo_numero": "numeroProcesso",
        "metodo": "post",
        "campos_fixos": _CAMPOS_FIXOS_TJPR,
    },
}


def formulario_projudi(slug: str) -> dict | None:
    """Usado pela rota de auto-envio (app/routes/governanca.py) — devolve
    None pra slug desconhecido."""
    return _FORMULARIOS.get(slug)


def links_projudi_estadual(numero_cnj: str) -> list[dict]:
    """Segmento "8" (Justiça Estadual) — TJPR (ponte de auto-envio) e TJAM
    (link solto). TJGO fica de fora (bloqueou já no carregamento — ver
    seção -84). Lista vazia se o número não for desse segmento."""
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"] or validado["partes"]["segmento_codigo"] != "8":
        return []
    return [
        {"rotulo": "TJPR — Projudi", "tipo": "bridge", "slug": "tjpr"},
        {
            "rotulo": "TJAM — Projudi (abre a busca, sem o número pré-preenchido)",
            "tipo": "link",
            "url": "https://projudi-consulta.tjam.jus.br/publica",
        },
    ]
