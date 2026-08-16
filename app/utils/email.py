"""
Envio de e-mail via SMTP — usado pelos lembretes de compromisso da Agenda
(ver enviar_lembretes_compromissos.py e app/models/compromisso.py).

Só envia de verdade se SMTP_HOST/SMTP_USER/SMTP_PASSWORD estiverem
configurados (ver config.py e .env.example) — mesmo padrão "degrada
honestamente sem credencial" usado em DATAJUD_API_KEY e no modelo de IA
local: sem SMTP configurado, `enviar_email()` só devolve False e quem
chamou decide o que fazer (aqui, o lembrete continua saindo por
notificação dentro do sistema, que não depende de nenhuma credencial
externa).

Nunca lança exceção pra fora — um e-mail que falha (servidor fora do ar,
credencial errada etc.) não pode derrubar o job de lembretes nem impedir
os próximos compromissos de serem processados.
"""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app


def smtp_configurado():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_USER") and cfg.get("SMTP_PASSWORD"))


def enviar_email(destinatario, assunto, corpo_texto):
    if not smtp_configurado() or not destinatario:
        return False

    cfg = current_app.config
    msg = MIMEMultipart()
    msg["From"] = cfg.get("SMTP_REMETENTE") or cfg["SMTP_USER"]
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))

    try:
        contexto = ssl.create_default_context()
        with smtplib.SMTP(cfg["SMTP_HOST"], int(cfg["SMTP_PORT"]), timeout=15) as servidor:
            servidor.starttls(context=contexto)
            servidor.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            servidor.sendmail(msg["From"], [destinatario], msg.as_string())
        return True
    except Exception as e:
        current_app.logger.warning(f"Falha ao enviar e-mail para {destinatario}: {e}")
        return False
