"""
Janela de configuração do Agente Local (tkinter — já vem com o Python
padrão do Windows, sem dependência extra) — aparece sozinha na primeira
vez que o agente instalado roda (sem configuração salva ainda em
config_store.py), e também pode ser reaberta pelo menu do ícone da
bandeja ("Configurar...") a qualquer momento.

Só pede de cara o essencial (endereço do JusControl + token de
pareamento); os campos do certificado e de cada tribunal-piloto (PJe,
Projudi, e-SAJ) ficam atrás de um botão "Configurações avançadas", cada
um abrindo sua PRÓPRIA janela pequena — em vez de uma lista enorme
dentro da janela principal — porque a maioria dos advogados só vai
colar o token e pronto; o certificado e os dados de tribunal só são
preenchidos quando o piloto de um tribunal específico estiver pronto
pra uso real.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config_store
from cliente_api import ClienteJusControl, ErroApiJusControl


def abrir_wizard_configuracao(dados_iniciais=None):
    """
    Bloqueia até a janela fechar. Devolve o dict salvo se o usuário
    clicou em "Salvar" (já persistido em config_store), ou None se
    cancelou/fechou sem salvar.
    """
    dados = dict(dados_iniciais or config_store.carregar())
    resultado = {"salvo": None}

    janela = tk.Tk()
    janela.title("Configurar Agente Local — JusControl")
    janela.resizable(False, False)

    quadro = ttk.Frame(janela, padding=16)
    quadro.grid(sticky="nsew")

    ttk.Label(quadro, text="Configuração do Agente Local", font=("Segoe UI", 12, "bold")).grid(
        column=0, row=0, columnspan=2, sticky="w", pady=(0, 10))

    campos_var = {}

    def _linha(rotulo, chave, linha, senha=False, largura=42):
        ttk.Label(quadro, text=rotulo).grid(column=0, row=linha, sticky="w", pady=3)
        var = tk.StringVar(value=str(dados.get(chave, "")))
        entrada = ttk.Entry(quadro, textvariable=var, width=largura, show="*" if senha else "")
        entrada.grid(column=1, row=linha, sticky="we", pady=3, padx=(8, 0))
        campos_var[chave] = var
        return entrada

    _linha("Endereço do JusControl", "juscontrol_url", 1)
    _linha("Token de pareamento", "token_pareamento", 2, senha=True)

    ttk.Label(quadro, text="Cole o token gerado na tela \"Meu agente local\" do JusControl.",
              foreground="#666", font=("Segoe UI", 8)).grid(column=1, row=3, sticky="w")

    iniciar_var = tk.BooleanVar(value=bool(dados.get("iniciar_com_windows", True)))
    ttk.Checkbutton(quadro, text="Iniciar automaticamente com o Windows",
                    variable=iniciar_var).grid(column=0, row=4, columnspan=2, sticky="w", pady=(10, 0))

    # Garante que TODOS os campos avançados existem em campos_var desde já
    # (mesmo antes de qualquer sub-janela ser aberta) — é o que _coletar()
    # e config_store.salvar() esperam encontrar.
    for chave in (
        "certificado_pfx_caminho", "certificado_pfx_senha",
        "pje_tribunal", "pje_instancia", "pje_id_consultante", "pje_senha_consultante", "pje_url_wsdl",
        "projudi_id_consultante", "projudi_senha_consultante", "projudi_url_wsdl",
        "esaj_id_consultante", "esaj_senha_consultante", "esaj_url_wsdl",
    ):
        campos_var[chave] = tk.StringVar(value=str(dados.get(chave, "")))

    # ---------------- Sub-janelas de "Configurações avançadas" ----------------
    # Cada seção (Certificado, PJe, Projudi, e-SAJ) abre na SUA PRÓPRIA
    # janela pequena, em vez de empilhar tudo numa lista só — evita a
    # janela principal virar uma tela enorme de rolar.

    def _abrir_secao(titulo, definicoes, chave_status=None):
        """`definicoes` é uma lista de (rótulo, chave, é_senha). Copia os
        valores atuais de campos_var pra uma janela nova; só grava de volta
        em campos_var (a configuração de verdade só é salva em disco quando
        o botão "Salvar" da janela principal é clicado, como sempre foi)."""
        sub = tk.Toplevel(janela)
        sub.title(titulo)
        sub.resizable(False, False)
        sub.transient(janela)

        quadro_sub = ttk.Frame(sub, padding=16)
        quadro_sub.grid(sticky="nsew")
        ttk.Label(quadro_sub, text=titulo, font=("Segoe UI", 10, "bold")).grid(
            column=0, row=0, columnspan=2, sticky="w", pady=(0, 8))

        vars_locais = {}
        linha = 1
        for rotulo, chave, senha in definicoes:
            ttk.Label(quadro_sub, text=rotulo).grid(column=0, row=linha, sticky="w", pady=3)
            var = tk.StringVar(value=campos_var[chave].get())
            ttk.Entry(quadro_sub, textvariable=var, width=42, show="*" if senha else "").grid(
                column=1, row=linha, sticky="we", pady=3, padx=(8, 0))
            vars_locais[chave] = var
            linha += 1

        def _ok():
            for chave, var in vars_locais.items():
                campos_var[chave].set(var.get().strip())
            _atualizar_status()
            sub.destroy()

        def _fechar_sem_salvar():
            sub.destroy()

        quadro_botoes_sub = ttk.Frame(quadro_sub)
        quadro_botoes_sub.grid(column=0, row=linha, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(quadro_botoes_sub, text="Cancelar", command=_fechar_sem_salvar).pack(side="left", padx=(0, 8))
        ttk.Button(quadro_botoes_sub, text="OK", command=_ok).pack(side="left")

        sub.protocol("WM_DELETE_WINDOW", _fechar_sem_salvar)
        sub.grab_set()
        sub.update_idletasks()
        sub.tk.call("tk::PlaceWindow", sub._w, "center")
        sub.wait_window()

    def _abrir_secao_certificado():
        sub = tk.Toplevel(janela)
        sub.title("Certificado digital (A1)")
        sub.resizable(False, False)
        sub.transient(janela)

        quadro_sub = ttk.Frame(sub, padding=16)
        quadro_sub.grid(sticky="nsew")
        ttk.Label(quadro_sub, text="Certificado digital (A1)", font=("Segoe UI", 10, "bold")).grid(
            column=0, row=0, columnspan=2, sticky="w", pady=(0, 8))

        caminho_var = tk.StringVar(value=campos_var["certificado_pfx_caminho"].get())
        senha_var = tk.StringVar(value=campos_var["certificado_pfx_senha"].get())

        def _escolher_arquivo():
            caminho = filedialog.askopenfilename(
                title="Selecione o certificado (.pfx/.p12)",
                filetypes=[("Certificado digital", "*.pfx *.p12"), ("Todos os arquivos", "*.*")],
            )
            if caminho:
                caminho_var.set(caminho)

        ttk.Label(quadro_sub, text="Certificado (.pfx/.p12)").grid(column=0, row=1, sticky="w", pady=3)
        linha_cert = ttk.Frame(quadro_sub)
        linha_cert.grid(column=1, row=1, sticky="we", pady=3, padx=(8, 0))
        ttk.Entry(linha_cert, textvariable=caminho_var, width=32).pack(side="left")
        ttk.Button(linha_cert, text="Procurar...", command=_escolher_arquivo).pack(side="left", padx=(4, 0))

        ttk.Label(quadro_sub, text="Senha do certificado").grid(column=0, row=2, sticky="w", pady=3)
        ttk.Entry(quadro_sub, textvariable=senha_var, width=42, show="*").grid(
            column=1, row=2, sticky="we", pady=3, padx=(8, 0))

        def _ok():
            campos_var["certificado_pfx_caminho"].set(caminho_var.get().strip())
            campos_var["certificado_pfx_senha"].set(senha_var.get().strip())
            _atualizar_status()
            sub.destroy()

        def _fechar_sem_salvar():
            sub.destroy()

        quadro_botoes_sub = ttk.Frame(quadro_sub)
        quadro_botoes_sub.grid(column=0, row=3, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(quadro_botoes_sub, text="Cancelar", command=_fechar_sem_salvar).pack(side="left", padx=(0, 8))
        ttk.Button(quadro_botoes_sub, text="OK", command=_ok).pack(side="left")

        sub.protocol("WM_DELETE_WINDOW", _fechar_sem_salvar)
        sub.grab_set()
        sub.update_idletasks()
        sub.tk.call("tk::PlaceWindow", sub._w, "center")
        sub.wait_window()

    def _abrir_secao_pje():
        _abrir_secao("PJe", [
            ("Tribunal PJe (ex: trt2, tjrj)", "pje_tribunal", False),
            ("Instância (ex: 1g, 2g)", "pje_instancia", False),
            ("ID consultante (se exigido)", "pje_id_consultante", False),
            ("Senha consultante", "pje_senha_consultante", True),
            ("URL do WSDL (confirme antes de usar)", "pje_url_wsdl", False),
        ])

    def _abrir_secao_projudi():
        _abrir_secao("Projudi", [
            ("ID consultante", "projudi_id_consultante", False),
            ("Senha consultante", "projudi_senha_consultante", True),
            ("URL do WSDL (obrigatório — obtenha com o tribunal)", "projudi_url_wsdl", False),
        ])

    def _abrir_secao_esaj():
        _abrir_secao("e-SAJ (mais incerto — pode não existir nesse tribunal)", [
            ("ID consultante", "esaj_id_consultante", False),
            ("Senha consultante", "esaj_senha_consultante", True),
            ("URL do WSDL (obrigatório — obtenha com o tribunal)", "esaj_url_wsdl", False),
        ])

    # ---------------- Seção avançada: um botão por assunto, recolhida por padrão ----------------
    quadro_avancado = ttk.LabelFrame(quadro, text="Avançado — certificado e tribunais (piloto)", padding=10)

    status_secoes = {}

    def _linha_secao(rotulo_botao, comando, linha, chave_status_texto):
        ttk.Button(quadro_avancado, text=rotulo_botao, width=22, command=comando).grid(
            column=0, row=linha, sticky="w", pady=3)
        var_status = tk.StringVar(value="")
        ttk.Label(quadro_avancado, textvariable=var_status, foreground="#666").grid(
            column=1, row=linha, sticky="w", pady=3, padx=(10, 0))
        status_secoes[chave_status_texto] = var_status

    _linha_secao("Certificado digital...", _abrir_secao_certificado, 0, "certificado")
    _linha_secao("PJe...", _abrir_secao_pje, 1, "pje")
    _linha_secao("Projudi...", _abrir_secao_projudi, 2, "projudi")
    _linha_secao("e-SAJ...", _abrir_secao_esaj, 3, "esaj")

    ttk.Label(
        quadro_avancado,
        text="Preencha só o(s) tribunal(is) que for usar de verdade — cada um abre numa janela à parte.",
        foreground="#666", font=("Segoe UI", 8), wraplength=340, justify="left",
    ).grid(column=0, row=4, columnspan=2, sticky="w", pady=(6, 0))

    def _atualizar_status():
        status_secoes["certificado"].set(
            "configurado" if campos_var["certificado_pfx_caminho"].get() else "não configurado")
        status_secoes["pje"].set(
            "configurado" if (campos_var["pje_tribunal"].get() or campos_var["pje_url_wsdl"].get())
            else "não configurado")
        status_secoes["projudi"].set(
            "configurado" if campos_var["projudi_url_wsdl"].get() else "não configurado")
        status_secoes["esaj"].set(
            "configurado" if campos_var["esaj_url_wsdl"].get() else "não configurado")

    _atualizar_status()

    avancado_aberto = tk.BooleanVar(value=False)

    def _alternar_avancado():
        if avancado_aberto.get():
            quadro_avancado.grid_remove()
            avancado_aberto.set(False)
            botao_avancado.config(text="▸ Mostrar configurações avançadas")
        else:
            quadro_avancado.grid(column=0, row=6, columnspan=2, sticky="we", pady=(10, 0))
            avancado_aberto.set(True)
            botao_avancado.config(text="▾ Ocultar configurações avançadas")

    botao_avancado = ttk.Button(quadro, text="▸ Mostrar configurações avançadas", command=_alternar_avancado)
    botao_avancado.grid(column=0, row=5, columnspan=2, sticky="w", pady=(10, 0))

    status_var = tk.StringVar(value="")
    ttk.Label(quadro, textvariable=status_var, foreground="#a33").grid(
        column=0, row=7, columnspan=2, sticky="w", pady=(10, 0))

    def _coletar():
        coletado = {chave: var.get().strip() for chave, var in campos_var.items()}
        coletado["intervalo_polling_segundos"] = int(dados.get("intervalo_polling_segundos", 60))
        coletado["iniciar_com_windows"] = bool(iniciar_var.get())
        return coletado

    def _testar_conexao():
        coletado = _coletar()
        if not coletado["juscontrol_url"] or not coletado["token_pareamento"]:
            status_var.set("Preencha o endereço e o token antes de testar.")
            return
        try:
            cliente = ClienteJusControl(coletado["juscontrol_url"], coletado["token_pareamento"])
            info = cliente.ping()
            messagebox.showinfo("Conexão OK", f"Conectado como {info.get('usuario')} "
                                                f"(agente \"{info.get('apelido')}\").")
            status_var.set("")
        except ErroApiJusControl as e:
            status_var.set(str(e))
        except Exception as e:
            status_var.set(f"Não foi possível conectar: {e}")

    def _salvar():
        coletado = _coletar()
        if not coletado["juscontrol_url"] or not coletado["token_pareamento"]:
            status_var.set("Endereço do JusControl e token de pareamento são obrigatórios.")
            return
        salvo = config_store.salvar(coletado)
        resultado["salvo"] = salvo
        janela.destroy()

    def _cancelar():
        janela.destroy()

    quadro_botoes = ttk.Frame(quadro)
    quadro_botoes.grid(column=0, row=8, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(quadro_botoes, text="Testar conexão", command=_testar_conexao).pack(side="left", padx=(0, 8))
    ttk.Button(quadro_botoes, text="Cancelar", command=_cancelar).pack(side="left", padx=(0, 8))
    ttk.Button(quadro_botoes, text="Salvar", command=_salvar).pack(side="left")

    janela.protocol("WM_DELETE_WINDOW", _cancelar)
    janela.eval("tk::PlaceWindow . center")
    janela.mainloop()

    return resultado["salvo"]


if __name__ == "__main__":
    resultado = abrir_wizard_configuracao()
    print("Configuração salva." if resultado else "Cancelado pelo usuário.")
