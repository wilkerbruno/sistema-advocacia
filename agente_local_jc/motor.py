"""
Motor de busca — a lógica de "pegar uma tarefa pendente, buscar os
autos, mandar o resultado (ou o erro) de volta" que tanto `main.py`
(modo desenvolvedor, terminal) quanto `tray_app.py` (modo instalado,
ícone na bandeja) usam. Fica num módulo só pra garantir que as duas
formas de rodar o agente se comportem EXATAMENTE igual — só muda de
onde cada uma lê a configuração (variável de ambiente vs. arquivo em
%APPDATA%) e como cada uma mostra o andamento (print no terminal vs.
notificação/menu da bandeja).
"""
import traceback

from conector_base import ErroConectorTribunal
import registro_conectores
import certificado


def processar_uma_tarefa(cliente, tarefa, cert_caminho, cert_senha, config_conectores, log):
    """
    `cliente`: um ClienteJusControl já configurado (ver cliente_api.py).
    `log(mensagem)`: callback pra reportar andamento — cada chamador
    decide se isso vira print, arquivo de log, notificação etc.
    """
    tarefa_id = tarefa["id"]
    slug_conector = tarefa["tribunal_conector"]
    numero_processo = tarefa.get("numero_processo") or ""

    log(f"Tarefa {tarefa_id}: processo {numero_processo!r}, conector {slug_conector!r} — iniciando.")
    cliente.marcar_iniciada(tarefa_id)

    try:
        config_conector = config_conectores.get(slug_conector, {})
        conector = registro_conectores.construir_conector(slug_conector, config_conector)

        cert_carregado = None
        if cert_caminho:
            cert_carregado = certificado.carregar_pfx(cert_caminho, cert_senha)

        if cert_carregado is not None:
            with cert_carregado:
                resultado = conector.buscar_autos_completos(numero_processo, cert_carregado)
        else:
            resultado = conector.buscar_autos_completos(numero_processo, None)

    except ErroConectorTribunal as e:
        log(f"Tarefa {tarefa_id}: erro esperado — {e}")
        cliente.reportar_erro(tarefa_id, str(e))
        return False
    except Exception as e:
        # Erro inesperado (bug, dependência faltando etc.) — reporta mesmo
        # assim pro advogado não ficar esperando pra sempre; o traceback
        # completo só vai pro log local, nunca pro servidor.
        log(f"Tarefa {tarefa_id}: erro inesperado — {e}\n{traceback.format_exc()}")
        cliente.reportar_erro(tarefa_id, f"Erro inesperado no agente local: {e}")
        return False

    resposta = cliente.enviar_resultado(tarefa_id, resultado.caminho_pdf)
    log(f"Tarefa {tarefa_id}: concluída — documento #{resposta.get('documento_id')} criado no processo.")
    return True


def verificar_uma_vez(cliente, cert_caminho, cert_senha, config_conectores, log):
    """Um ciclo: lista as tarefas pendentes e processa cada uma. Devolve
    quantas foram processadas (0 é normal — só significa "nada pendente
    agora"). Falha de rede/servidor não propaga — só loga, pro chamador
    (loop do main.py ou timer do tray_app.py) tentar de novo no próximo
    ciclo sem cair o programa inteiro."""
    try:
        tarefas = cliente.listar_tarefas_pendentes()
    except Exception as e:
        log(f"Falha ao verificar tarefas pendentes: {e}")
        return 0

    for tarefa in tarefas:
        try:
            processar_uma_tarefa(cliente, tarefa, cert_caminho, cert_senha, config_conectores, log)
        except Exception as e:
            log(f"Falha ao processar tarefa {tarefa.get('id')}: {e}\n{traceback.format_exc()}")
    return len(tarefas)
