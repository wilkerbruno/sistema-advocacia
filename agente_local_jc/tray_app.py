"""
Agente Local do JusControl — modo INSTALADO (ícone na bandeja do
Windows, início automático, sem terminal nenhum aberto). É este arquivo
que vira o `.exe` distribuído pelo instalador (ver build/agente_local.spec
e build/instalador.iss) — para rodar/testar direto do código-fonte sem
instalar nada, use `main.py`.

Fluxo:
  1. Se o advogado configurou um login local (OAB + senha — opcional, ver
     login_local.py/tela_bloqueio.py), pede a senha ANTES de qualquer
     outra coisa; errar demais ou cancelar encerra o programa sem abrir
     nada.
  2. Se não há configuração salva ainda (config_store.py), abre a janela
     de configuração (config_gui.py) pedindo o endereço do JusControl e
     o token de pareamento.
  3. Registra o início automático com o Windows, se marcado.
  4. Sobe o ícone na bandeja (numa thread própria) e, em outra thread à
     parte, fica checando tarefas pendentes a cada N segundos (mesmo
     motor.py usado por main.py) — o ícone muda de cor conforme o
     status. A THREAD PRINCIPAL fica livre pra ser a única a criar/usar
     janelas do tkinter (ver _bombear_fila_gui) — no Windows, tkinter
     trava sem erro nenhum se usado fora da thread que já está com um
     mainloop dele rodando, e o pystray despacha cada clique de menu
     numa thread própria dele, diferente da principal.
  5. Reabrir "Configurar..." pelo menu do ícone, depois do primeiro
     pareamento, passa a exigir o código do autenticador (2FA) da conta
     JusControl — ver autenticador_local.py.
"""
import ctypes
import os
import queue
import sys
import threading
import time
from datetime import datetime

import pystray
from PIL import Image, ImageDraw

import config_store
import config_gui
import login_local
import tela_bloqueio
import autenticador_local
import motor
import autostart_windows
from cliente_api import ClienteJusControl, ErroApiJusControl

NOME_APP = "Agente Local — JusControl"
_NOME_MUTEX_INSTANCIA_UNICA = "Global\\JusControlAgenteLocal_InstanciaUnica"
_ERROR_ALREADY_EXISTS = 183

_estado = {"status": "iniciando", "ultima_mensagem": "", "parar": False}
_lock = threading.Lock()
_mutex_instancia = None  # precisa ficar vivo até o processo terminar — ver _garantir_instancia_unica
_fila_gui = queue.Queue()  # ver _abrir_configuracao/_bombear_fila_gui — por quê isto existe


def _garantir_instancia_unica():
    """
    Impede abrir o agente duas vezes ao mesmo tempo — cada instância nova
    duplicaria o ícone na bandeja e a janela de configuração (foi
    exatamente o que aconteceu rodando `python tray_app.py` mais de uma
    vez enquanto a primeira ainda estava subindo — ver PENDENCIAS.md).
    Usa um mutex nomeado do próprio Windows (`ctypes`, sem dependência
    nova) — só funciona no Windows, mas o agente só roda como app de
    verdade lá mesmo (o `.exe` distribuído é só pra Windows).
    """
    global _mutex_instancia
    if sys.platform != "win32":
        return True

    _mutex_instancia = ctypes.windll.kernel32.CreateMutexW(None, False, _NOME_MUTEX_INSTANCIA_UNICA)
    ja_tem_outra_instancia = ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS
    if not ja_tem_outra_instancia:
        return True

    try:
        import tkinter
        from tkinter import messagebox
        raiz = tkinter.Tk()
        raiz.withdraw()
        messagebox.showwarning(
            NOME_APP,
            "O Agente Local já está rodando — veja o ícone perto do relógio do Windows "
            "(pode estar escondido nos ícones ocultos, a setinha \"^\"). Não precisa abrir de novo.",
        )
        raiz.destroy()
    except Exception:
        pass
    return False


def _log(mensagem):
    linha = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {mensagem}"
    with _lock:
        _estado["ultima_mensagem"] = mensagem
    try:
        with open(config_store.caminho_log(), "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def _cor_do_status(status):
    return {"ok": (46, 125, 50), "erro": (198, 40, 40), "iniciando": (158, 158, 158)}.get(status, (158, 158, 158))


def _gerar_icone(status):
    """Desenha um ícone simples (círculo colorido com "J") na hora — não
    depende de nenhum arquivo de imagem externo, então o instalador não
    precisa empacotar nenhum .ico separado além do que o PyInstaller já
    gera pro próprio executável."""
    tamanho = 64
    imagem = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    desenho.ellipse((2, 2, tamanho - 2, tamanho - 2), fill=_cor_do_status(status))
    desenho.text((tamanho / 2 - 8, tamanho / 2 - 14), "J", fill="white")
    return imagem


def _definir_status(icone, status):
    with _lock:
        _estado["status"] = status
    icone.icon = _gerar_icone(status)
    icone.title = f"{NOME_APP} — {'conectado' if status == 'ok' else 'com problema' if status == 'erro' else 'iniciando'}"


def _construir_cliente(dados):
    return ClienteJusControl(dados["juscontrol_url"], dados["token_pareamento"])


def _config_conectores(dados):
    return {
        "pje_mni": {
            "tribunal": dados.get("pje_tribunal", ""),
            "instancia": dados.get("pje_instancia", "1g"),
            "id_consultante": dados.get("pje_id_consultante", ""),
            "senha_consultante": dados.get("pje_senha_consultante", ""),
            "url_wsdl": dados.get("pje_url_wsdl") or None,
        },
        "projudi": {
            "id_consultante": dados.get("projudi_id_consultante", ""),
            "senha_consultante": dados.get("projudi_senha_consultante", ""),
            "url_wsdl": dados.get("projudi_url_wsdl") or None,
        },
        "esaj_sp": {
            "id_consultante": dados.get("esaj_id_consultante", ""),
            "senha_consultante": dados.get("esaj_senha_consultante", ""),
            "url_wsdl": dados.get("esaj_url_wsdl") or None,
        },
    }


def _laco_verificacao(icone):
    while True:
        with _lock:
            if _estado["parar"]:
                return
        dados = config_store.carregar()
        if not config_store.configuracao_minima_completa(dados):
            _definir_status(icone, "erro")
            _log("Sem configuração completa — abra \"Configurar...\" no menu do ícone.")
            time.sleep(dados.get("intervalo_polling_segundos", 60))
            continue

        cliente = _construir_cliente(dados)
        try:
            motor.verificar_uma_vez(
                cliente, dados.get("certificado_pfx_caminho", ""), dados.get("certificado_pfx_senha", ""),
                _config_conectores(dados), _log,
            )
            _definir_status(icone, "ok")
        except Exception as e:
            _log(f"Falha no ciclo de verificação: {e}")
            _definir_status(icone, "erro")

        time.sleep(int(dados.get("intervalo_polling_segundos", 60)))


def _abrir_configuracao(icone, item=None):
    # NÃO abre a janela aqui: no Windows, o pystray despacha cada clique
    # de menu numa thread própria (dele), e o tkinter trava — sem erro
    # nenhum, sem fechar nem no "OK" nem no X — se uma janela dele é
    # criada/usada fora da thread que está rodando o mainloop principal
    # (era a suposição do comentário antigo deste método, que se provou
    # errada na prática — ver PENDENCIAS.md). Por isso só enfileira o
    # pedido aqui; quem de fato abre a janela é sempre a THREAD PRINCIPAL,
    # em _bombear_fila_gui — a única que pode tocar em tkinter neste
    # programa.
    _fila_gui.put("configurar")


def _abrir_log(icone, item=None):
    caminho = config_store.caminho_log()
    if sys.platform == "win32":
        os.startfile(caminho) if os.path.exists(caminho) else None  # noqa: S606


def _verificar_agora(icone, item=None):
    dados = config_store.carregar()
    if not config_store.configuracao_minima_completa(dados):
        return
    cliente = _construir_cliente(dados)
    motor.verificar_uma_vez(
        cliente, dados.get("certificado_pfx_caminho", ""), dados.get("certificado_pfx_senha", ""),
        _config_conectores(dados), _log,
    )


def _sair(icone, item=None):
    with _lock:
        _estado["parar"] = True
    icone.stop()


def _bombear_fila_gui():
    """
    Roda na THREAD PRINCIPAL do processo, do início ao fim — é aqui, e só
    aqui, que este programa cria/usa qualquer janela do tkinter (ver
    aviso em _abrir_configuracao). Fica esperando pedidos que chegam pela
    fila (colocados a partir do menu do ícone, numa thread diferente) e
    processa um de cada vez; sai quando "Sair" é clicado no menu.
    """
    while True:
        with _lock:
            if _estado["parar"]:
                return
        try:
            pedido = _fila_gui.get(timeout=0.5)
        except queue.Empty:
            continue

        if pedido == "configurar":
            dados_atuais = config_store.carregar()
            # Autenticador (2FA) obrigatório pra abrir a Configuração —
            # pedido do usuário, ver autenticador_local.py. Roda ANTES de
            # abrir o wizard, na mesma thread principal (a única que pode
            # tocar em tkinter neste programa — ver aviso em
            # _abrir_configuracao logo acima).
            if not autenticador_local.pode_abrir_configuracao(dados_atuais, logar=_log):
                _log("Acesso à Configuração negado (autenticador não confirmado).")
                continue

            resultado = config_gui.abrir_wizard_configuracao(dados_atuais)
            if resultado:
                _aplicar_autostart(resultado)
                _log("Configuração atualizada pelo usuário.")


def _aplicar_autostart(dados):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return  # só faz sentido registrar o .exe compilado, não o script Python cru
    caminho_exe = sys.executable
    if dados.get("iniciar_com_windows", True):
        autostart_windows.ativar(caminho_exe)
    else:
        autostart_windows.desativar()


def main():
    if not _garantir_instancia_unica():
        return

    dados = config_store.carregar()

    # Login local (OAB + senha) — pedido do usuário, ver login_local.py e
    # tela_bloqueio.py. Só pede alguma coisa se o advogado tiver
    # configurado isso antes (opcional); roda ANTES de qualquer outra
    # coisa, inclusive do wizard de primeira configuração — o cadeado
    # protege o programa como um todo, não só o polling em segundo plano.
    if login_local.login_local_configurado(dados):
        if not tela_bloqueio.abrir_tela_bloqueio(dados):
            return  # senha errada esgotada, ou o usuário cancelou — não abre nada

    if not config_store.configuracao_minima_completa(dados):
        dados = config_gui.abrir_wizard_configuracao(dados)
        if not dados:
            return  # usuário cancelou a configuração inicial — não tem o que rodar

    _aplicar_autostart(dados)

    icone = pystray.Icon(
        "jus_control_agente_local", _gerar_icone("iniciando"), NOME_APP,
        menu=pystray.Menu(
            pystray.MenuItem("Configurar...", _abrir_configuracao),
            pystray.MenuItem("Verificar agora", _verificar_agora),
            pystray.MenuItem("Ver log", _abrir_log),
            pystray.MenuItem("Sair", _sair),
        ),
    )

    thread_verificacao = threading.Thread(target=_laco_verificacao, args=(icone,), daemon=True)
    thread_verificacao.start()

    # O ícone/menu do pystray roda na PRÓPRIA thread dele a partir daqui —
    # a thread principal fica livre pra ser a única a usar tkinter (ver
    # _bombear_fila_gui e o aviso em _abrir_configuracao).
    thread_icone = threading.Thread(target=icone.run, daemon=True)
    thread_icone.start()

    _bombear_fila_gui()


if __name__ == "__main__":
    main()
