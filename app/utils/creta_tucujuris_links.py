"""
Links de conveniência pra dois sistemas soltos — Creta (Justiça Federal
em Pernambuco/JEF) e Tucujuris (TJAP) — PENDENCIAS.md, seção -86
(pesquisa) e -87 (implementação). Mesma disciplina dos outros módulos
de link (eproc_links.py, projudi_links.py): isto NÃO é captura
automática, só abre o site OFICIAL do tribunal numa aba nova.

Diferença pros outros módulos: aqui todos os links são só "tipo: link"
(sem ponte de auto-envio) — nunca pré-preenchem o número do processo.
Motivo, confirmado ao vivo (seção -86):

- **Creta**: a busca exige um captcha de imagem ("Informe o que está
  escrito na imagem ao lado") embutido direto no formulário, diferente
  a cada carregamento de página — não tem como pré-preencher e mandar
  direto, o captcha muda toda vez de qualquer forma.
- **Tucujuris**: a página carrega com Cloudflare Turnstile E reCAPTCHA
  do Google ao mesmo tempo — não dá pra confiar num token de sessão
  previsto de antemão pra uma ponte de auto-envio (mesma cautela do
  TJAM no Projudi).

**Importante sobre o Creta:** só a seção da Justiça Federal em
Pernambuco (JFPE) foi testada e confirmada ao vivo. O Creta também é
usado por outras seções (Sergipe, Ceará, Paraíba, Alagoas), mas cada
uma parece ter um endereço/estrutura própria — uma delas (Sergipe) nem
parecia ser página de consulta pública, e sim de login — então não
incluí nenhuma das outras sem confirmar ao vivo primeiro (nunca
inventar URL que não foi vista de verdade).
"""
from app.utils.cnj import validar_numero_cnj


def links_outros_estadual(numero_cnj: str) -> list[dict]:
    """Segmento "8" (Justiça Estadual) — Tucujuris (TJAP). Lista vazia se
    o número não for desse segmento."""
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"] or validado["partes"]["segmento_codigo"] != "8":
        return []
    return [
        {
            "rotulo": "TJAP — Tucujuris (abre a busca, sem o número pré-preenchido)",
            "tipo": "link",
            "url": "https://tucujuris.tjap.jus.br/pages/consultar-processo/consultar-processo.html",
        },
    ]


def links_outros_federal(numero_cnj: str) -> list[dict]:
    """Segmento "4" (Justiça Federal) — Creta, só a seção de Pernambuco
    (JFPE), a única confirmada ao vivo. Lista vazia se o número não for
    desse segmento."""
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"] or validado["partes"]["segmento_codigo"] != "4":
        return []
    return [
        {
            "rotulo": "Creta — Justiça Federal em Pernambuco/JEF (abre a busca, sem o número pré-preenchido)",
            "tipo": "link",
            "url": "https://creta.jfpe.jus.br/cretainternetpe/consulta/processo/pesquisar.wsp",
        },
    ]
