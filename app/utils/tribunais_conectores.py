"""
Registro dos conectores de tribunal que o Agente Local do JusControl
(pasta agente_local_jc/ na raiz do repositório — roda na máquina do
próprio advogado, nunca no servidor) sabe ou vai saber atender — ver
app/models/agente_local.py para o desenho completo e PENDENCIAS.md
seção -56.

Cada entrada aqui é só um RÓTULO (slug + nome de exibição) que a tela do
processo oferece pro advogado escolher na hora de pedir "buscar autos
completos" — o slug escolhido vira `SolicitacaoBuscaAutos.tribunal_conector`.
O CÓDIGO de cada conector (a parte que fala de verdade com o tribunal,
via SOAP/MNI ou outro protocolo) mora inteiramente em
agente_local_jc/conectores/, não aqui — o servidor da JusControl NUNCA
faz nenhuma chamada a nenhum tribunal para essa funcionalidade, só
recebe o resultado já pronto que o agente local do advogado manda.

Arquitetura pensada para MÚLTIPLOS tribunais desde o início (pedido
explícito do cliente: "preciso que o agente busque processos de vários
tribunais, pois os advogados trabalham com diferentes tribunais") —
adicionar um tribunal novo é: implementar um conector novo em
agente_local_jc/conectores/ + acrescentar uma linha no dicionário
abaixo + no conjunto CONECTORES_IMPLEMENTADOS. Por enquanto "pje_mni",
"projudi" e "esaj_sp" têm conector implementado, e os três são PILOTOS
ainda não testados contra nenhum tribunal real (ver
agente_local_jc/README.md) — o "esaj_sp" é o mais incerto dos três: não
há confirmação de que o e-SAJ (TJMS, TJSP e outros) realmente expõe o
mesmo tipo de webservice que o PJe/Projudi (ver aviso em
agente_local_jc/conectores/esaj.py) — os demais aparecem na lista só
para deixar visível, desde já, que a tela e o modelo de dados não são
amarrados a um tribunal só.
"""

TRIBUNAIS_CONECTORES = {
    "pje_mni": "PJe — via MNI/SOAP (piloto, requer certificado A1 e ainda não foi testado contra um tribunal real)",
    "projudi": "Projudi — via MNI/SOAP (piloto, requer certificado A1 e URL do WSDL do tribunal; ainda não foi testado contra um tribunal real)",
    "esaj_sp": "e-SAJ (TJSP, TJMS e outros tribunais estaduais) — tentativa via MNI/SOAP (piloto MAIS incerto que os outros — não há confirmação de que o e-SAJ exponha esse serviço; ainda não foi testado contra um tribunal real)",
    # Fast-follow — ainda sem conector implementado, listado só para deixar
    # claro que o desenho é multi-tribunal desde o início:
    "eproc": "e-Proc (usado por vários TRFs, TJs e TRTs) — conector ainda não implementado",
}

CONECTORES_IMPLEMENTADOS = {"pje_mni", "projudi", "esaj_sp"}


def opcoes_para_formulario():
    """
    Lista (slug, rótulo, implementado) para preencher o <select> da tela
    de "Buscar autos completos" — conectores implementados aparecem
    primeiro, os demais ficam desabilitados no template (mostrados só
    para transparência do roadmap, não podem ser escolhidos ainda).
    """
    itens = [(slug, rotulo, slug in CONECTORES_IMPLEMENTADOS) for slug, rotulo in TRIBUNAIS_CONECTORES.items()]
    return sorted(itens, key=lambda item: (not item[2], item[1]))


def rotulo_do_conector(slug):
    return TRIBUNAIS_CONECTORES.get(slug, slug)
