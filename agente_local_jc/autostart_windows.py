"""
Liga/desliga o início automático do Agente Local junto com o Windows —
grava (ou remove) uma entrada no Registro do Windows, em
`HKEY_CURRENT_USER\\...\\Run` (não precisa de administrador, e só afeta
o usuário atual — cada advogado que usa o mesmo computador teria a
própria entrada).

Só funciona no Windows por natureza (`winreg` é uma lib só do Windows);
em qualquer outro sistema, as funções aqui simplesmente não fazem nada
e devolvem False — usado só pra permitir testar o resto do agente fora
do Windows sem quebrar em erro de import.
"""
import sys

NOME_ENTRADA = "JusControlAgenteLocal"

if sys.platform == "win32":
    import winreg

    _CHAVE = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def ativar(caminho_executavel):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CHAVE, 0, winreg.KEY_SET_VALUE) as chave:
            winreg.SetValueEx(chave, NOME_ENTRADA, 0, winreg.REG_SZ, f'"{caminho_executavel}" --minimizado')
        return True

    def desativar():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CHAVE, 0, winreg.KEY_SET_VALUE) as chave:
                winreg.DeleteValue(chave, NOME_ENTRADA)
        except FileNotFoundError:
            pass
        return True

    def esta_ativo():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CHAVE, 0, winreg.KEY_READ) as chave:
                winreg.QueryValueEx(chave, NOME_ENTRADA)
                return True
        except FileNotFoundError:
            return False

else:  # pragma: no cover - só relevante fora do Windows, pra não quebrar import
    def ativar(caminho_executavel):
        return False

    def desativar():
        return False

    def esta_ativo():
        return False
