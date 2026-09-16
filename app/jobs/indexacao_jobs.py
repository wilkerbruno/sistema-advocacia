"""
Job de indexação de documento (item 3 — PENDENCIAS.md, seção -103), rodando
em segundo plano via RQ (ver app/utils/fila.py e app/jobs/ia_jobs.py para o
mesmo padrão já usado pela geração de IA) — nunca dentro do ciclo de
requisição/resposta do upload: um documento de muitas páginas com OCR pode
levar minutos, e travar o worker do gunicorn até terminar seria exatamente
o mesmo problema que a fila de IA já resolveu para a geração de análise.
"""
from app import create_app
from app.extensions import db

_app = None


def _obter_app():
    global _app
    if _app is None:
        _app = create_app()
    return _app


def indexar_documento_job(documento_id):
    """
    Indexa um `Documento` já salvo em disco (upload manual, ver
    app/routes/processos.py::add_documento, ou entregue pelo Agente Local,
    ver app/routes/agente_local_api.py::enviar_resultado). Enfileirado logo
    após o `db.session.commit()` que cria o Documento — nunca antes, pra
    garantir que o worker (processo separado) já encontra a linha no banco.
    """
    app = _obter_app()
    with app.app_context():
        from app.models import Documento
        from app.utils.indexacao_documentos import indexar_documento

        documento = db.session.get(Documento, documento_id)
        if documento is None:
            return  # documento apagado enquanto o job esperava na fila — nada a fazer

        try:
            indexar_documento(documento, app.config["UPLOAD_FOLDER"])
        except Exception as e:  # nunca deixa o job travado/o worker derrubado por um bug de indexação
            import sentry_sdk
            sentry_sdk.capture_exception(e)
            documento.erro_indexacao = f"Erro inesperado na indexação: {e}"[:500]
            from datetime import datetime
            documento.indexado_em = datetime.utcnow()

        db.session.commit()
