"""
Gera e envia o relatório semanal de governança por e-mail (seção 10 do
briefing). Pensado para rodar via cron/agendador do sistema operacional do
servidor — este projeto não tem fila assíncrona (Celery beat) provisionada,
então "agendar" aqui significa uma linha de crontab, não um job interno.

Uso:
    python enviar_relatorio_semanal.py            # gera e envia
    python enviar_relatorio_semanal.py --testar    # gera e imprime no
                                                     # console, não envia

Exemplo de crontab (toda segunda-feira às 7h):
    0 7 * * 1 cd /caminho/do/projeto && /caminho/do/venv/bin/python enviar_relatorio_semanal.py >> /var/log/relatorio_semanal.log 2>&1

Configuração necessária no .env (ver .env.example):
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_REMETENTE,
    RELATORIO_SEMANAL_DESTINATARIOS (e-mails separados por vírgula)

Sem essas variáveis preenchidas, o script avisa e não tenta enviar —
nunca falha silenciosamente nem manda e-mail incompleto.
"""
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db
from app.models import Processo, Prazo, Movimentacao

TESTAR = "--testar" in sys.argv


def montar_relatorio():
    hoje = date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    semana_passada_inicio = inicio_semana - timedelta(days=7)

    prazos_da_semana = Prazo.query.filter(
        Prazo.deletado_em.is_(None),
        Prazo.data_vencimento.between(inicio_semana, fim_semana),
        Prazo.status != "cumprido",
    ).order_by(Prazo.data_vencimento).all()

    prazos_perdidos_semana_passada = Prazo.query.filter(
        Prazo.deletado_em.is_(None), Prazo.status == "perdido",
        Prazo.data_vencimento.between(semana_passada_inicio, inicio_semana - timedelta(days=1)),
    ).all()

    limite_30 = datetime.utcnow() - timedelta(days=30)
    processos_parados = Processo.query.filter(
        Processo.status == "ativo",
        db.or_(Processo.ultima_movimentacao_em.is_(None), Processo.ultima_movimentacao_em <= limite_30),
    ).limit(20).all()

    linhas = [f"Relatório semanal de governança — {inicio_semana.strftime('%d/%m')} a {fim_semana.strftime('%d/%m/%Y')}", ""]

    linhas.append(f"PRAZOS DESTA SEMANA ({len(prazos_da_semana)})")
    for p in prazos_da_semana:
        linhas.append(f"  - {p.data_vencimento.strftime('%d/%m')} — {p.processo.numero_processo or p.processo.numero_interno} — {p.descricao}")
    if not prazos_da_semana:
        linhas.append("  Nenhum.")
    linhas.append("")

    linhas.append(f"PRAZOS PERDIDOS NA SEMANA PASSADA ({len(prazos_perdidos_semana_passada)})")
    for p in prazos_perdidos_semana_passada:
        linhas.append(f"  - {p.data_vencimento.strftime('%d/%m')} — {p.processo.numero_processo or p.processo.numero_interno} — {p.descricao}")
    if not prazos_perdidos_semana_passada:
        linhas.append("  Nenhum.")
    linhas.append("")

    linhas.append(f"PROCESSOS PARADOS HÁ 30+ DIAS ({len(processos_parados)})")
    for p in processos_parados:
        linhas.append(f"  - {p.numero_processo or p.numero_interno} — {p.cliente.nome}")
    if not processos_parados:
        linhas.append("  Nenhum.")

    return "Relatório semanal de governança", "\n".join(linhas), inicio_semana, fim_semana


def enviar(assunto, corpo, config):
    destinatarios = [e.strip() for e in config.get("RELATORIO_SEMANAL_DESTINATARIOS", "").split(",") if e.strip()]
    if not (config.get("SMTP_HOST") and config.get("SMTP_USER") and config.get("SMTP_PASSWORD") and destinatarios):
        print("SMTP não configurado (SMTP_HOST/SMTP_USER/SMTP_PASSWORD/RELATORIO_SEMANAL_DESTINATARIOS "
              "faltando no .env) — relatório gerado, mas NÃO enviado.")
        print("\n--- conteúdo que seria enviado ---\n")
        print(corpo)
        return False

    msg = MIMEMultipart()
    msg["From"] = config.get("SMTP_REMETENTE") or config["SMTP_USER"]
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo, "plain", "utf-8"))

    contexto = ssl.create_default_context()
    with smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"]) as servidor:
        servidor.starttls(context=contexto)
        servidor.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        servidor.sendmail(msg["From"], destinatarios, msg.as_string())

    print(f"Relatório enviado para: {', '.join(destinatarios)}")
    return True


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        assunto, corpo, inicio, fim = montar_relatorio()
        if TESTAR:
            print(f"--- MODO TESTE (não envia) ---\n\n{corpo}")
        else:
            enviar(assunto, corpo, app.config)
