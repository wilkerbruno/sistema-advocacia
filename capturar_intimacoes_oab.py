"""
Captura periódica de intimações via API Comunica do CNJ (DJEN), por OAB
monitorada (item 1 da lista de pipeline de IA jurídica — PENDENCIAS.md,
seção -102) — para toda `OabMonitorada` com `ativo=True`.

Isso não roda sozinho — precisa ser AGENDADO (cron), do mesmo jeito que
capturar_movimentacoes.py. No EasyPanel, crie um serviço do tipo "Cron Job"
apontando pro mesmo código/imagem, rodando por exemplo 1x por dia:

    python capturar_intimacoes_oab.py

A API Comunica não tem defasagem de indexação conhecida (publicações
aparecem no dia da disponibilização) — rodar de manhã cedo cobre a
publicação do dia anterior a tempo de virar prazo na agenda do escritório.

Uso:
    python capturar_intimacoes_oab.py                # roda pra todas as OABs ativas
    python capturar_intimacoes_oab.py --limite 5      # só as 5 primeiras (teste)
    python capturar_intimacoes_oab.py --oab 12        # só uma OAB específica (ID interno)
    python capturar_intimacoes_oab.py --dias 3        # janela de disponibilização (default: desde a
                                                        # última captura bem-sucedida, ou 1 dia se nunca capturou)

Nunca marca uma OAB como inativa por um erro de rede pontual — só registra
em `OabMonitorada.ultimo_erro_captura` e segue para a próxima (mesmo
princípio de capturar_movimentacoes.py: falha de captura é sinal pra
alertar, nunca uma decisão automática de desligar o monitoramento).
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db
from app.models import OabMonitorada
from app.utils.captura_conectores import obter_conector
from app.utils.conector_djen import ConexaoComunicaError
from app.utils.captura_djen_pipeline import processar_publicacao_capturada

PAUSA_ENTRE_OABS_SEGUNDOS = 0.5  # gentileza com o rate limit não documentado publicamente da API Comunica
DIAS_JANELA_PADRAO_SEM_CAPTURA_ANTERIOR = 1


def capturar(limite=None, oab_id=None, dias_janela=None):
    app = create_app()
    with app.app_context():
        query = OabMonitorada.query.filter_by(ativo=True)
        if oab_id:
            query = query.filter_by(id=oab_id)
        if limite:
            query = query.limit(limite)
        oabs = query.all()

        print(f"{len(oabs)} OAB(s) monitorada(s) para capturar.")
        sucesso, falha = 0, 0
        conector = obter_conector("djen")  # público, sem chave — um único conector serve todas as empresas
        hoje = date.today()

        for oab in oabs:
            if dias_janela:
                data_inicio = hoje - timedelta(days=dias_janela)
            elif oab.ultima_captura_em:
                data_inicio = oab.ultima_captura_em.date()
            else:
                data_inicio = hoje - timedelta(days=DIAS_JANELA_PADRAO_SEM_CAPTURA_ANTERIOR)

            try:
                publicacoes = conector.monitorar_publicacoes_por_oab(
                    oab.numero, oab.uf, data_inicio=data_inicio, data_fim=hoje,
                )
                novas = 0
                for publicacao_capturada in publicacoes:
                    intimacao = processar_publicacao_capturada(oab, publicacao_capturada)
                    if intimacao is not None:
                        novas += 1
                oab.ultima_captura_em = datetime.utcnow()
                oab.ultimo_erro_captura = None
                db.session.commit()
                sucesso += 1
                print(f"  OK  OAB {oab.numero}/{oab.uf}: {novas} intimação(ões) nova(s) "
                      f"de {len(publicacoes)} publicação(ões) encontrada(s).")
            except (ConexaoComunicaError, ValueError) as e:
                db.session.rollback()
                oab.ultimo_erro_captura = str(e)[:500]
                db.session.commit()
                falha += 1
                print(f"  FALHA OAB {oab.numero}/{oab.uf}: {e}")
            except Exception as e:  # nunca deixa uma OAB travar a fila inteira
                db.session.rollback()
                oab.ultimo_erro_captura = f"Erro inesperado: {e}"[:500]
                db.session.commit()
                falha += 1
                print(f"  ERRO  OAB {oab.numero}/{oab.uf}: {e}")

            time.sleep(PAUSA_ENTRE_OABS_SEGUNDOS)

        print(f"Concluído: {sucesso} sucesso(s), {falha} falha(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limite", type=int, default=None)
    parser.add_argument("--oab", type=int, default=None, dest="oab_id")
    parser.add_argument("--dias", type=int, default=None, dest="dias_janela")
    args = parser.parse_args()
    capturar(limite=args.limite, oab_id=args.oab_id, dias_janela=args.dias_janela)
