"""
Vigilância do Diário Oficial da União (DOU) — pedido do usuário (2026-09):
"seria possível a gente vincular o diário oficial da união no sistema para
melhorar ainda mais a entrega do sistema". Ver PENDENCIAS.md (seção mais
recente) para a pesquisa completa (INLABS, Ro-DOU) e as decisões tomadas.

⚠️ Diferente da captura por OAB (app/models/captacao_oab.py — DJEN/API
Comunica, INTIMAÇÕES processuais): o DOU é o diário do governo federal
(leis, decretos, portarias, atos de pessoal, contratos e licitações), não
publica intimação de processo nenhuma. Este módulo NUNCA vincula a um
Processo — é um alerta de "seu cliente (ou um termo de interesse do
escritório) apareceu numa publicação oficial do governo federal hoje",
pra revisão humana; o advogado decide o que fazer com essa informação.

Dois modelos:

- `PalavraChaveDou`: termos de interesse cadastrados manualmente pelo
  escritório (ex.: uma lei específica, um órgão, uma área de atuação) —
  além do monitoramento AUTOMÁTICO por nome/CNPJ dos clientes já
  cadastrados (não precisa de tabela pra isso, lê direto de `Cliente`).
- `PublicacaoDouCapturada`: cada matéria do DOU que bateu com um cliente
  ou uma palavra-chave, numa captura diária. Guarda só um TRECHO da
  matéria (nunca o Diário inteiro) — ver app/utils/captura_dou_pipeline.py.
"""
from datetime import datetime
from app.extensions import db


class PalavraChaveDou(db.Model):
    __tablename__ = "palavras_chave_dou"
    __table_args__ = (
        db.UniqueConstraint("unidade_id", "termo", name="uq_palavra_chave_dou_unidade_termo"),
    )

    id = db.Column(db.Integer, primary_key=True)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    # Guardado exatamente como digitado (pra exibir na tela); a comparação
    # de verdade usa uma versão normalizada (minúsculo, sem acento — ver
    # app/utils/captura_dou_pipeline.py::_normalizar), nunca este campo cru.
    termo = db.Column(db.String(150), nullable=False)
    ativa = db.Column(db.Boolean, default=True, nullable=False)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<PalavraChaveDou {self.termo!r} unidade={self.unidade_id}>"


class PublicacaoDouCapturada(db.Model):
    """
    `status`:
      - "pendente_revisao": recém-capturada, ninguém olhou ainda.
      - "lida": um humano já conferiu (não precisa de nenhuma ação — só
        "marcar como vista", bem mais leve que a triagem de intimação).
      - "ignorada": marcada como irrelevante (ex.: falso positivo de nome
        homônimo — comum sobretudo em cliente pessoa física, ver docstring
        de app/utils/captura_dou_pipeline.py).
    """
    __tablename__ = "publicacoes_dou_capturadas"
    __table_args__ = (
        # Mesma matéria pode legitimamente bater com MAIS de um termo desta
        # unidade (ex.: cita dois clientes diferentes do escritório) — isso
        # vira duas linhas, cada uma com seu próprio termo_encontrado. O que
        # nunca pode duplicar é a MESMA matéria pelo MESMO termo (ex.:
        # reprocessamento do mesmo dia).
        db.UniqueConstraint("unidade_id", "id_materia_fonte", "termo_encontrado",
                             name="uq_publicacao_dou_unidade_materia_termo"),
    )

    STATUS = ("pendente_revisao", "lida", "ignorada")
    # Códigos aceitos pelo INLABS (ver app/utils/conector_inlabs_dou.py) —
    # guardado cru aqui; SECOES_LEGIVEL abaixo só formata pra exibição.
    SECOES_LEGIVEL = {
        "DO1": "Seção 1 — Leis e decretos",
        "DO2": "Seção 2 — Atos de pessoal",
        "DO3": "Seção 3 — Contratos e licitações",
        "DO1E": "Seção 1 — Extra",
        "DO2E": "Seção 2 — Extra",
        "DO3E": "Seção 3 — Extra",
    }

    id = db.Column(db.Integer, primary_key=True)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    # id numérico da matéria na fonte (atributo "id" do <article> do INLABS)
    # — usado pra dedup entre execuções (mesmo motivo de
    # IntimacaoCapturada.id_comunicacao_fonte).
    id_materia_fonte = db.Column(db.String(30), nullable=False)

    secao = db.Column(db.String(10), nullable=False)  # ver SECOES_LEGIVEL acima
    orgao = db.Column(db.String(255))
    titulo = db.Column(db.String(500))
    ementa = db.Column(db.Text)
    # Só o trecho ao redor do termo encontrado (ver
    # app/utils/captura_dou_pipeline.py::_montar_trecho) — nunca a matéria
    # inteira; o Diário Oficial não precisa ser replicado dentro deste
    # sistema, só o suficiente pra dar contexto de por que isto apareceu
    # aqui e permitir decidir se vale abrir o link original.
    texto_trecho = db.Column(db.Text)
    data_publicacao = db.Column(db.Date)
    # Link pro PDF da página/matéria no site oficial, quando dá pra montar
    # (ver conector) — nullable porque nem sempre a fonte devolve dado
    # suficiente pra montar um link confiável; nesse caso o trecho acima é
    # a única informação disponível mesmo.
    link_pdf = db.Column(db.String(500))

    # O termo (nome/CNPJ de cliente, ou palavra-chave cadastrada) que bateu
    # nesta matéria — guardado como texto solto (não só uma FK) porque
    # continua fazendo sentido mesmo se o cliente for renomeado ou a
    # palavra-chave for apagada depois.
    termo_encontrado = db.Column(db.String(150), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True)  # só quando bateu por cliente
    cliente = db.relationship("Cliente")

    status = db.Column(db.String(20), default="pendente_revisao")
    motivo_ignorada = db.Column(db.String(255), nullable=True)
    revisado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    revisado_por = db.relationship("Usuario")
    revisado_em = db.Column(db.DateTime, nullable=True)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def secao_legivel(self):
        return self.SECOES_LEGIVEL.get(self.secao, self.secao)

    def __repr__(self):
        return f"<PublicacaoDouCapturada {self.id} unidade={self.unidade_id} termo={self.termo_encontrado!r}>"
