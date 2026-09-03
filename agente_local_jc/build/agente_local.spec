# -*- mode: python ; coding: utf-8 -*-
"""
Spec do PyInstaller pro Agente Local (modo instalado, com ícone na
bandeja — tray_app.py). Gera um único .exe ("onefile"), sem console
(--windowed), pra não abrir nenhuma janela preta de terminal.

Rodado automaticamente pelo GitHub Actions (ver
.github/workflows/build-agente-local.yml) — pra rodar manualmente:

    cd agente_local_jc
    pip install -r requirements-build.txt
    python build/gerar_icone.py
    pyinstaller build/agente_local.spec --distpath build/dist --workpath build/work

zeep depende de alguns módulos que o PyInstaller às vezes não detecta
sozinho (parsers de schema XML carregados dinamicamente) — os
hiddenimports abaixo cobrem os casos conhecidos; se uma versão nova do
zeep mudar isso, o sintoma é `ModuleNotFoundError` só ao RODAR o .exe
(não ao compilar), então vale testar o instalador de verdade depois de
atualizar a dependência.
"""
import os

AQUI = os.path.dirname(os.path.abspath(SPEC))
RAIZ = os.path.dirname(AQUI)

a = Analysis(
    [os.path.join(RAIZ, "tray_app.py")],
    pathex=[RAIZ],
    binaries=[],
    datas=[],
    hiddenimports=[
        "zeep.wsdl.bindings.soap",
        "zeep.wsdl.bindings.http",
        "lxml.etree",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="JusControlAgente",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # sem janela de terminal — é um programa de bandeja
    icon=os.path.join(AQUI, "icone.ico"),
)
