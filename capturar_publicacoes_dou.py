"""
Captura diária do Diário Oficial da União (DOU) via INLABS — pedido do
usuário (2026-09): "seria possível a gente vincular o diário oficial da
união no sistema". Ver PENDENCIAS.md (seção mais recente) para a pesquisa
completa (por que INLABS + "diário completo" em vez de adotar o Ro-DOU
como plataforma) e app/utils/conector_inlabs_dou.py para o fluxo de
download/parsing.

Isso não roda sozinho — precisa ser AGENDADO (cron), do mesmo jeito que
capturar_movimentacoes.py e capturar_intimacoes_oab.py. No EasyPanel, crie
um serviço do tipo "Cron Job" apontando pro mesmo código/imagem, rodando
1x por dia, de madrugada:

    python capturar_publicacoes_dou.py

⚠️ Horário sugerido, não confirmado contra o INLABS real: a edição
"matutina" do DOU costuma ser publicada de madrugada (por volta da 0h/1h,
segundo o próprio site da Imprensa Nacional), mas isso não foi validado a
partir deste ambiente de geração de código (proxy bloqueia .gov.br — ver
aviso em conector_inlabs_dou.py). Rodar às 3h-4h da manhã dá folga pra
eventual atraso na publicação da edição do dia.

Se as credenciais INLABS_EMAIL/INLABS_SENHA não estiverem configuradas, o
script avisa e encerra sem erro (exit code 0) — o resto do sistema
continua funcionando normalmente sem a vigilância do DOU (mesmo princípio
usado em outras integrações opcionais deste projeto).

Uso:
    python capturar_publicacoes_dou.py                  # captura a edição de hoje, seções DO1/DO2/DO3
    python capturar_publicacoes_dou.py --data 2026-09-20 # captura a edição de uma data específica
    python capturar_publicacoes_dou.py --secoes DO1,DO2  # só as seções informadas

Nunca lança exceção pra fora por causa de uma falha de rede/credencial
pontual — registra no log e encerra com exit code != 0 pra o cron acusar a
falha (não há um "OabMonitorada.ultimo_erro_captura" equivalente aqui,
porque a captura do DOU não é por unidade/alvo, é um download único
compartilhado por toda a plataforma).
"""
import argparse
import sys
from datetime import date, datetime

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db
from app.utils.conector_inlabs_dou import (
    baixar_materias_do_dia, login_configurado, ConexaoInlabsError,
    SECOES_PADRAO,
)
from app.utils.captura_dou_pipeline import processar_materia_capturada


def capturar(data_referencia=None, secoes=None):
    app = create_app()
    with app.app_context():
        if not login_configurado():
            print(
                "Vigilância do DOU não configurada (faltam INLABS_EMAIL/INLABS_SENHA) "
                "— nada a fazer, encerrando sem erro."
            )
            return 0

        secoes = secoes or SECOES_PADRAO
        data_referencia = data_referencia or date.today()
        print(f"Capturando DOU de {data_referencia.strftime('%d/%m/%Y')} — seções {', '.join(secoes)}...")

        try:
            materias = baixar_materias_do_dia(data_referencia=data_referencia, secoes=secoes)
        except ConexaoInlabsError as e:
            print(f"FALHA ao baixar o DOU: {e}")
            return 1

        print(f"{len(materias)} matéria(s) baixada(s). Conferindo contra clientes/palavras-chave monitorados...")

        total_capturas = 0
        for materia in materias:
            try:
                criadas = processar_materia_capturada(materia)
                if criadas:
                    total_capturas += len(criadas)
                    db.session.commit()
            except Exception as e:  # nunca deixa UMA matéria malformada travar o dia inteiro
                db.session.rollback()
                print(f"  ERRO ao processar matéria '{materia.get('id_materia_fonte')}': {e}")

        print(f"Concluído: {total_capturas} publicação(ões) nova(s) capturada(s) para revisão.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, default=None, dest="data_str",
                         help="Data de referência no formato AAAA-MM-DD (default: hoje).")
    parser.add_argument("--secoes", type=str, default=None,
                         help="Seções separadas por vírgula, ex: DO1,DO2,DO3 (default: DO1,DO2,DO3).")
    args = parser.parse_args()

    data_referencia = None
    if args.data_str:
        try:
            data_referencia = datetime.strptime(args.data_str, "%Y-%m-%d").date()
        except ValueError:
            print(f"Data inválida: '{args.data_str}' — use o formato AAAA-MM-DD.")
            sys.exit(2)

    secoes = tuple(s.strip().upper() for s in args.secoes.split(",")) if args.secoes else None

    sys.exit(capturar(data_referencia=data_referencia, secoes=secoes))
