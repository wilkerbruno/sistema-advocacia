"""
Lembrete automático de prazo processual e de audiência (PENDENCIAS.md,
seção -44) — item "Lembrete de prazo/audiência por WhatsApp/e-mail" da
tabela de prioridades do relatório de 20/08/2026. Mesmo mecanismo de
disparo de enviar_lembretes_compromissos.py (cron dentro do próprio
container, ver docker/lembretes-prazos-audiencias.cron), script
SEPARADO por ser uma regra diferente: Compromisso deixa o usuário
escolher um horário exato (`notificar_em`); prazo e audiência não têm
esse campo — o lembrete dispara sozinho quando falta um número fixo de
dias (configurável por variável de ambiente, ver config.py:
LEMBRETE_PRAZO_DIAS_ANTES / LEMBRETE_AUDIENCIA_DIAS_ANTES) pra vencer.

Roda a cada dia (ver o .cron — diferente dos lembretes de compromisso,
que precisam de granularidade de minutos porque o horário é exato; aqui
a unidade que importa é o DIA, então rodar 1x/dia de madrugada já cobre
o caso de uso) e dispara todo prazo/audiência dentro da janela (ou já
vencido/já passado, sem lembrete ainda — melhor um lembrete atrasado que
nenhum) cujo `lembrete_enviado_em` ainda esteja vazio. Marca
`lembrete_enviado_em` logo depois, então nunca dispara duas vezes o
mesmo lembrete mesmo que o job rode de novo.

Diferença de destinatário entre os dois tipos (decisão deliberada, não
configurável por enquanto — ver PENDENCIAS.md se isso precisar mudar):
  - PRAZO: só o responsável interno. Prazo processual ("prazo pra
    contestar", "prazo pra recorrer") é jargão que não costuma fazer
    sentido pro cliente sem contexto, e mandar isso desacompanhado por
    WhatsApp arriscaria confundir/preocupar à toa. Fica só entre a
    equipe.
  - AUDIÊNCIA: responsável interno E o cliente do processo (quando tem
    e-mail/WhatsApp cadastrado) — audiência é um evento que o cliente
    frequentemente precisa saber ou comparecer, diferente de um prazo
    interno.

Canais (mesmo padrão de enviar_lembretes_compromissos.py — ver os
comentários lá para o detalhe de cada verificação de configuração):
  - Notificação dentro do sistema: sempre, pro responsável.
  - E-mail: se SMTP estiver configurado, pro responsável sempre, e pro
    cliente também no caso de audiência.
  - WhatsApp: se WHATSAPP_BRIDGE_URL estiver configurada E a empresa já
    tiver conectado o próprio número — pro responsável sempre (se ele
    tiver número cadastrado no perfil) e pro cliente no caso de
    audiência (se o cliente tiver número cadastrado). Nunca cai pro
    número de outra empresa nem falha o lembrete inteiro por falta de
    canal configurado.

Uso:
    python enviar_lembretes_prazos_audiencias.py
"""
import sys
from datetime import datetime, date, timedelta

sys.path.insert(0, ".")
from flask import current_app
from app import create_app
from app.extensions import db
from app.models import Prazo, Audiencia
from app.utils.notificacoes import notificar
from app.utils.email import enviar_email, smtp_configurado
from app.utils.whatsapp import enviar_whatsapp, whatsapp_configurado

STATUS_PRAZO_PENDENTES = ("pendente", "em_elaboracao", "protocolado_aguardando_evidencia")


def _empresa_e_sessao(unidade):
    empresa = unidade.empresa if unidade else None
    sessao = empresa.whatsapp_sessao_efetiva if empresa else None
    return empresa, sessao


def _enviar_whatsapp_responsavel(responsavel, sessao, mensagem, rotulo):
    if whatsapp_configurado() and sessao and responsavel and responsavel.whatsapp:
        if enviar_whatsapp(responsavel.whatsapp, mensagem, sessao=sessao):
            print(f"  WHATSAPP OK (responsável) {rotulo}: enviado para {responsavel.nome}.")
        else:
            print(f"  WHATSAPP FALHOU (responsável) {rotulo}: o WAHA recusou o envio.")


def enviar_lembretes_prazos():
    dias_antes = current_app.config["LEMBRETE_PRAZO_DIAS_ANTES"]
    limite = date.today() + timedelta(days=dias_antes)
    agora = datetime.utcnow()

    pendentes = Prazo.query.filter(
        Prazo.status.in_(STATUS_PRAZO_PENDENTES),
        Prazo.data_vencimento <= limite,
        Prazo.lembrete_enviado_em.is_(None),
        Prazo.deletado_em.is_(None),
    ).all()

    print(f"{len(pendentes)} lembrete(s) de prazo pendente(s).")

    for p in pendentes:
        try:
            processo = p.processo
            numero = (processo.numero_processo or processo.numero_interno or f"processo #{processo.id}") if processo else "processo"
            titulo = f"Lembrete de prazo: {p.descricao}"
            linhas = [p.descricao, f"Processo: {numero}", f"Vencimento: {p.data_vencimento.strftime('%d/%m/%Y')}"]
            mensagem = "\n".join(linhas)

            if p.responsavel_id:
                notificar(p.responsavel_id, titulo, mensagem=mensagem, tipo="prazo",
                          link=f"/processos/{p.processo_id}" if p.processo_id else None)

                if smtp_configurado() and p.responsavel and p.responsavel.email:
                    enviar_email(p.responsavel.email, titulo, mensagem)

                _, sessao = _empresa_e_sessao(processo.unidade if processo else None)
                _enviar_whatsapp_responsavel(p.responsavel, sessao, mensagem, f"prazo #{p.id}")

            p.lembrete_enviado_em = agora
            db.session.commit()
            print(f"  OK  prazo #{p.id}: {p.descricao}")
        except Exception as e:  # nunca deixa um prazo travar a fila inteira
            db.session.rollback()
            print(f"  ERRO  prazo #{p.id}: {e}")


def enviar_lembretes_audiencias():
    dias_antes = current_app.config["LEMBRETE_AUDIENCIA_DIAS_ANTES"]
    limite = datetime.utcnow() + timedelta(days=dias_antes)
    agora = datetime.utcnow()

    pendentes = Audiencia.query.filter(
        Audiencia.status == "agendada",
        Audiencia.data_hora <= limite,
        Audiencia.lembrete_enviado_em.is_(None),
    ).all()

    print(f"{len(pendentes)} lembrete(s) de audiência pendente(s).")

    for a in pendentes:
        try:
            processo = a.processo
            numero = (processo.numero_processo or processo.numero_interno or f"processo #{processo.id}") if processo else "processo"
            titulo = f"Lembrete de audiência: {a.tipo or 'Audiência'}"
            linhas = [f"Audiência {a.tipo or ''}".strip(), f"Processo: {numero}",
                      f"Data: {a.data_hora.strftime('%d/%m/%Y %H:%M')}"]
            if a.local:
                linhas.append(f"Local: {a.local}")
            if a.modalidade == "virtual" and a.link_virtual:
                linhas.append(f"Link: {a.link_virtual}")
            mensagem = "\n".join(linhas)

            cliente = processo.cliente if processo else None
            empresa, sessao = _empresa_e_sessao(processo.unidade if processo else None)

            if a.responsavel_id:
                notificar(a.responsavel_id, titulo, mensagem=mensagem, tipo="audiencia",
                          link=f"/processos/{a.processo_id}" if a.processo_id else None)
                if smtp_configurado() and a.responsavel and a.responsavel.email:
                    enviar_email(a.responsavel.email, titulo, mensagem)
                _enviar_whatsapp_responsavel(a.responsavel, sessao, mensagem, f"audiência #{a.id}")

            # Cliente: diferente de prazo, audiência é evento que o cliente
            # costuma precisar saber/comparecer — ver docstring do módulo.
            if cliente:
                if smtp_configurado() and cliente.email:
                    enviar_email(cliente.email, titulo, mensagem)
                if whatsapp_configurado() and sessao and cliente.whatsapp:
                    if enviar_whatsapp(cliente.whatsapp, mensagem, sessao=sessao):
                        print(f"  WHATSAPP OK (cliente) audiência #{a.id}: enviado para {cliente.nome}.")
                    else:
                        print(f"  WHATSAPP FALHOU (cliente) audiência #{a.id}: o WAHA recusou o envio.")

            a.lembrete_enviado_em = agora
            db.session.commit()
            print(f"  OK  audiência #{a.id}: {a.tipo or 'audiência'} em {a.data_hora.strftime('%d/%m/%Y %H:%M')}")
        except Exception as e:  # nunca deixa uma audiência travar a fila inteira
            db.session.rollback()
            print(f"  ERRO  audiência #{a.id}: {e}")


def enviar_lembretes():
    app = create_app()
    with app.app_context():
        enviar_lembretes_prazos()
        enviar_lembretes_audiencias()


if __name__ == "__main__":
    enviar_lembretes()
