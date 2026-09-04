"""
Janela de configuração do Agente Local (tkinter — já vem com o Python
padrão do Windows, sem dependência extra) — aparece sozinha na primeira
vez que o agente instalado roda (sem configuração salva ainda em
config_store.py), e também pode ser reaberta pelo menu do ícone da
bandeja ("Configurar...") a qualquer momento.

Só pede de cara o essencial (endereço do JusControl + token de
pareamento); os campos do certificado e do tribunal-piloto ficam numa
seção "Avançado" recolhida, porque a maioria dos advogados vai só
colar o token e pronto — o certificado pode ser configurado depois,
quando o piloto de um tribunal específico estiver pronto pra uso real.
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

    # ---------------- Seção avançada (certificado + tribunais-piloto) ----------------
    quadro_avancado = ttk.LabelFrame(quadro, text="Avançado — certificado e tribunais (piloto)", padding=10)

    def _linha_avancada(rotulo, chave, linha, senha=False):
        ttk.Label(quadro_avancado, text=rotulo).grid(column=0, row=linha, sticky="w", pady=2)
        var = tk.StringVar(value=str(dados.get(chave, "")))
        entrada = ttk.Entry(quadro_avancado, textvariable=var, width=38, show="*" if senha else "")
        entrada.grid(column=1, row=linha, sticky="we", pady=2, padx=(8, 0))
        campos_var[chave] = var
        return entrada

    def _escolher_arquivo():
        caminho = filedialog.askopenfilename(
            title="Selecione o certificado (.pfx/.p12)",
            filetypes=[("Certificado digital", "*.pfx *.p12"), ("Todos os arquivos", "*.*")],
        )
        if caminho:
            campos_var["certificado_pfx_caminho"].set(caminho)

    ttk.Label(quadro_avancado, text="Certificado (.pfx/.p12)").grid(column=0, row=0, sticky="w", pady=2)
    linha_cert = ttk.Frame(quadro_avancado)
    linha_cert.grid(column=1, row=0, sticky="we", pady=2, padx=(8, 0))
    campos_var["certificado_pfx_caminho"] = tk.StringVar(value=str(dados.get("certificado_pfx_caminho", "")))
    ttk.Entry(linha_cert, textvariable=campos_var["certificado_pfx_caminho"], width=28).pack(side="left")
    ttk.Button(linha_cert, text="Procurar...", command=_escolher_arquivo).pack(side="left", padx=(4, 0))

    _linha_avancada("Senha do certificado", "certificado_pfx_senha", 1, senha=True)

    ttk.Label(quadro_avancado, text="PJe", font=("Segoe UI", 9, "bold")).grid(
        column=0, row=2, columnspan=2, sticky="w", pady=(8, 2))
    _linha_avancada("Tribunal PJe (ex: trt2, tjrj)", "pje_tribunal", 3)
    _linha_avancada("Instância (ex: 1g, 2g)", "pje_instancia", 4)
    _linha_avancada("ID consultante (se exigido)", "pje_id_consultante", 5)
    _linha_avancada("Senha consultante", "pje_senha_consultante", 6, senha=True)
    _linha_avancada("URL do WSDL (confirme antes de usar)", "pje_url_wsdl", 7)

    ttk.Label(quadro_avancado, text="Projudi", font=("Segoe UI", 9, "bold")).grid(
        column=0, row=8, columnspan=2, sticky="w", pady=(8, 2))
    _linha_avancada("ID consultante", "projudi_id_consultante", 9)
    _linha_avancada("Senha consultante", "projudi_senha_consultante", 10, senha=True)
    _linha_avancada("URL do WSDL (obrigatório — obtenha com o tribunal)", "projudi_url_wsdl", 11)

    ttk.Label(quadro_avancado, text="e-SAJ (mais incerto — pode não existir nesse tribunal)",
              font=("Segoe UI", 9, "bold")).grid(column=0, row=12, columnspan=2, sticky="w", pady=(8, 2))
    _linha_avancada("ID consultante", "esaj_id_consultante", 13)
    _linha_avancada("Senha consultante", "esaj_senha_consultante", 14, senha=True)
    _linha_avancada("URL do WSDL (obrigatório — obtenha com o tribunal)", "esaj_url_wsdl", 15)

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
