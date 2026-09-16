"""
Captura da intimação por OAB do escritório (item 1 da lista de pipeline de
IA jurídica trazida pelo usuário — PENDENCIAS.md, seção -102): "DJEN e API
Comunica do CNJ por OAB do escritório, mais push do tribunal onde houver. O
input humano do onboarding é a OAB, não o processo."

Isso é ADITIVO ao cadastro por número CNJ que já existia (app/routes/
processos.py::novo, captura via DataJud) — os dois convivem: quem já sabe o
número do processo continua cadastrando do jeito de sempre; quem cadastra
uma OAB aqui passa a "descobrir" processos novos automaticamente, sem
precisar digitar nenhum número antes.

Dois modelos:

- `OabMonitorada`: a OAB (número + UF) que o escritório quer que o sistema
  fique consultando no DJEN/API Comunica. Um escritório real tem VÁRIOS
  advogados, cada um com a PRÓPRIA OAB — por isso é uma lista por unidade,
  não um campo único da empresa.
- `IntimacaoCapturada`: cada comunicação (intimação/citação) que a API
  Comunica devolveu pra alguma OAB monitorada. Guarda TODAS as capturadas
  (inclusive as vinculadas automaticamente) — é o registro de auditoria de
  "o que a OAB recebeu e o que o sistema fez com isso" (ver
  app/utils/captura_djen_pipeline.py para a lógica de vincular/triagem).
"""
from datetime import datetime
from app.extensions import db


class OabMonitorada(db.Model):
    __tablename__ = "oabs_monitoradas"
    __table_args__ = (
        db.UniqueConstraint("unidade_id", "numero", "uf", name="uq_oab_unidade_numero_uf"),
    )

    id = db.Column(db.Integer, primary_key=True)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    numero = db.Column(db.String(20), nullable=False)  # só dígitos (ver app/utils/cnj.py::somente_digitos)
    uf = db.Column(db.String(2), nullable=False)
    # Rótulo livre — "de quem é essa OAB" — só pra facilitar identificação
    # na tela, nunca usado pra autenticação nem vínculo de fato (isso é
    # numero+uf, que é o que a API Comunica realmente aceita).
    nome_advogado = db.Column(db.String(150))
    # Vínculo opcional com um Usuario do sistema (o próprio advogado dono
    # da OAB, se ele tiver login aqui) — só informativo, nunca obrigatório
    # (o escritório pode monitorar a OAB de um advogado que não usa o
    # sistema diretamente).
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])

    ativo = db.Column(db.Boolean, default=True)
    ultima_captura_em = db.Column(db.DateTime, nullable=True)
    ultimo_erro_captura = db.Column(db.String(500), nullable=True)

    # Segredo do webhook de push (ver app/routes/captacao_oab.py::
    # webhook_comunicacao e a ressalva de escopo no docstring de
    # app/utils/conector_djen.py) — gerado automaticamente no cadastro,
    # nunca escolhido pelo usuário, guardado em texto puro de propósito
    # (precisa ser comparado a cada chamada recebida de fora, diferente de
    # senha — mesmo raciocínio around ser só um "bearer" de baixa
    # sensibilidade, não uma credencial de acesso a dado de terceiro).
    token_webhook = db.Column(db.String(64), unique=True, index=True, nullable=True)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<OabMonitorada {self.numero}/{self.uf}>"


class IntimacaoCapturada(db.Model):
    """
    Uma comunicação (intimação/citação) devolvida pela API Comunica (ou
    recebida por push, quando o tribunal oferecer) para uma OAB monitorada.

    `status`:
      - "vinculada": o número do processo bateu com um Processo já
        cadastrado nesta empresa — vinculação AUTOMÁTICA (a maioria dos
        casos, quando o escritório já cadastrou o processo por CNJ antes
        ou em captura anterior). `processo_id`/`publicacao_id`/`prazo_id`
        ficam preenchidos, `vinculado_por_id` fica None (foi o robô).
      - "pendente_triagem": não achou nenhum processo com esse número
        nesta empresa — precisa de uma decisão humana (criar processo novo
        a partir daqui, vincular manualmente a um processo já cadastrado
        com número diferente/divergente, ou ignorar). Isso é exatamente o
        caso em que "o input humano é a OAB, não o processo": o processo
        em si só aparece quando uma intimação real chega pra ele.
      - "ignorada": humano decidiu que esta intimação não é deste
        escritório/não deve virar processo (`motivo_ignorada` obrigatório).
    """
    __tablename__ = "intimacoes_capturadas"

    STATUS = ("pendente_triagem", "vinculada", "ignorada")

    id = db.Column(db.Integer, primary_key=True)

    oab_monitorada_id = db.Column(db.Integer, db.ForeignKey("oabs_monitoradas.id"), nullable=False)
    oab_monitorada = db.relationship("OabMonitorada")

    # Denormalizado a partir de oab_monitorada.unidade_id — permite usar
    # aplicar_escopo_unidade(IntimacaoCapturada.query, IntimacaoCapturada)
    # direto, mesmo padrão do resto do sistema (ver app/utils/acesso.py).
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)

    # Identificador único da comunicação na fonte (hash/id da API Comunica,
    # ou o que o payload de push trouxer) — dedup entre execuções do cron
    # e entre polling/push da MESMA comunicação.
    id_comunicacao_fonte = db.Column(db.String(120), unique=True, index=True, nullable=False)

    numero_processo = db.Column(db.String(40))  # só dígitos, quando a fonte trouxe
    numero_processo_mascara = db.Column(db.String(40))
    tribunal = db.Column(db.String(60))
    orgao = db.Column(db.String(150))
    tipo_comunicacao = db.Column(db.String(60))
    tipo_documento = db.Column(db.String(60))
    meio = db.Column(db.String(30))  # "D" (diário eletrônico), "E" (edital)... conforme a fonte
    texto = db.Column(db.Text)
    destinatarios_texto = db.Column(db.Text)  # partes/advogados destinatários, já formatado em texto

    data_disponibilizacao = db.Column(db.Date)
    # Já calculada (1º dia útil seguinte à disponibilização — Lei
    # 11.419/2006, art. 4º, §3º; ver app/utils/prazos_engine.py) — é a
    # data que efetivamente conta pro prazo, não a de disponibilização.
    data_publicacao = db.Column(db.Date)
    link_certidao = db.Column(db.String(255))

    origem = db.Column(db.String(20), default="comunica_api")  # "comunica_api" ou "push_tribunal"

    status = db.Column(db.String(20), default="pendente_triagem")
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=True)
    processo = db.relationship("Processo")
    publicacao_id = db.Column(db.Integer, db.ForeignKey("publicacoes.id"), nullable=True)
    prazo_id = db.Column(db.Integer, db.ForeignKey("prazos.id"), nullable=True)

    motivo_ignorada = db.Column(db.String(255), nullable=True)
    vinculado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)  # None = automático
    vinculado_por = db.relationship("Usuario", foreign_keys=[vinculado_por_id])
    vinculado_em = db.Column(db.DateTime, nullable=True)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<IntimacaoCapturada {self.id} status={self.status}>"
