"""
Tela de cadeado (OAB + senha local) mostrada toda vez que o Agente Local
inicia, QUANDO o advogado configurou um login local (ver login_local.py e
a seção "Login local" da tela de Configuração em config_gui.py). Sem essa
configuração, esta tela nunca aparece — o agente abre direto, como
sempre.

tkinter, mesma biblioteca já usada em config_gui.py (vem com o Python
padrão do Windows, sem dependência nova).
"""
import tkinter as tk
from tkinter import ttk

import login_local

MAX_TENTATIVAS = 5
# Pausa curta a cada tentativa errada — não é uma proteção forte contra
# força bruta de verdade (é só um programa local, sem servidor pra
# aplicar um bloqueio de tempo real), mas encarece um pouco tentar várias
# senhas na mão. Ver docstring de login_local.py sobre o que essa tela
# protege (acesso físico ao computador, não uma conta online). Usa
# `janela.after` (agendamento não-bloqueante do tkinter), nunca
# `time.sleep`, pra não travar o loop de eventos da janela enquanto
# espera.
PAUSA_MS_APOS_ERRO = 1500


def abrir_tela_bloqueio(dados):
    """
    Bloqueia até o usuário acertar a senha, cancelar, ou esgotar as
    tentativas. Devolve True se a senha bateu, False em qualquer outro
    caso (quem chama deve encerrar o programa quando devolver False —
    nunca seguir adiante sem confirmar a senha).
    """
    resultado = {"ok": False}
    tentativas_restantes = {"n": MAX_TENTATIVAS}

    janela = tk.Tk()
    janela.title("Agente Local — JusControl")
    janela.resizable(False, False)

    quadro = ttk.Frame(janela, padding=20)
    quadro.grid(sticky="nsew")

    ttk.Label(quadro, text="🔒 Agente Local bloqueado", font=("Segoe UI", 12, "bold")).grid(
        column=0, row=0, columnspan=2, sticky="w", pady=(0, 4))
    ttk.Label(quadro, text=f"OAB: {dados.get('oab_local', '')}", foreground="#666").grid(
        column=0, row=1, columnspan=2, sticky="w", pady=(0, 12))

    ttk.Label(quadro, text="Senha").grid(column=0, row=2, sticky="w", pady=3)
    senha_var = tk.StringVar()
    entrada_senha = ttk.Entry(quadro, textvariable=senha_var, width=30, show="*")
    entrada_senha.grid(column=1, row=2, sticky="we", pady=3, padx=(8, 0))
    entrada_senha.focus()

    status_var = tk.StringVar(value="")
    ttk.Label(quadro, textvariable=status_var, foreground="#a33", wraplength=300, justify="left").grid(
        column=0, row=3, columnspan=2, sticky="w", pady=(8, 0))

    def _entrar(event=None):
        senha = senha_var.get()
        if login_local.verificar_senha(senha, dados.get("oab_senha_hash"), dados.get("oab_senha_salt")):
            resultado["ok"] = True
            janela.destroy()
            return

        tentativas_restantes["n"] -= 1
        senha_var.set("")
        entrada_senha.config(state="disabled")
        botao_entrar.config(state="disabled")

        if tentativas_restantes["n"] <= 0:
            status_var.set("Muitas tentativas erradas — o Agente Local vai fechar.")
            janela.after(PAUSA_MS_APOS_ERRO, janela.destroy)
            return

        status_var.set(f"Senha incorreta — {tentativas_restantes['n']} tentativa(s) restante(s).")

        def _reabilitar():
            entrada_senha.config(state="normal")
            botao_entrar.config(state="normal")
            entrada_senha.focus()

        janela.after(PAUSA_MS_APOS_ERRO, _reabilitar)

    def _cancelar():
        janela.destroy()

    entrada_senha.bind("<Return>", _entrar)

    quadro_botoes = ttk.Frame(quadro)
    quadro_botoes.grid(column=0, row=4, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(quadro_botoes, text="Sair", command=_cancelar).pack(side="left", padx=(0, 8))
    botao_entrar = ttk.Button(quadro_botoes, text="Entrar", command=_entrar)
    botao_entrar.pack(side="left")

    janela.protocol("WM_DELETE_WINDOW", _cancelar)
    janela.eval("tk::PlaceWindow . center")
    janela.mainloop()

    return resultado["ok"]
