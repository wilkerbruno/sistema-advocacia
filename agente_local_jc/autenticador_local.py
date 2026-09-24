"""
Trava por autenticador (2FA) pra abrir a tela "Configurar..." do Agente
Local — pedido do usuário: "para o cara acessar a configuração do
aplicativo ele tem que autenticar com autenticador igual funciona hoje
para logar no sistema web".

Deliberadamente usa o MESMO autenticador (TOTP) já configurado na conta
JusControl do advogado, em vez de um segredo novo só pro agente — o
código digitado aqui é conferido pelo SERVIDOR
(app/routes/agente_local_api.py::verificar_autenticador), que já sabe de
qual conta é este agente através do próprio token de pareamento (nunca é
preciso informar e-mail nem OAB de novo pra isso). Por isso, abrir a
Configuração precisa de internet no momento — não tem como conferir isso
offline sem duplicar o segredo TOTP da conta aqui (decisão deliberada,
confirmada com o usuário).

Se este sistema JusControl não tiver a funcionalidade de autenticador
ligada (`TOTP_CIFRA_KEY` não configurada no servidor) ou o próprio
usuário ainda não tiver confirmado o autenticador da conta dele, a
Configuração abre direto, sem pedir código nenhum — não faz sentido o
agente exigir aqui algo que a própria conta web ainda não exige pra
logar (ver `status_autenticador` no servidor).

Falha de rede = acesso NEGADO (fail-closed), nunca liberado — permitir
abrir a Configuração só porque não deu pra confirmar o código destruiria
o propósito da trava (bastaria desligar o Wi-Fi pra pular a verificação).
A única exceção deliberada é quando o PRÓPRIO token de pareamento está
inválido/revogado: nesse caso a única forma de corrigir isso é reabrir
esta mesma tela de Configuração pra colar um token novo, então deixamos
abrir (ver ErroTokenInvalido abaixo) — não há prejuízo de segurança
adicional, porque um token inválido já não consegue buscar tarefa nem
dado nenhum de processo no servidor de qualquer forma.
"""
import tkinter as tk
from tkinter import ttk, messagebox

from cliente_api import ClienteJusControl, ErroApiJusControl, ErroTokenInvalido, ErroAutenticadorBloqueado

MAX_TENTATIVAS_TELA = 5


def pode_abrir_configuracao(dados, logar=None):
    """
    Devolve True se a tela de Configuração pode abrir agora. `logar` é
    uma função opcional (mensagem) -> None, pro chamador registrar o
    motivo no log do agente (ver tray_app.py).
    """
    logar = logar or (lambda mensagem: None)

    token = dados.get("token_pareamento")
    url = dados.get("juscontrol_url")
    if not token or not url:
        # Ainda não pareado — não tem contra o que conferir (é a própria
        # tela de Configuração o único jeito de colar o token pela
        # primeira vez), deixa abrir.
        return True

    cliente = ClienteJusControl(url, token)

    try:
        status = cliente.status_autenticador()
    except ErroTokenInvalido:
        logar("Token de pareamento inválido/revogado — abrindo Configuração mesmo assim "
              "(só assim dá pra colar um token novo).")
        return True
    except ErroApiJusControl as e:
        messagebox.showerror(
            "Sem conexão",
            f"Não foi possível confirmar o autenticador: {e}\n\n"
            "Conecte-se à internet e tente novamente — por segurança, a Configuração "
            "não abre sem confirmar o código.",
        )
        return False

    if not status.get("exigido"):
        return True

    return _pedir_codigo_e_validar(cliente, logar)


def _pedir_codigo_e_validar(cliente, logar):
    resultado = {"ok": False}
    tentativas_restantes = {"n": MAX_TENTATIVAS_TELA}

    janela = tk.Tk()
    janela.title("Confirmar autenticador — JusControl")
    janela.resizable(False, False)

    quadro = ttk.Frame(janela, padding=20)
    quadro.grid(sticky="nsew")

    ttk.Label(quadro, text="Confirme o autenticador", font=("Segoe UI", 12, "bold")).grid(
        column=0, row=0, columnspan=2, sticky="w", pady=(0, 4))
    ttk.Label(
        quadro, text="Digite o código do MESMO app autenticador usado pra logar no JusControl "
                     "(o código muda a cada 30 segundos).",
        foreground="#666", wraplength=320, justify="left",
    ).grid(column=0, row=1, columnspan=2, sticky="w", pady=(0, 12))

    ttk.Label(quadro, text="Código").grid(column=0, row=2, sticky="w", pady=3)
    codigo_var = tk.StringVar()
    entrada = ttk.Entry(quadro, textvariable=codigo_var, width=16)
    entrada.grid(column=1, row=2, sticky="w", pady=3, padx=(8, 0))
    entrada.focus()

    status_var = tk.StringVar(value="")
    ttk.Label(quadro, textvariable=status_var, foreground="#a33", wraplength=320, justify="left").grid(
        column=0, row=3, columnspan=2, sticky="w", pady=(8, 0))

    def _confirmar(event=None):
        codigo = codigo_var.get().strip()
        try:
            ok = cliente.verificar_autenticador(codigo)
        except ErroAutenticadorBloqueado as e:
            status_var.set(str(e))
            logar(f"Autenticador bloqueado temporariamente: {e}")
            janela.update()
            return
        except ErroTokenInvalido:
            # o token deixou de ser válido ENTRE o status_autenticador e
            # agora (ex.: revogado por outro dispositivo nesse meio-tempo)
            # — mesma decisão de pode_abrir_configuracao: deixa abrir.
            resultado["ok"] = True
            janela.destroy()
            return
        except ErroApiJusControl as e:
            status_var.set(f"Sem conexão: {e}")
            janela.update()
            return

        if ok:
            resultado["ok"] = True
            janela.destroy()
            return

        tentativas_restantes["n"] -= 1
        codigo_var.set("")
        if tentativas_restantes["n"] <= 0:
            status_var.set("Muitas tentativas erradas.")
            janela.update()
            janela.after(1200, janela.destroy)
            return
        status_var.set(f"Código inválido — {tentativas_restantes['n']} tentativa(s) restante(s).")

    def _cancelar():
        janela.destroy()

    entrada.bind("<Return>", _confirmar)

    quadro_botoes = ttk.Frame(quadro)
    quadro_botoes.grid(column=0, row=4, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(quadro_botoes, text="Cancelar", command=_cancelar).pack(side="left", padx=(0, 8))
    ttk.Button(quadro_botoes, text="Confirmar", command=_confirmar).pack(side="left")

    janela.protocol("WM_DELETE_WINDOW", _cancelar)
    janela.eval("tk::PlaceWindow . center")
    janela.mainloop()

    return resultado["ok"]
