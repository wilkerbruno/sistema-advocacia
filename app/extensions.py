from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para acessar o sistema."
login_manager.login_message_category = "warning"

# Proteção CSRF (ver PENDENCIAS.md seção -28 / AUDITORIA_GRANDE_PORTE.md
# item 1.2): protege todo POST/PUT/PATCH/DELETE do sistema, exigindo o
# token de app/templates/base.html (formulários) ou do header X-CSRFToken
# (chamadas fetch). Views chamadas por serviço externo (webhook de
# pagamento) são isentas explicitamente com @csrf.exempt — ver
# app/routes/licenciamento.py.
csrf = CSRFProtect()
