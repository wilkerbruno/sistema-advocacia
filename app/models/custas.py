from datetime import datetime
from app.extensions import db


class TabelaCustas(db.Model):
    """
    Tabela de custas judiciais (item 9 da lista de pipeline de IA jurídica
    trazida pelo usuário — PENDENCIAS.md, seção -108): "Base de cálculo,
    valor da causa, custas, preparo e guias quando aplicável, com memória
    de cálculo aberta para conferência."

    Mesmo padrão de RegraProximaAcao/MapaEstadoTPU (app/models/
    estado_processual.py): tabela GLOBAL (não por empresa — é regra de
    tribunal, um fato objetivo da lei/regimento de custas, não estilo do
    escritório, ao contrário de ModeloPeca), editável por interface, NUNCA
    hardcoded no código, admin confere/mantém atualizada. Cadastrar um
    valor errado aqui é grave (risco real de recolhimento a menor/maior) —
    por isso o sistema não vem com nada pré-calculado escondido no código,
    só o botão explícito "carregar tabela padrão TJSP" (ver
    app/utils/calculo_custas.py::TABELA_PADRAO_TJSP) que insere linhas já
    revisáveis/editáveis, nunca aplicadas às cegas.

    Dois formatos de regra, ambos cabem neste mesmo modelo flat (sem JSON
    aninhado, pra ficar fácil de editar linha a linha pela tela, mesmo
    espírito das outras tabelas de regra do sistema):

    - Percentual sobre um valor base (ex.: distribuição cível = 1,5% do
      valor da causa) — usa `percentual`, com piso/teto opcionais em
      `valor_minimo`/`valor_maximo`.
    - Valor fixo (ex.: porte de remessa e retorno por volume) — usa
      `valor_fixo` sozinho (percentual nulo).
    - Faixas (ex.: custas de inventário por faixa de valor da partilha):
      várias linhas com o MESMO `tribunal`+`tipo_custa`, cada uma com um
      `faixa_ate` diferente (o valor-limite da causa pra esta linha se
      aplicar) e seu próprio `valor_fixo` — a linha com `faixa_ate=None`
      é a faixa "sem teto" (valor acima de todas as outras). Ver
      app/utils/calculo_custas.py::calcular_custa pra como isso é
      resolvido em tempo de cálculo.
    """
    __tablename__ = "tabela_custas"

    id = db.Column(db.Integer, primary_key=True)
    tribunal = db.Column(db.String(30), nullable=False, index=True)
    # Categoria da custa — texto livre (não é um enum fechado no banco),
    # mesmo espírito de RegraProximaAcao.ato_capturado. Ex.: distribuicao_civel,
    # execucao_titulo_extrajudicial, preparo_recurso, agravo_instrumento,
    # porte_remessa_retorno, inventario_partilha...
    tipo_custa = db.Column(db.String(60), nullable=False, index=True)
    descricao = db.Column(db.String(200), nullable=False)

    percentual = db.Column(db.Numeric(6, 3), nullable=True)  # ex.: 1.500 = 1,5%
    valor_fixo = db.Column(db.Numeric(12, 2), nullable=True)
    valor_minimo = db.Column(db.Numeric(12, 2), nullable=True)
    valor_maximo = db.Column(db.Numeric(12, 2), nullable=True)
    # Só usado no formato "faixas" (ver docstring acima) — None = faixa sem
    # teto (valor acima de todas as demais faixas cadastradas pro mesmo
    # tribunal+tipo_custa).
    faixa_ate = db.Column(db.Numeric(14, 2), nullable=True)

    # Base legal, data de referência da UFESP/índice usado, e qualquer
    # ressalva — sempre texto livre, nunca escondido: quem for usar o
    # cálculo pra cobrar do cliente ou recolher a guia precisa poder
    # conferir a fonte, não só confiar no número.
    observacao = db.Column(db.String(400), nullable=True)

    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<TabelaCustas {self.tribunal}/{self.tipo_custa} \"{self.descricao}\">"


class CalculoCustas(db.Model):
    """
    Memória de cálculo (item 9 — PENDENCIAS.md, seção -108): "com memória
    de cálculo aberta para conferência". Cada cálculo feito pra um
    processo fica registrado aqui — nunca sobrescreve o anterior (mesmo
    espírito de DelimitacaoObjeto: histórico completo de auditoria, um
    processo pode calcular custas várias vezes ao longo do tempo, ex.:
    distribuição, depois preparo de recurso meses depois).

    `memoria_calculo` é texto livre, linha a linha, mostrando EXATAMENTE
    como o valor foi obtido (valor base usado, percentual/regra aplicada,
    piso/teto se algum foi aplicado, base legal) — pensado pra ser lido e
    conferido por um humano antes de recolher a guia de verdade, nunca
    "confie no sistema" às cegas.
    """
    __tablename__ = "calculos_custas"

    id = db.Column(db.Integer, primary_key=True)

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo")

    # Nullable: a regra pode ter sido desativada/excluída depois do
    # cálculo — a memória de texto já salva continua valendo como
    # registro histórico independente da linha da tabela ainda existir.
    tabela_custas_id = db.Column(db.Integer, db.ForeignKey("tabela_custas.id"), nullable=True)
    tabela_custas = db.relationship("TabelaCustas")

    tribunal = db.Column(db.String(30), nullable=False)
    tipo_custa = db.Column(db.String(60), nullable=False)
    descricao_custa = db.Column(db.String(200), nullable=False)  # cópia da descrição no momento do cálculo
    valor_base = db.Column(db.Numeric(14, 2), nullable=True)  # valor da causa/partilha usado, quando aplicável
    quantidade = db.Column(db.Integer, default=1)
    valor_calculado = db.Column(db.Numeric(12, 2), nullable=False)
    memoria_calculo = db.Column(db.Text, nullable=False)

    calculado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    calculado_por = db.relationship("Usuario")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<CalculoCustas {self.id} processo={self.processo_id} {self.tipo_custa}=R${self.valor_calculado}>"
