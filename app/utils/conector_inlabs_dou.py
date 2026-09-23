"""
Conector de captura do Diário Oficial da União (DOU) via INLABS — a fonte
oficial da Imprensa Nacional, livre e gratuita desde 01/01/2020 (ver
PENDENCIAS.md, seção mais recente, para a pesquisa completa e o porquê de
"diário completo" ter sido a abordagem escolhida em vez de só busca por
palavra-chave no site).

Diferente do DJEN/API Comunica (app/utils/conector_djen.py — sem cadastro
nenhum), o INLABS exige uma conta (e-mail + senha, gratuita, cadastrada uma
vez em https://inlabs.in.gov.br/) — credencial ÚNICA da plataforma inteira
(INLABS_EMAIL/INLABS_SENHA em config.py), nunca por empresa cliente (BYOK
não se aplica aqui: o DOU é o mesmo diário pra todo mundo).

Fluxo (`baixar_materias_do_dia`, chamado 1x/dia por capturar_publicacoes_dou.py):
  1. Login (POST logar.php) — pega um cookie de sessão.
  2. Lista os arquivos .zip disponíveis pra uma data (GET index.php?p=<data>)
     — cada seção pedida (DO1/DO2/DO3/DO1E/DO2E/DO3E) vira um .zip separado;
     um dia sem edição de alguma seção simplesmente não aparece na lista
     (nunca trata isso como erro).
  3. Baixa e extrai cada .zip — dentro tem um arquivo .xml por matéria
     publicada.
  4. Faz o parsing de cada .xml pra um dict com os campos usados pela
     triagem (app/utils/captura_dou_pipeline.py).
  5. Apaga TUDO que baixou (zips e xmls) ao final — nunca guarda o Diário
     bruto em disco além do tempo da própria captura; só o que bate com
     algum termo de interesse vira registro permanente (ver pipeline).

⚠️ LIMITAÇÃO REAL, leia antes de prometer algo pro cliente final — este
conector é MAIS bem fundamentado que o de DJEN (baseado no código-fonte real
de ingestão de um projeto do próprio governo federal que faz exatamente
isto, o Ro-DOU: github.com/gestaogovbr/Ro-dou, arquivo
dag_load_inlabs/ro_dou_inlabs_load_pg_dag.py e o schema em
dag_load_inlabs/sql/init-db.sql), mas AINDA ASSIM não foi testado contra um
download real a partir deste ambiente de geração de código — o proxy de
rede daqui bloqueia qualquer domínio .gov.br por política. Os nomes de
campo abaixo (id, name, pubName, artType, pubDate, artCategory, Identifica,
Data, ementa, titulo, subtitulo, texto) vêm do schema Postgres que aquele
projeto usa pra guardar o que extrai do mesmo XML — por isso `_valor()`
busca cada campo de forma tolerante a maiúscula/minúscula, mas teste com um
dia real após o deploy (no Brasil — o INLABS, assim como o DJEN, é
plausível que restrinja acesso a fora do país, embora isso não tenha sido
confirmado em nenhuma fonte pesquisada) e ajuste o mapeamento se algum
campo vier sistematicamente vazio.

Também não confirmado: se a conta gratuita do INLABS tem algum limite de
requisições/downloads por dia — nenhuma fonte pesquisada menciona isso, mas
`baixar_materias_do_dia` só é chamado 1x/dia pelo cron, então o volume de
uso é baixo por natureza.
"""
import os
import re
import shutil
import tempfile
import zipfile
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import current_app

BASE_URL = "https://inlabs.in.gov.br/"
TIMEOUT_SEGUNDOS = 60

# Seções aceitas pelo INLABS (ver README oficial: github.com/Imprensa-Nacional/inlabs).
# O escritório escolheu monitorar as 3 seções principais por padrão (não as
# edições extras "E") — ver PENDENCIAS.md.
SECOES_PADRAO = ("DO1", "DO2", "DO3")
TODAS_SECOES = ("DO1", "DO2", "DO3", "DO1E", "DO2E", "DO3E")


class ConexaoInlabsError(Exception):
    """Erro amigável — cobre credencial não configurada, falha de login,
    falha de rede e resposta inesperada do INLABS. Quem chama nunca
    precisa diferenciar os casos além de logar/avisar e seguir em frente
    (mesmo princípio de ConexaoComunicaError em conector_djen.py)."""


def login_configurado():
    """Checagem rápida (sem chamar rede) — usada pelo cron e pela tela de
    status pra avisar honestamente 'vigilância do DOU desligada' em vez de
    tentar e falhar a cada execução."""
    email = current_app.config.get("INLABS_EMAIL")
    senha = current_app.config.get("INLABS_SENHA")
    return bool(email and senha)


def _autenticar():
    email = current_app.config.get("INLABS_EMAIL")
    senha = current_app.config.get("INLABS_SENHA")
    if not email or not senha:
        raise ConexaoInlabsError(
            "Vigilância do DOU não configurada — cadastre INLABS_EMAIL/INLABS_SENHA "
            "(conta gratuita em https://inlabs.in.gov.br/)."
        )

    sessao = requests.Session()
    try:
        sessao.post(
            urljoin(BASE_URL, "logar.php"),
            data={"email": email, "password": senha},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT_SEGUNDOS,
        )
    except requests.exceptions.RequestException as e:
        raise ConexaoInlabsError(f"Falha de conexão ao autenticar no INLABS: {e}") from e

    if not sessao.cookies.get("inlabs_session_cookie"):
        raise ConexaoInlabsError(
            "Login no INLABS falhou — confira INLABS_EMAIL/INLABS_SENHA (credencial errada, "
            "ou conta desativada)."
        )
    return sessao


def _cabecalho_download(sessao):
    cookie = sessao.cookies.get("inlabs_session_cookie")
    # "origem" replicado do script oficial (inlabs-auto-download-*.py/.sh do
    # próprio repositório do INLABS) — não documentado o motivo, mas
    # presente em toda chamada de download nos dois scripts oficiais.
    return {"Cookie": f"inlabs_session_cookie={cookie}", "origem": "736372697074"}


def _listar_arquivos_do_dia(sessao, data_referencia, secoes):
    """Devolve a lista de hrefs (.zip) disponíveis pra `data_referencia`,
    filtrados pelas `secoes` pedidas. Um dia sem edição de alguma seção
    (feriado, domingo, edição não publicada) simplesmente não aparece —
    nunca é tratado como erro, só como "nada pra baixar dessa seção hoje"."""
    data_str = data_referencia.strftime("%Y-%m-%d")
    try:
        resposta = sessao.get(
            urljoin(BASE_URL, f"index.php?p={data_str}"),
            headers=_cabecalho_download(sessao), timeout=TIMEOUT_SEGUNDOS,
        )
    except requests.exceptions.RequestException as e:
        raise ConexaoInlabsError(f"Falha de conexão ao listar arquivos do INLABS: {e}") from e

    soup = BeautifulSoup(resposta.text, "html.parser")
    links = [a.get("href") for a in soup.find_all("a", title="Baixar Arquivo")]
    links = [href for href in links if href and href.endswith(".zip")]
    return [href for href in links if any(f"-{secao}.zip" in href for secao in secoes)]


def _baixar_e_extrair(sessao, hrefs, pasta_destino):
    """Baixa cada .zip e extrai na hora — devolve a lista de caminhos dos
    .xml extraídos (um arquivo por matéria publicada)."""
    caminhos_xml = []
    for href in hrefs:
        try:
            resposta = sessao.get(
                urljoin(BASE_URL, f"index.php{href}"),
                headers=_cabecalho_download(sessao), timeout=TIMEOUT_SEGUNDOS,
            )
        except requests.exceptions.RequestException as e:
            raise ConexaoInlabsError(f"Falha de conexão ao baixar '{href}' do INLABS: {e}") from e
        if resposta.status_code != 200:
            # Arquivo listado mas indisponível no momento do download — não
            # trava a captura do dia inteiro por causa de UMA seção; segue
            # pras próximas (mesmo espírito de "nunca desliga por erro
            # pontual" de capturar_intimacoes_oab.py).
            continue

        nome_zip = href.split("dl=")[-1] if "dl=" in href else "arquivo.zip"
        caminho_zip = os.path.join(pasta_destino, nome_zip)
        with open(caminho_zip, "wb") as f:
            f.write(resposta.content)

        try:
            with zipfile.ZipFile(caminho_zip, "r") as zip_ref:
                zip_ref.extractall(pasta_destino)
        except zipfile.BadZipFile:
            continue  # download corrompido/incompleto — descarta esta seção, segue pras próximas

    for nome in os.listdir(pasta_destino):
        if nome.lower().endswith(".xml"):
            caminhos_xml.append(os.path.join(pasta_destino, nome))
    return caminhos_xml


def _texto_direto(elemento):
    """Texto do elemento, ignorando o de filhos aninhados (mesmo raciocínio
    de .text simples do ElementTree, só protegendo contra None)."""
    return (elemento.text or "").strip() if elemento is not None else ""


def _valor(*fontes, nomes):
    """
    Busca `nomes` (várias variações de grafia aceitas) nos atributos E nos
    filhos diretos de cada elemento em `fontes` (em ordem — o primeiro que
    achar um valor não-vazio vence). Comparação de nome de tag/atributo é
    sempre case-insensitive, porque a grafia exata (pubName vs PubName vs
    pubname) não foi confirmada contra um arquivo real (ver aviso no topo
    do módulo).
    """
    nomes_normalizados = {n.lower() for n in nomes}
    for elemento in fontes:
        if elemento is None:
            continue
        for chave, valor in elemento.attrib.items():
            if chave.lower() in nomes_normalizados and valor and valor.strip():
                return valor.strip()
        for filho in elemento:
            tag = filho.tag.split("}")[-1]  # remove namespace, se houver
            if tag.lower() in nomes_normalizados:
                texto = _texto_direto(filho)
                if texto:
                    return texto
    return None


def _extrair_assinatura(texto_html):
    """O campo `texto` vem como HTML (confirmado no loader oficial do
    Ro-DOU) — a assinatura de quem publicou o ato normalmente vem marcada
    como `<p class="assina">Nome</p>` dentro dele. Best-effort: devolve None
    se não achar nada reconhecível, nunca quebra o parsing por isso."""
    if not texto_html:
        return None
    soup = BeautifulSoup(texto_html, "html.parser")
    tags = soup.find_all("p", class_="assina")
    if not tags:
        return None
    return ", ".join(t.get_text(strip=True) for t in tags if t.get_text(strip=True)) or None


def _texto_plano(texto_html):
    """Remove marcação HTML do campo `texto`, pra guardar/comparar texto
    limpo (a marcação em si nunca é mostrada ao usuário)."""
    if not texto_html:
        return ""
    return BeautifulSoup(texto_html, "html.parser").get_text(separator=" ", strip=True)


def _parsear_materia(caminho_xml, secao_arquivo):
    """
    Devolve um dict com os campos usados pela triagem, ou None se o arquivo
    não puder ser interpretado como uma matéria (XML corrompido/formato
    inesperado — nunca derruba a captura do dia inteiro por causa de UMA
    matéria malformada).
    """
    import xml.etree.ElementTree as ET

    try:
        raiz = ET.parse(caminho_xml).getroot()
    except ET.ParseError:
        return None

    corpo = None
    for elemento in raiz.iter():
        if elemento.tag.split("}")[-1].lower() == "body":
            corpo = elemento
            break

    id_materia = _valor(raiz, corpo, nomes=["id"])
    if not id_materia:
        return None  # sem id não dá pra deduplicar com segurança — descarta (mesmo critério do DJEN)

    texto_html = _valor(corpo, raiz, nomes=["texto"]) or ""

    return {
        "id_materia_fonte": id_materia,
        "secao": secao_arquivo,
        # ⚠️ "orgao" busca só "identifica" (nome do órgão, ex.: "Ministério da
        # Justiça"), NUNCA "name" aqui: o atributo `name` do <article> é o
        # slug da matéria (ex.: "portaria-123-2026-dou", usado em
        # `_montar_link_pdf` no pipeline), não o nome do órgão — se os dois
        # fossem buscados juntos, `_valor` acharia o atributo `name` da raiz
        # ANTES de olhar o filho <Identifica> do <body> (atributos da
        # primeira fonte vencem de filhos de fontes seguintes), devolvendo o
        # slug no lugar do órgão sempre que o XML tiver os dois campos —
        # já aconteceu aqui e foi pego pelo teste de conector_inlabs_dou.
        "orgao": _valor(raiz, corpo, nomes=["identifica"]),
        "titulo": _valor(corpo, raiz, nomes=["titulo"]),
        "ementa": _valor(corpo, raiz, nomes=["ementa"]),
        "subtitulo": _valor(corpo, raiz, nomes=["subtitulo"]),
        "texto_plano": _texto_plano(texto_html),
        "assinatura": _extrair_assinatura(texto_html),
        "data_publicacao_str": _valor(raiz, corpo, nomes=["pubdate", "data"]),
        "pdfpage": _valor(raiz, corpo, nomes=["pdfpage"]),
        "editionnumber": _valor(raiz, corpo, nomes=["editionnumber"]),
        # Slug da matéria — única fonte usada por `_montar_link_pdf` no
        # pipeline pra montar o link de leitura (best-effort, ver aviso lá).
        "name": _valor(raiz, corpo, nomes=["name"]),
    }


def _secao_do_nome_arquivo(caminho_xml, secoes_pedidas):
    """O nome do .zip (e, por extensão, dos .xml dentro dele) inclui a
    seção (ex.: '2026-09-22-DO1.zip') — usa isso pra rotular cada matéria
    com a seção correta em vez de confiar só no conteúdo do XML."""
    nome = os.path.basename(caminho_xml).upper()
    for secao in secoes_pedidas:
        if secao in nome:
            return secao
    return secoes_pedidas[0] if secoes_pedidas else "DO1"


def baixar_materias_do_dia(data_referencia=None, secoes=SECOES_PADRAO):
    """
    Ponto de entrada principal — baixa e extrai TODAS as matérias
    publicadas em `data_referencia` (default: hoje) nas `secoes` pedidas, e
    devolve uma lista de dicts (ver `_parsear_materia`). Sempre limpa os
    arquivos temporários ao final (sucesso ou erro).

    Levanta ConexaoInlabsError em qualquer falha de credencial/rede — quem
    chama (capturar_publicacoes_dou.py) decide como registrar/seguir.
    """
    data_referencia = data_referencia or date.today()
    sessao = _autenticar()

    pasta_temp = tempfile.mkdtemp(prefix="inlabs_dou_")
    try:
        hrefs = _listar_arquivos_do_dia(sessao, data_referencia, secoes)
        if not hrefs:
            return []

        caminhos_xml = _baixar_e_extrair(sessao, hrefs, pasta_temp)
        materias = []
        for caminho in caminhos_xml:
            secao = _secao_do_nome_arquivo(caminho, secoes)
            materia = _parsear_materia(caminho, secao)
            if materia:
                materia["data_publicacao_fallback"] = data_referencia
                materias.append(materia)
        return materias
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)
