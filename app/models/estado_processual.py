from datetime import datetime
from app.extensions import db


class MapaEstadoTPU(db.Model):
    """
    Mapa código TPU/CNJ -> estado de negócio, editável por interface
    (nunca hardcoded — tribunal cria movimentação nova o tempo todo).

    Estados de negócio sugeridos pelo briefing (cível):
    Distribuído, Aguardando citação, Citado / prazo de resposta,
    Em instrução, Aguardando sentença, Sentenciado, Em fase recursal,
    Trânsito em julgado, Em cumprimento/execução, Arquivado.

    Estados adicionais (execução fiscal / administrativo):
    Citado em execução, Garantido o juízo, Embargos opostos,
    Auto de infração, Recurso administrativo pendente.

    Alguns códigos da Tabela Processual Unificada são genéricos demais para
    carregar sozinhos o significado real do ato — o exemplo mais comum é
    "Ato ordinatório" (código 11383), usado por muitos tribunais como
    guarda-chuva para qualquer expediente de mero impulso processual, sem
    diferenciar o que de fato aconteceu (intimação, remessa dos autos,
    juntada de petição etc. podem todos cair no mesmo código). Pra esses
    casos, `texto_contido` permite mapear pelo CONTEÚDO real do ato (um
    trecho de texto a procurar, sem diferenciar maiúsculas/minúsculas, igual
    já funciona em RegraProximaAcao.ato_capturado) em vez de depender só do
    código — ver app/utils/estado_processual_engine.py.
    """
    __tablename__ = "mapa_estado_tpu"

    id = db.Column(db.Integer, primary_key=True)
    # Opcional a partir de quando texto_contido passou a existir: um código
    # como "Ato ordinatório" (11383) sozinho não diferencia atos bem
    # diferentes entre si, então pode fazer sentido ter várias linhas SEM
    # código (codigo_tpu=None), cada uma casando por um texto_contido
    # diferente. MySQL permite múltiplos NULL num índice único, então isso
    # não conflita com o unique= abaixo (que segue impedindo cadastrar o
    # MESMO código duas vezes).
    codigo_tpu = db.Column(db.String(20), unique=True, nullable=True, index=True)
    descricao_tpu = db.Column(db.String(255))  # texto original do código (ex: "Conclusos para despacho")
    # Trecho de texto (opcional) a procurar no texto integral da movimentação
    # quando o código sozinho não for suficiente/confiável (ex.: "Ato
    # ordinatório") — só é consultado quando não há mapa pelo código exato.
    # Cadastro exige pelo menos um dos dois (codigo_tpu ou texto_contido).
    texto_contido = db.Column(db.String(150), nullable=True)
    estado_negocio = db.Column(db.String(60), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HistoricoEstadoProcesso(db.Model):
    """
    Cada mudança de estado gera evento datado — permite medir tempo em
    cada fase (idade média da carteira / tempo médio por fase, seção 9).
    """
    __tablename__ = "historico_estado_processo"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="historico_estados")

    estado_negocio = db.Column(db.String(60), nullable=False)
    data_evento = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    origem_movimentacao_id = db.Column(db.Integer, db.ForeignKey("movimentacoes.id"), nullable=True)


class RegraProximaAcao(db.Model):
    """
    Motor de próxima ação exigida (seção 7.1) — tabela de regras editável
    que liga o ato capturado à ação exigida, ao prazo legal base e ao
    responsável sugerido. Nunca hardcoded no código da aplicação.
    """
    __tablename__ = "regras_proxima_acao"

    id = db.Column(db.Integer, primary_key=True)
    ato_capturado = db.Column(db.String(150), nullable=False)
    codigo_tpu = db.Column(db.String(20), index=True, nullable=True)
    acao_exigida = db.Column(db.String(255), nullable=False)
    prazo_base_dias = db.Column(db.Integer)  # nulo quando o prazo é "conforme despacho" ou data de evento
    unidade_prazo = db.Column(db.String(20), default="dias_uteis")  # dias_uteis, dias_corridos, data_evento
    observacao_prazo = db.Column(db.String(120))  # ex: "conforme despacho", "data da audiência"
    responsavel_sugerido_papel = db.Column(db.String(20))  # advogado, gestor...
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
