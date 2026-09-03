"""
Agente Local do JusControl — laço principal.

Roda continuamente na máquina do advogado: a cada `INTERVALO_POLLING_SEGUNDOS`,
pergunta ao JusControl se há alguma busca de autos pendente para este
usuário (ver "Meu agente local" e o botão "Buscar autos completos" na
tela do processo), e para cada uma:
  1. marca como "em andamento" (pra não ser pega duas vezes);
  2. carrega o certificado (só em memória, nunca gravado sem cifra);
  3. chama o conector do tribunal certo (ver registro_conectores.py);
  4. manda o PDF resultante de volta pro JusControl, ou reporta o erro.

Uso:
  1. cd agente_local_jc
  2. python -m venv .venv && (.venv\\Scripts\\activate  OU  source .venv/bin/activate)
  3. pip install -r requirements.txt
  4. cp .env.exemplo .env   (e preencher com os valores reais)
  5. python main.py

⚠️ Ver README.md antes de rodar isto contra um processo real — o
conector "pje_mni" é um piloto NÃO testado contra nenhum tribunal.
"""
import sys
import time
import traceback

import config
import cliente_api
import certificado
from registro_conectores import construir_conector
from conector_base import ErroConectorTribunal


def _log(mensagem):
    print(f"[agente-local] {mensagem}", flush=True)


def processar_tarefa(tarefa):
    tarefa_id = tarefa["id"]
    slug_conector = tarefa["tribunal_conector"]
    numero_processo = tarefa.get("numero_processo") or ""

    _log(f"Tarefa {tarefa_id}: processo {numero_processo!r}, conector {slug_conector!r} — iniciando.")
    cliente_api.marcar_iniciada(tarefa_id)

    try:
        config_conector = config.CONFIG_CONECTORES.get(slug_conector, {})
        conector = construir_conector(slug_conector, config_conector)

        cert_carregado = None
        if config.CERTIFICADO_PFX_CAMINHO:
            cert_carregado = certificado.carregar_pfx(config.CERTIFICADO_PFX_CAMINHO, config.CERTIFICADO_PFX_SENHA)

        if cert_carregado is not None:
            with cert_carregado:
                resultado = conector.buscar_autos_completos(numero_processo, cert_carregado)
        else:
            resultado = conector.buscar_autos_completos(numero_processo, None)

    except ErroConectorTribunal as e:
        _log(f"Tarefa {tarefa_id}: erro esperado — {e}")
        cliente_api.reportar_erro(tarefa_id, str(e))
        return
    except Exception as e:
        # Erro inesperado (bug, dependência faltando etc.) — reporta
        # mesmo assim pro advogado não ficar esperando pra sempre, mas
        # com o traceback só no log local (nunca manda traceback interno
        # pro servidor).
        _log(f"Tarefa {tarefa_id}: erro inesperado — {e}\n{traceback.format_exc()}")
        cliente_api.reportar_erro(tarefa_id, f"Erro inesperado no agente local: {e}")
        return

    resposta = cliente_api.enviar_resultado(tarefa_id, resultado.caminho_pdf)
    _log(f"Tarefa {tarefa_id}: concluída — documento #{resposta.get('documento_id')} criado no processo.")


def loop_principal():
    config.validar_configuracao_minima()
    try:
        info = cliente_api.ping()
    except cliente_api.ErroApiJusControl as e:
        _log(str(e))
        sys.exit(1)
    _log(f"Pareado como {info.get('usuario')} — agente {info.get('apelido')!r}. "
         f"Verificando tarefas a cada {config.INTERVALO_POLLING_SEGUNDOS}s.")

    while True:
        try:
            tarefas = cliente_api.listar_tarefas_pendentes()
            for tarefa in tarefas:
                processar_tarefa(tarefa)
        except Exception as e:
            # Falha de rede/servidor não deve derrubar o agente — só loga
            # e tenta de novo no próximo ciclo.
            _log(f"Falha neste ciclo de verificação: {e}")
        time.sleep(config.INTERVALO_POLLING_SEGUNDOS)


if __name__ == "__main__":
    try:
        loop_principal()
    except KeyboardInterrupt:
        _log("Encerrado pelo usuário.")
