"""
Disparo de lembretes de compromisso da Agenda (ver app/models/compromisso.py
e app/routes/agenda.py) na hora marcada pelo usuário em `notificar_em`.

Não roda sozinho — precisa ser AGENDADO. Já vem pronto para rodar dentro
do próprio container via cron (ver docker/lembretes-compromissos.cron e
Dockerfile), no mesmo esquema já usado pela recaptura diária do DataJud
(capturar_movimentacoes.py) — não depende de nenhum recurso externo de
agendamento nem de configuração manual no EasyPanel.

Roda a cada 5 minutos (granularidade do cron, ver o .cron) e dispara todo
compromisso cujo `notificar_em` já passou e ainda não foi notificado
(`notificacao_enviada_em is None`). Marca `notificacao_enviada_em` logo
depois de notificar, então nunca dispara duas vezes o mesmo lembrete
mesmo que o job rode várias vezes seguidas.

Canais (todos usam exatamente a mesma mensagem — nome, descrição completa
e data/hora — pra ninguém receber uma versão incompleta):
  - Notificação dentro do sistema (sempre, para o usuário responsável —
    não depende de nenhuma credencial externa).
  - E-mail, SE SMTP estiver configurado (ver app/utils/email.py e
    config.py): para o responsável (interno) sempre, E TAMBÉM para o
    cliente, se ele estiver vinculado ao compromisso e tiver e-mail
    cadastrado. Sem SMTP configurado, só a notificação in-app é enviada
    mesmo assim — nunca falha o lembrete inteiro por falta de e-mail.
  - WhatsApp, SE `WHATSAPP_BRIDGE_URL` estiver configurada E a EMPRESA do
    compromisso já tiver conectado o próprio número (ver
    app/utils/whatsapp.py, seção "MULTI-SESSÃO", e app/routes/
    integracoes.py — a pedido explícito, pra cada empresa cliente usar o
    PRÓPRIO número em vez de todas compartilharem o número da
    plataforma, já que os clientes não conseguem responder dúvida num
    número que não é da própria empresa deles):
      - Para o RESPONSÁVEL (o usuário do escritório que está enviando o
        lembrete), sempre que ele tiver um número cadastrado no próprio
        perfil (Usuario.whatsapp) — independente de cliente ou de
        `enviar_whatsapp`, pra ele também nunca esquecer o próprio
        compromisso.
      - Para o CLIENTE, SE o compromisso tiver `enviar_whatsapp=True` e
        estiver vinculado a um cliente com número cadastrado.
    Sem `WHATSAPP_BRIDGE_URL` configurada, OU sem a empresa ter conectado
    um número próprio ainda, o envio por WhatsApp simplesmente não
    acontece pra ninguém daquela empresa — o lembrete continua saindo
    normalmente pelos outros canais. NUNCA cai pra outro número "por
    padrão" (isso seria mandar a mensagem de uma empresa pelo número de
    outra).

Uso:
    python enviar_lembretes_compromissos.py
"""
import sys
from datetime import datetime

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db
from app.models import Compromisso
from app.utils.notificacoes import notificar
from app.utils.email import enviar_email, smtp_configurado
from app.utils.whatsapp import enviar_whatsapp, whatsapp_configurado


def enviar_lembretes():
    app = create_app()
    with app.app_context():
        agora = datetime.utcnow()
        pendentes = Compromisso.query.filter(
            Compromisso.status == "agendado",
            Compromisso.notificar_em.isnot(None),
            Compromisso.notificar_em <= agora,
            Compromisso.notificacao_enviada_em.is_(None),
        ).all()

        print(f"{len(pendentes)} lembrete(s) pendente(s).")

        for c in pendentes:
            try:
                titulo = f"Lembrete: {c.titulo}"
                detalhes = f" — {c.local}" if c.local else ""
                # Nome do compromisso na 1ª linha, descrição completa (se
                # houver) na 2ª, e data/hora — igual já funcionava — na
                # última. Sem descrição cadastrada, a linha some (nunca fica
                # uma linha em branco no meio da mensagem).
                linhas_mensagem = [c.titulo]
                if c.descricao:
                    linhas_mensagem.append(c.descricao)
                linhas_mensagem.append(f"às {c.data_hora.strftime('%d/%m/%Y %H:%M')}{detalhes}")
                mensagem = "\n".join(linhas_mensagem)

                notificar(c.responsavel_id, titulo, mensagem=mensagem, tipo="compromisso",
                          link=f"/agenda/compromissos/{c.id}/editar")

                # E-mail: pro responsável (interno) sempre que houver SMTP
                # configurado, e também pro cliente — a pedido explícito,
                # pra o cliente também receber o lembrete completo por
                # e-mail, não só quem está na equipe.
                if smtp_configurado():
                    if c.responsavel and c.responsavel.email:
                        enviar_email(c.responsavel.email, titulo, mensagem)
                    if c.cliente and c.cliente.email:
                        enviar_email(c.cliente.email, titulo, mensagem)

                # Cada compromisso pertence a uma empresa (via unidade), e
                # cada empresa tem a própria sessão do WAHA (= o próprio
                # número conectado) — ver Empresa.whatsapp_sessao_efetiva.
                # Sem empresa identificável ou sem sessão conectada, os
                # dois blocos de WhatsApp abaixo são pulados por completo
                # (nunca cai pro número de outra empresa).
                empresa_do_compromisso = c.unidade.empresa if c.unidade else None
                sessao = empresa_do_compromisso.whatsapp_sessao_efetiva if empresa_do_compromisso else None

                # WhatsApp do RESPONSÁVEL — a pedido explícito, pra quem
                # está enviando o lembrete também não esquecer do próprio
                # compromisso. Usa o número cadastrado no perfil dele
                # (Usuario.whatsapp, cadastro em Equipe > editar usuário),
                # NÃO o número do cliente, e independe de `enviar_whatsapp`
                # (que é sobre avisar o cliente, não sobre o responsável).
                if whatsapp_configurado() and sessao and c.responsavel and c.responsavel.whatsapp:
                    if enviar_whatsapp(c.responsavel.whatsapp, mensagem, sessao=sessao):
                        print(f"  WHATSAPP OK (responsável) compromisso #{c.id}: enviado para {c.responsavel.nome}.")
                    else:
                        print(f"  WHATSAPP FALHOU (responsável) compromisso #{c.id}: o WAHA recusou o envio "
                              f"pro número do responsável (veja o código HTTP no aviso 'WARNING in whatsapp' "
                              f"logo acima).")

                # Diagnóstico explícito de cada motivo de não enviar por
                # WhatsApp pro CLIENTE — sem isso, um envio pulado ficava
                # silencioso e indistinguível de "funcionou mas o cliente
                # não recebeu".
                if c.enviar_whatsapp:
                    if not whatsapp_configurado():
                        print(f"  WHATSAPP PULADO compromisso #{c.id}: WHATSAPP_BRIDGE_URL não configurada "
                              f"no .env do app principal (ver PENDENCIAS.md, seção -4).")
                    elif not sessao:
                        print(f"  WHATSAPP PULADO compromisso #{c.id}: a empresa "
                              f"'{empresa_do_compromisso.nome if empresa_do_compromisso else '?'}' ainda não "
                              f"conectou um número de WhatsApp próprio (Minhas Integrações).")
                    elif not c.cliente:
                        print(f"  WHATSAPP PULADO compromisso #{c.id}: nenhum cliente vinculado ao compromisso.")
                    elif not c.cliente.whatsapp:
                        print(f"  WHATSAPP PULADO compromisso #{c.id}: cliente '{c.cliente.nome}' não tem "
                              f"número de WhatsApp cadastrado.")
                    else:
                        if enviar_whatsapp(c.cliente.whatsapp, mensagem, sessao=sessao):
                            c.whatsapp_enviado_em = agora
                            print(f"  WHATSAPP OK compromisso #{c.id}: enviado para {c.cliente.nome}.")
                        else:
                            print(f"  WHATSAPP FALHOU compromisso #{c.id}: o WAHA recusou o envio "
                                  f"(veja o código HTTP no aviso 'WARNING in whatsapp' logo acima). "
                                  f"Causas mais comuns: (1) WHATSAPP_BRIDGE_TOKEN (no app principal) "
                                  f"diferente do WAHA_API_KEY (no serviço WAHA) — precisam ser "
                                  f"IDÊNTICOS, char por char; (2) a sessão '{sessao}' não está com "
                                  f"status WORKING (confira em Minhas Integrações).")

                c.notificacao_enviada_em = agora
                db.session.commit()
                print(f"  OK  compromisso #{c.id}: {c.titulo}")
            except Exception as e:  # nunca deixa um compromisso travar a fila inteira
                db.session.rollback()
                print(f"  ERRO  compromisso #{c.id}: {e}")


if __name__ == "__main__":
    enviar_lembretes()
