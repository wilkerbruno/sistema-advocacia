"""
Agente Local do JusControl — modo INSTALADO (ícone na bandeja do
Windows, início automático, sem terminal nenhum aberto). É este arquivo
que vira o `.exe` distribuído pelo instalador (ver build/agente_local.spec
e build/instalador.iss) — para rodar/testar direto do código-fonte sem
instalar nada, use `main.py`.

Fluxo:
  1. Se não há configuração salva ainda (config_store.py), abre a janela
     de configuração (config_gui.py) pedindo o endereço do JusControl e
     o token de pareamento.
  2. Registra o início automático com o Windows, se marcado.
  3. Sobe o ícone na bandeja e, numa thread à parte, fica checando
     tarefas pendentes a cada N segundos (mesmo motor.py usado por
     main.py) — o ícone muda de cor conforme o status.
"""
import os
import sys
import threading
import time
from datetime import datetime

import pystray
from PIL import Image, ImageDraw

import config_store
import config_gui
import motor
import autostart_windows
from cliente_api import ClienteJusControl, ErroApiJusControl

NOME_APP = "Agente Local — JusControl"

_estado = {"status": "iniciando", "ultima_mensagem": "", "parar": False}
_lock = threading.Lock()


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
    # tkinter precisa rodar na thread principal — como o pystray já está
    # rodando o loop dele lá, abrir a janela aqui (chamada a partir do
    # menu, que o pystray despacha numa thread própria) funciona porque
    # cada chamada cria e destrói o próprio Tk() isoladamente.
    resultado = config_gui.abrir_wizard_configuracao()
    if resultado:
        _aplicar_autostart(resultado)
        _log("Configuração atualizada pelo usuário.")


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


def _aplicar_autostart(dados):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return  # só faz sentido registrar o .exe compilado, não o script Python cru
    caminho_exe = sys.executable
    if dados.get("iniciar_com_windows", True):
        autostart_windows.ativar(caminho_exe)
    else:
        autostart_windows.desativar()


def main():
    dados = config_store.carregar()
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

    thread = threading.Thread(target=_laco_verificacao, args=(icone,), daemon=True)
    thread.start()

    icone.run()


if __name__ == "__main__":
    main()
