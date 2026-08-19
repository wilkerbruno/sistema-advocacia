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

    # ⚠️ DEPRECATED (correção de segurança, ver PENDENCIAS.md seção -28):
    # a API de leitura /api/v1/* (app/routes/api_integracao.py) usava um
    # único token global aqui, que dava acesso aos dados de TODAS as
    # empresas clientes da plataforma, sem filtro por empresa nenhum —
    # vazamento de dados entre clientes. Essa variável NÃO É MAIS LIDA em
    # lugar nenhum do código; os tokens agora são um por empresa,
    # gerados em /plataforma/empresas/<id> (tabela `tokens_integracao`).
    # Mantida aqui só como lembrete para você remover do .env quando
    # puder — não faz mais nada.

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

    # Agentes de IA jurídica (Operação/Gestão/Negócios) e Análise de processo
    # (resumo dos autos / rascunho de petição) — modelo local pequeno (até 2B
    # parâmetros, Qwen2.5-1.5B-Instruct em GGUF), rodando dentro do próprio
    # servidor via llama-cpp-python (ver app/utils/ia_local.py). Sem chave
    # de API, sem custo por mensagem, sem dado saindo do servidor. Sem o
    # arquivo de pesos baixado (roda sozinho durante o build da imagem
    # Docker), o agente responde de forma honesta que está indisponível —
    # nunca inventa resposta.
    #
    # Existe um modelo maior/mais robusto pronto para uso (Qwen3-4B, ~2,5
    # GB) em baixar_modelo_ia_local.py, mas ele fica DESLIGADO por padrão —
    # checamos o painel de recursos do servidor em produção (EasyPanel) e a
    # RAM já estava em ~74% de uso antes de qualquer coisa da IA, sem folga
    # para o modelo maior nos 2 workers do gunicorn. Ver PENDENCIAS.md,
    # seção -6, para o passo a passo de como ativar o modelo maior quando
    # (se) o plano do servidor tiver mais RAM.
    IA_LOCAL_MODELO_PATH = os.environ.get(
        "IA_LOCAL_MODELO_PATH",
        os.path.join(BASE_DIR, "app", "ia_local", "modelos", "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
    )
    # 4096 é o padrão seguro para o modelo pequeno. A Análise de processo por
    # IA (resumo dos autos / rascunho de petição, ver
    # app/utils/analise_processo_ia.py) já ajusta sozinha o tamanho do
    # digest do processo a este limite — se um dia trocar para o modelo
    # maior com mais RAM sobrando, pode valer a pena subir este valor (ver
    # PENDENCIAS.md, seção -6).
    IA_LOCAL_CONTEXT_SIZE = int(os.environ.get("IA_LOCAL_CONTEXT_SIZE", "4096"))
    IA_LOCAL_MAX_TOKENS_RESPOSTA = int(os.environ.get("IA_LOCAL_MAX_TOKENS_RESPOSTA", "700"))
    IA_LOCAL_THREADS = int(os.environ["IA_LOCAL_THREADS"]) if os.environ.get("IA_LOCAL_THREADS") else None

    # Captura automática de movimentações via DataJud (API pública e
    # gratuita do CNJ) — ver app/utils/conector_datajud.py. Sem isso
    # definido, o cadastro por CNJ (/governanca/processos/novo-por-cnj)
    # continua funcionando, mas todo processo é marcado honestamente como
    # "não monitorável automaticamente". Cadastro gratuito da chave em
    # https://datajud-wiki.cnj.jus.br/
    DATAJUD_API_KEY = os.environ.get("DATAJUD_API_KEY", "")

    # Lembrete de compromisso da Agenda por WhatsApp, via WAHA
    # (https://waha.devlike.pro — ver app/utils/whatsapp.py e PENDENCIAS.md,
    # seção -4, para o passo a passo completo de deploy no EasyPanel).
    # Automação NÃO-OFICIAL, decisão explícita (não é a API paga da Meta),
    # ciente do risco de banimento do número usado.
    # WHATSAPP_BRIDGE_URL: URL do serviço WAHA (segundo serviço no mesmo
    # projeto do EasyPanel) — ex: "http://waha:3000" (endereço interno) ou
    # a URL pública que o EasyPanel atribuiu ao serviço.
    # WHATSAPP_BRIDGE_TOKEN: mesmo valor definido em WAHA_API_KEY na
    # configuração do serviço WAHA (usado no header X-Api-Key).
    # Sem WHATSAPP_BRIDGE_URL definida, o lembrete de compromisso continua
    # saindo normalmente por notificação no sistema e e-mail — só o
    # WhatsApp fica desativado, nunca falha silenciosamente.
    WHATSAPP_BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "")
    WHATSAPP_BRIDGE_TOKEN = os.environ.get("WHATSAPP_BRIDGE_TOKEN", "")

    # Legado: chave da Anthropic (Claude), não é mais usada pelo Agente de IA
    # desde que ele passou a rodar no modelo local acima. Mantida só para
    # facilitar reverter, se um dia quiser voltar a usar uma API de ponta em
    # vez do modelo local — ver histórico do git em app/routes/agente_ia.py.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
