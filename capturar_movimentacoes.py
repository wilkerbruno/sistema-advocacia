"""
Captura periódica de movimentações via DataJud (ver
app/utils/conector_datajud.py) para todos os processos marcados como
`monitoravel=True` e `forma_acompanhamento="automatico"`.

Isso não roda sozinho — precisa ser AGENDADO (cron). No EasyPanel, crie um
serviço do tipo "Cron Job" apontando pro mesmo código/imagem, rodando por
exemplo 1x por dia:

    python capturar_movimentacoes.py

(a frequência exata fica a seu critério — o próprio DataJud não atualiza
em tempo real, a defasagem dos tribunais para a base nacional já é de
horas a alguns dias, então rodar de hora em hora não traz ganho real e só
consome a cota de uso da chave de API à toa; 1x por dia costuma ser
suficiente para controle de prazo).

Uso:
    python capturar_movimentacoes.py                # roda pra todos os monitoráveis
    python capturar_movimentacoes.py --limite 20     # só os 20 primeiros (teste)
    python capturar_movimentacoes.py --processo 123  # só um processo específico (ID interno)

Nunca marca um processo como falho por um erro de rede pontual — só
registra em LogCaptura (aba Governança > observabilidade) e segue pro
próximo. Não faz `monitoravel=False` automaticamente: isso é sempre uma
decisão humana (ver governanca.marcar_nao_monitoravel), captura falhando
só acende o alerta (seção 14 do briefing: "alerta quando a captura falha
2 dias seguidos").
"""
import argparse
import sys
import time

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db
from app.models import Processo, LogCaptura
from app.utils.captura_conectores import obter_conector, ConectorNaoConfiguradoError
from app.utils.conector_datajud import TribunalNaoIdentificadoError, ConexaoDataJudError
from app.utils.captura_pipeline import aplicar_carga_inicial, registrar_movimentacoes_capturadas

PAUSA_ENTRE_CHAMADAS_SEGUNDOS = 0.5  # gentileza com a cota de uso da chave de API


def capturar(limite=None, processo_id=None):
    app = create_app()
    with app.app_context():
        try:
            conector = obter_conector("padrao")
        except ConectorNaoConfiguradoError as e:
            print(f"Captura não configurada: {e}")
            return

        query = Processo.query.filter_by(monitoravel=True, forma_acompanhamento="automatico")
        if processo_id:
            query = query.filter_by(id=processo_id)
        if limite:
            query = query.limit(limite)
        processos = query.all()

        print(f"{len(processos)} processo(s) para recapturar.")
        sucesso, falha = 0, 0

        for processo in processos:
            try:
                dados = conector.consultar_processo(processo.numero_processo, tribunal_hint=processo.tribunal_datajud)
                aplicar_carga_inicial(processo, dados)
                novas = registrar_movimentacoes_capturadas(processo, dados["movimentacoes"])
                db.session.add(LogCaptura(
                    fonte="datajud", processo_id=processo.id, tribunal=dados["tribunal_slug"],
                    status="sucesso", mensagem=f"{novas} movimentação(ões) nova(s).",
                ))
                db.session.commit()
                sucesso += 1
                print(f"  OK  {processo.numero_processo}: {novas} movimentação(ões) nova(s).")
            except (TribunalNaoIdentificadoError, ConexaoDataJudError, ValueError) as e:
                db.session.rollback()
                db.session.add(LogCaptura(
                    fonte="datajud", processo_id=processo.id, tribunal=processo.tribunal_datajud,
                    status="falha", mensagem=str(e)[:500],
                ))
                db.session.commit()
                falha += 1
                print(f"  FALHA {processo.numero_processo}: {e}")
            except Exception as e:  # nunca deixa um processo travar a fila inteira
                db.session.rollback()
                db.session.add(LogCaptura(
                    fonte="datajud", processo_id=processo.id, tribunal=processo.tribunal_datajud,
                    status="falha", mensagem=f"Erro inesperado: {e}"[:500],
                ))
                db.session.commit()
                falha += 1
                print(f"  ERRO  {processo.numero_processo}: {e}")

            time.sleep(PAUSA_ENTRE_CHAMADAS_SEGUNDOS)

        print(f"Concluído: {sucesso} sucesso(s), {falha} falha(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limite", type=int, default=None)
    parser.add_argument("--processo", type=int, default=None, dest="processo_id")
    args = parser.parse_args()
    capturar(limite=args.limite, processo_id=args.processo_id)
