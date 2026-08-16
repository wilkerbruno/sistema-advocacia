import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def normalizar_url_mysql(url):
    """
    Aceita tanto 'mysql://user:pass@host:port/db' (formato comum, ex: o que
    o EasyPanel mostra na tela de conexão) quanto 'mysql+pymysql://...'
    (formato exigido pelo SQLAlchemy) e sempre devolve o segundo.
    """
    if url and url.startswith("mysql://"):
        url = url.replace("mysql://", "mysql+pymysql://", 1)
    if url and "charset=" not in url:
        url += ("&" if "?" in url else "?") + "charset=utf8mb4"
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao-por-uma-aleatoria")

    # Banco de dados MySQL
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = os.environ.get("DB_PORT", "3306")
    DB_NAME = os.environ.get("DB_NAME", "sistema_advocacia")

    _url_padrao = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    SQLALCHEMY_DATABASE_URI = normalizar_url_mysql(os.environ.get("DATABASE_URL", _url_padrao))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # Upload de documentos dos processos
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB por arquivo
    ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "jpg", "jpeg", "png", "xls", "xlsx", "txt"}

    # Sessão
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    REMEMBER_COOKIE_DURATION = timedelta(days=7)

    # Alertas de prazos (dias de antecedência para considerar "urgente")
    DIAS_ALERTA_PRAZO = 5

    # Cofre de senha de processo (seção 5.1 do briefing) — chave simétrica
    # usada para criptografar/descriptografar SenhaProcesso.valor_criptografado.
    # Em produção real, mover para um Vault/KMS dedicado; isso aqui é o
    # mínimo necessário para nunca gravar a senha em texto puro no banco.
    COFRE_SENHA_PROCESSO_KEY = os.environ.get("COFRE_SENHA_PROCESSO_KEY", "")

    # API de leitura autenticada para o Data Lake do escritório (seção 12).
    # Sem essa variável definida, a API /api/v1/* responde 503 (nunca abre
    # os dados sem token configurado).
    DATALAKE_API_TOKEN = os.environ.get("DATALAKE_API_TOKEN", "")

    # Envio do relatório semanal por e-mail (seção 10). Sem essas variáveis,
    # o script enviar_relatorio_semanal.py avisa e não tenta enviar.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_REMETENTE = os.environ.get("SMTP_REMETENTE", "")
    RELATORIO_SEMANAL_DESTINATARIOS = os.environ.get("RELATORIO_SEMANAL_DESTINATARIOS", "")  # e-mails separados por vírgula

    # Cobrança de licenças via Mercado Pago (Checkout Pro).
    MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN", "")

    # Preços padrão mostrados no cadastro público de empresa (self-service).
    # São só o "preço de tabela" inicial — o admin desenvolvedor pode
    # ajustar o valor de cada empresa depois em /plataforma/empresas/<id>/licenca,
    # sem que a empresa veja que existe negociação.
    PRECO_PADRAO_MENSAL = os.environ.get("PRECO_PADRAO_MENSAL", "199.90")
    PRECO_PADRAO_TRIMESTRAL = os.environ.get("PRECO_PADRAO_TRIMESTRAL", "539.90")
    PRECO_PADRAO_ANUAL = os.environ.get("PRECO_PADRAO_ANUAL", "1999.90")

    # Agentes de IA jurídica (Operação/Gestão/Negócios). Sem ANTHROPIC_API_KEY
    # definida, o agente responde de forma honesta que está indisponível —
    # nunca inventa resposta nem trava a tela do usuário.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
