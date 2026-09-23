"""
Pipeline de triagem da vigilância do Diário Oficial da União (DOU) — ver
app/utils/conector_inlabs_dou.py (download/parsing) e app/models/captacao_dou.py
(modelos). Ponto de entrada único: `processar_materia_capturada`, chamado
pelo cron (capturar_publicacoes_dou.py) uma vez por matéria do dia.

O que decide se uma matéria "interessa" a uma unidade (item confirmado com
o usuário — ver PENDENCIAS.md): o nome/CNPJ de cada `Cliente` ativo
cadastrado nessa unidade, OU uma `PalavraChaveDou` cadastrada manualmente.
Comparação sempre literal (substring, case/acento-insensível) — nunca fuzzy
— mesmo princípio já usado em app/utils/conflito_interesse.py: "um match
'quase igual' gera mais ruído do que ajuda".

⚠️ Falso positivo é esperado e normal, sobretudo em cliente PESSOA FÍSICA:
nome de pessoa não é um identificador único (diferente de CNPJ) — "João da
Silva" pode aparecer no DOU por ser outra pessoa completamente alheia ao
escritório (nomeação de servidor, por exemplo). Por isso toda captura nasce
como "pendente_revisao": é sempre um humano quem decide se a menção é do
cliente de verdade ou de um homônimo, nunca o sistema decide sozinho.
"""
import re
import unicodedata
from datetime import datetime

from app.extensions import db
from app.models import Cliente, PalavraChaveDou, PublicacaoDouCapturada
from app.utils.cnj import somente_digitos

# Termos normalizados mais curtos que isso nunca são comparados — um nome
# ou palavra-chave de 1-3 caracteres bateria em praticamente qualquer texto
# grande, gerando só ruído (mesmo raciocínio do corte mínimo em buscas de
# texto livre no resto do sistema).
TAMANHO_MINIMO_TERMO = 4

RAIO_TRECHO_CHARS = 300  # quantos caracteres de contexto pra cada lado do termo encontrado


def _normalizar(texto):
    """minúsculas + sem acento + espaços colapsados — mesmo critério de
    app/utils/conflito_interesse.py::_normalizar_nome, duplicado aqui (em
    vez de importado) porque são domínios diferentes (conflito de
    interesse vs. vigilância de diário oficial) que só coincidem por usar a
    mesma técnica simples de normalização de nome, não por dependência real
    de um módulo no outro."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().split())


def _regex_cnpj(digitos):
    """
    Monta uma regex que acha os 14 dígitos do CNPJ na ordem exata, tolerando
    pontuação/espaço opcional ENTRE cada dígito (cobre "12.345.678/0001-90",
    "12345678000190", ou variações com espaço) — sem colar todos os dígitos
    do texto num string só e comparar substring (isso juntaria números de
    processo/data/outros CNPJs vizinhos e daria falso positivo/negativo).
    """
    partes = [re.escape(d) + r"[.\s/-]*" for d in digitos[:-1]] + [re.escape(digitos[-1])]
    return re.compile("".join(partes))


def _termos_monitorados():
    """
    Devolve uma lista de tuplas (unidade_id, termo_normalizado_ou_regex,
    termo_original, cliente_id, eh_cnpj) juntando o monitoramento
    AUTOMÁTICO (nome + CNPJ de cada cliente ativo, não anonimizado — ver
    app/utils/lgpd.py::anonimizar_cliente, nunca monitora um cliente já
    anonimizado) com as palavras-chave cadastradas manualmente (só as
    ativas).
    """
    termos = []

    clientes = Cliente.query.filter_by(ativo=True).filter(Cliente.anonimizado_em.is_(None)).all()
    for cliente in clientes:
        nome_normalizado = _normalizar(cliente.nome)
        if len(nome_normalizado) >= TAMANHO_MINIMO_TERMO:
            termos.append((cliente.unidade_id, nome_normalizado, cliente.nome, cliente.id, False))

        digitos = somente_digitos(cliente.cpf_cnpj or "")
        # Só CNPJ (14 dígitos) — CPF (11 dígitos) quase nunca aparece
        # completo no DOU por exigência de anonimização/LGPD em dado
        # pessoal de pessoa física (o próprio Diário costuma mascarar CPF,
        # ex.: "123.***.**9-00"), então monitorar CPF completo aqui
        # dificilmente encontraria alguma coisa — não implementado.
        if len(digitos) == 14:
            termos.append((cliente.unidade_id, digitos, cliente.cpf_cnpj, cliente.id, True))

    palavras = PalavraChaveDou.query.filter_by(ativa=True).all()
    for palavra in palavras:
        termo_normalizado = _normalizar(palavra.termo)
        if len(termo_normalizado) >= TAMANHO_MINIMO_TERMO:
            termos.append((palavra.unidade_id, termo_normalizado, palavra.termo, None, False))

    return termos


def _recortar_ao_redor(texto_plano, inicio_match, fim_match):
    """Recorta `RAIO_TRECHO_CHARS` de contexto pra cada lado do intervalo
    [inicio_match, fim_match) de `texto_plano` — nunca guarda a matéria
    inteira (ver docstring de PublicacaoDouCapturada.texto_trecho)."""
    inicio = max(0, inicio_match - RAIO_TRECHO_CHARS)
    fim = min(len(texto_plano), fim_match + RAIO_TRECHO_CHARS)
    prefixo = "…" if inicio > 0 else ""
    sufixo = "…" if fim < len(texto_plano) else ""
    return prefixo + texto_plano[inicio:fim].strip() + sufixo


def _sem_ocorrencia(texto_plano):
    """O termo bateu no título/ementa/órgão, não no corpo do texto —
    devolve o início do texto como contexto geral, nunca quebra."""
    limite = 2 * RAIO_TRECHO_CHARS
    sufixo = "…" if len(texto_plano) > limite else ""
    return texto_plano[:limite].strip() + sufixo


def _montar_trecho(texto_plano, termo_normalizado):
    """
    Recorta o trecho ao redor da primeira ocorrência de um termo de NOME
    (substring literal, não CNPJ) — ver `_montar_trecho_cnpj` para o caso
    de CNPJ, que usa a regex tolerante a pontuação em vez de substring.

    Simplificação assumida: a posição encontrada em `_normalizar(texto)` é
    usada para recortar diretamente `texto_plano` (o original) — funciona
    bem porque a normalização usada aqui (remover acento, minúsculas) NUNCA
    muda o comprimento do texto em português (cada caractere acentuado vira
    exatamente um caractere ASCII), então os índices continuam alinhados.
    """
    if not texto_plano:
        return None
    texto_normalizado = _normalizar(texto_plano)
    pos = texto_normalizado.find(termo_normalizado)
    if pos == -1:
        return _sem_ocorrencia(texto_plano)
    return _recortar_ao_redor(texto_plano, pos, pos + len(termo_normalizado))


def _montar_trecho_cnpj(texto_plano, regex_cnpj):
    """Mesma ideia de `_montar_trecho`, mas localizando a posição via a
    regex tolerante a pontuação (ver `_regex_cnpj`) em vez de substring
    literal — o CNPJ pode aparecer formatado de várias formas no texto
    original, então a busca de posição precisa da mesma tolerância usada
    pra decidir se bateu."""
    if not texto_plano:
        return None
    match = regex_cnpj.search(texto_plano)
    if not match:
        return _sem_ocorrencia(texto_plano)
    return _recortar_ao_redor(texto_plano, match.start(), match.end())


def _parse_data_publicacao(materia):
    """Formato confirmado no loader oficial do Ro-DOU: '%d/%m/%Y' (ver
    aviso em conector_inlabs_dou.py). Cai pra data de referência da própria
    captura se o campo vier vazio ou em formato inesperado — nunca quebra
    o processamento por causa de uma data malformada."""
    bruta = materia.get("data_publicacao_str")
    if bruta:
        try:
            return datetime.strptime(bruta.strip(), "%d/%m/%Y").date()
        except ValueError:
            pass
    return materia.get("data_publicacao_fallback")


def _montar_link_pdf(materia):
    """
    ⚠️ Melhor esforço, não confirmado: o atributo `name` do INLABS parece,
    por convenção observada em outros sistemas da Imprensa Nacional, ser um
    slug utilizável como link de leitura em
    'https://www.in.gov.br/web/dou/-/<name>' (mesmo padrão usado pela busca
    oficial — ver dou_hook.py do projeto Ro-DOU, campo `urlTitle`) — mas
    isto NUNCA foi confirmado contra um `name` real do INLABS (que pode ter
    outra convenção). Se o link não abrir a matéria certa após o deploy,
    isso é esperado até validar/corrigir — o trecho capturado continua
    sendo a informação confiável independente do link.
    """
    nome = materia.get("name")
    if nome and "-" in nome and " " not in nome:
        return f"https://www.in.gov.br/web/dou/-/{nome}"
    return None


def processar_materia_capturada(materia):
    """
    Confere uma matéria do dia (dict devolvido por
    conector_inlabs_dou.baixar_materias_do_dia) contra todos os termos
    monitorados (clientes + palavras-chave, de TODAS as unidades) e cria
    uma `PublicacaoDouCapturada` (via db.session.add — quem chama faz o
    commit) para cada combinação unidade+termo que bateu.

    Idempotente: se a mesma unidade+matéria+termo já foi capturada antes
    (reprocessamento do mesmo dia), não duplica — a checagem de existência
    é a MESMA regra da UniqueConstraint do modelo, verificada antes de
    instanciar em vez de depender de capturar IntegrityError.

    Devolve a lista das `PublicacaoDouCapturada` criadas nesta chamada
    (pode ser vazia — a grande maioria das matérias não bate com nada).
    """
    id_materia = materia.get("id_materia_fonte")
    if not id_materia:
        return []

    texto_busca_normalizado = _normalizar(" ".join(filter(None, [
        materia.get("titulo"), materia.get("ementa"), materia.get("subtitulo"),
        materia.get("orgao"), materia.get("texto_plano"),
    ])))
    texto_bruto_para_cnpj = " ".join(filter(None, [
        materia.get("titulo"), materia.get("ementa"), materia.get("texto_plano"),
    ]))

    criadas = []
    ja_processados_nesta_chamada = set()  # evita duplicar quando dois clientes têm o mesmo nome normalizado

    for unidade_id, termo_comparavel, termo_original, cliente_id, eh_cnpj in _termos_monitorados():
        chave_dedup = (unidade_id, id_materia, termo_original)
        if chave_dedup in ja_processados_nesta_chamada:
            continue

        regex_cnpj = _regex_cnpj(termo_comparavel) if eh_cnpj else None
        if eh_cnpj:
            bateu = bool(regex_cnpj.search(texto_bruto_para_cnpj))
        else:
            bateu = termo_comparavel in texto_busca_normalizado
        if not bateu:
            continue

        ja_processados_nesta_chamada.add(chave_dedup)

        existente = PublicacaoDouCapturada.query.filter_by(
            unidade_id=unidade_id, id_materia_fonte=id_materia, termo_encontrado=termo_original,
        ).first()
        if existente:
            continue

        texto_plano = materia.get("texto_plano") or ""
        if eh_cnpj:
            trecho = _montar_trecho_cnpj(texto_plano, regex_cnpj)
        else:
            trecho = _montar_trecho(texto_plano, termo_comparavel)

        publicacao = PublicacaoDouCapturada(
            unidade_id=unidade_id, id_materia_fonte=id_materia, secao=materia.get("secao", "DO1"),
            orgao=materia.get("orgao"), titulo=materia.get("titulo"), ementa=materia.get("ementa"),
            texto_trecho=trecho,
            data_publicacao=_parse_data_publicacao(materia),
            link_pdf=_montar_link_pdf(materia),
            termo_encontrado=termo_original, cliente_id=cliente_id,
        )
        db.session.add(publicacao)
        criadas.append(publicacao)

    return criadas
