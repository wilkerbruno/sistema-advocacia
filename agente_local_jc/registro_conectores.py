"""
Registro dos conectores de tribunal disponíveis NESTA instalação do
Agente Local — casa com o slug escolhido na tela do processo dentro do
JusControl (ver app/utils/tribunais_conectores.py no repositório
principal). Adicionar um tribunal novo = escrever um conector novo em
conectores/ (implementando ConectorTribunalLocal, ver conector_base.py)
e registrar aqui uma função que sabe construí-lo a partir da
configuração local.

Hoje "pje_mni" e "projudi" estão implementados, os dois como piloto — ver
o aviso em conectores/pje_mni.py e conectores/projudi.py (a lógica de
consulta MNI que os dois compartilham mora em conectores/mni_soap.py).
"""
from conectores.pje_mni import ConectorPjeMni
from conectores.projudi import ConectorProjudi


def construir_conector(slug, config_tribunal):
    """
    `config_tribunal`: dict específico do tribunal (campos variam por
    conector — ver cada `conectores/<slug>.py`) — hoje lido de um bloco
    fixo no config.py; num piloto com mais de um tribunal cadastrado por
    conector, isso viraria uma lista configurável por processo/tribunal.
    """
    if slug == "pje_mni":
        return ConectorPjeMni(
            tribunal=config_tribunal.get("tribunal"),
            instancia=config_tribunal.get("instancia", "1g"),
            id_consultante=config_tribunal.get("id_consultante"),
            senha_consultante=config_tribunal.get("senha_consultante"),
            url_wsdl=config_tribunal.get("url_wsdl"),
        )
    if slug == "projudi":
        return ConectorProjudi(
            id_consultante=config_tribunal.get("id_consultante"),
            senha_consultante=config_tribunal.get("senha_consultante"),
            url_wsdl=config_tribunal.get("url_wsdl"),
        )
    raise ValueError(
        f"Conector '{slug}' ainda não está implementado neste agente local — "
        "veja agente_local_jc/README.md para o roadmap de tribunais."
    )
