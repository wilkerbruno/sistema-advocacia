"""
Ingestão e indexação de documentos (item 3 da lista de pipeline de IA
jurídica — PENDENCIAS.md, seção -103): "OCR, índice vetorial, corte por
evento". Um `Documento` já anexado a um processo (upload manual ou trazido
pelo Agente Local — `categoria="autos_completo_agente_local"`, ver
app/models/agente_local.py) é indexado em pedaços (`DocumentoIndexado`) —
cada pedaço é um evento discreto e delimitado (uma página de PDF, ou um
trecho dela quando a página é grande — nunca um corte cego no meio de uma
frase), nunca o documento inteiro jogado cru num prompt.

Ver app/utils/indexacao_documentos.py para o pipeline de extração/OCR/
chunking/embedding, e app/utils/analise_processo_ia.py para onde isso é
usado (busca por similaridade em vez do corte por caractere bruto que
existia antes).

⚠️ Honestidade de escopo: o `embedding` é preenchido por um de DOIS
provedores possíveis (PENDENCIAS.md, seção -123) — a chave do Gemini da
empresa (BYOK, ver app/utils/gemini_api.py), quando cadastrada, OU o
modelo local (app/utils/ia_local.py::gerar_embeddings_lote, sem BYOK, sem
custo), quando o arquivo de pesos foi baixado no servidor; Gemini sempre
tem prioridade quando a empresa tem chave. Sem NENHUM dos dois disponível,
os chunks ainda são gerados e indexados (o "corte por evento" já ajuda por
si só), só a BUSCA por similaridade semântica é que fica indisponível —
nesse caso `app/utils/indexacao_documentos.py::buscar_trechos_relevantes`
cai para os chunks mais recentes, nunca quebra nem finge uma busca que não
rodou. `embedding_modelo` guarda de qual dos dois (e de qual arquivo/
versão, no caso do local) cada vetor veio — nunca compara vetores de
proveniências diferentes entre si.
"""
from datetime import datetime

from app.extensions import db


class DocumentoIndexado(db.Model):
    __tablename__ = "documentos_indexados"

    ORIGENS_TEXTO = ("camada_pdf", "ocr", "docx", "txt")

    id = db.Column(db.Integer, primary_key=True)

    documento_id = db.Column(db.Integer, db.ForeignKey("documentos.id"), nullable=False)
    documento = db.relationship("Documento", backref=db.backref("chunks_indexados", lazy="dynamic"))

    # Denormalizado de propósito (mesmo padrão de IntimacaoCapturada.unidade_id,
    # PENDENCIAS.md seção -102): permite consultar/escopar por processo sem
    # precisar de join com Documento em toda query de busca semântica.
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo")

    pagina = db.Column(db.Integer, nullable=True)  # 1-based; nulo pra docx/txt (documento sem paginação)
    ordem = db.Column(db.Integer, nullable=False, default=0)  # ordem estável dentro do documento

    texto = db.Column(db.Text, nullable=False)
    origem_texto = db.Column(db.String(20), nullable=False)  # ver ORIGENS_TEXTO acima

    # JSON-encoded list[float] — guardado como TEXT (não um tipo vetorial
    # nativo) de propósito: o banco de produção é MySQL comum (ver
    # config.py), sem extensão de vetor instalada; a similaridade é
    # calculada em Python/numpy no momento da busca (ver
    # buscar_trechos_relevantes) — não escala pra milhões de chunks, mas é
    # suficiente pro volume real de um escritório (documentos de alguns
    # processos por vez, não uma base de bilhões de vetores).
    embedding = db.Column(db.Text, nullable=True)
    embedding_modelo = db.Column(db.String(60), nullable=True)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
