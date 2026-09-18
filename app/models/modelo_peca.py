from datetime import datetime
from app.extensions import db


class ModeloPeca(db.Model):
    """
    Biblioteca de modelos de peças do escritório (item 10 da lista de
    pipeline de IA jurídica trazida pelo usuário — PENDENCIAS.md, seção
    -107): "Biblioteca de modelos por tipo de peça e área, com estilo e
    cláusulas do escritório aprendidos das peças anteriores."

    Escopo: por EMPRESA inteira (todas as unidades), nunca por unidade —
    mesma decisão de design já usada para o timbrado (ver
    app/utils/timbrado.py): mais simples de configurar, e a maioria dos
    escritórios cliente tem um jeito só de escrever, não um por filial.

    "Aprendidos das peças anteriores" aqui significa: um admin do
    escritório CADASTRA o esqueleto/cláusulas padrão que o escritório
    de fato usa (colado de uma peça real já usada, ou escrito à mão) —
    não há aprendizado automático por machine learning nenhum lendo o
    histórico sozinho (isso exigiria treinar um modelo, fora do escopo
    deste sistema). Uma vez cadastrado, porém, o modelo é aplicado
    SOZINHO daí em diante em toda geração de rascunho que casar o
    tipo_peca (e a área, quando informada) — diferente do
    `documento_referencia_id` em AnaliseProcessoIA, que é escolhido
    manualmente a cada geração. É essa aplicação automática e reutilizável
    que separa "biblioteca" de "anexar um documento de referência
    avulso".

    `tipo_peca` é texto livre (não fica preso aos dois valores hoje
    hardcoded em TIPOS_PECA_COM_DOSSIE — contestação/recurso são só os
    que ganham DOSSIÊ automático de documentos; a biblioteca de modelos
    vale para qualquer tipo de peça que o escritório queira cadastrar,
    ex.: "réplica", "embargos de declaração", "petição inicial
    trabalhista").

    `area_direito` opcional: None/vazio = "serve para qualquer área"
    (aplicado quando não existe um modelo mais específico casando a área
    exata do processo — ver app/utils/analise_processo_ia.py, resolução
    feita em app/routes/processos.py::gerar_analise_ia). Texto livre,
    igual Processo.area_direito (não é um enum fechado no banco).

    `conteudo` é o texto do modelo em si — esqueleto de seções, cláusulas
    padrão, jeito de escrever que o escritório usa. Nunca entra no
    "digest" usado por `_checar_grounding` (mesma regra do
    `texto_referencia` avulso) — é só estilo/estrutura, nunca fato do
    processo.
    """
    __tablename__ = "modelos_peca"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)
    empresa = db.relationship("Empresa")

    nome = db.Column(db.String(150), nullable=False)
    tipo_peca = db.Column(db.String(60), nullable=False, index=True)
    area_direito = db.Column(db.String(60), nullable=True)  # None = qualquer área
    conteudo = db.Column(db.Text, nullable=False)

    ativo = db.Column(db.Boolean, default=True)
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    criado_por = db.relationship("Usuario")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ModeloPeca {self.tipo_peca}/{self.area_direito or '*'} \"{self.nome}\">"


def resolver_modelo_peca(empresa_id, tipo_peca, area_direito):
    """
    Escolhe automaticamente qual ModeloPeca aplicar numa geração — "biblioteca
    aprendida" quer dizer isto: o advogado não precisa escolher nada, o
    sistema já sabe qual modelo usar pra este tipo de peça + área.

    Prioridade: modelo ATIVO da MESMA empresa cujo tipo_peca bate
    exatamente, preferindo primeiro um que também bata a área exata do
    processo e, na falta desse, um cadastrado sem área (serve pra
    qualquer área). Quando mais de um modelo empatar no mesmo nível de
    prioridade, usa o mais recentemente atualizado (presume-se o mais
    revisado/confiável). Devolve None quando nada casa — gerar sem modelo
    de escritório continua funcionando normalmente, só sem esse reforço de
    estilo (nunca bloqueia a geração).
    """
    if not tipo_peca:
        return None

    base = ModeloPeca.query.filter_by(empresa_id=empresa_id, tipo_peca=tipo_peca, ativo=True)

    if area_direito:
        especifico = base.filter_by(area_direito=area_direito).order_by(ModeloPeca.atualizado_em.desc()).first()
        if especifico:
            return especifico

    return base.filter_by(area_direito=None).order_by(ModeloPeca.atualizado_em.desc()).first()
