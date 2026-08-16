"""
Baixa os pesos do modelo de IA local usado pelo Agente de IA jurídica
(Operação/Gestão/Negócios, ver app/routes/agente_ia.py e app/utils/ia_local.py).

Modelo escolhido: Qwen2.5-1.5B-Instruct, quantizado em GGUF (q4_k_m, ~1,1 GB).
1,5 bilhão de parâmetros — dentro do limite de até 2B pedido — rodando via
llama-cpp-python, 100% local: sem chave de API, sem custo por mensagem, sem
dado saindo do servidor. Foi escolhido entre os modelos pequenos por ter
suporte multilíngue melhor que a maioria (inclui português), o que importa
bastante aqui.

⚠️ Trade-off que já foi conversado e é uma escolha consciente, não um bug:
um modelo desse tamanho é bem mais fraco que uma API como a da Anthropic —
alucina mais, principalmente em raciocínio jurídico mais elaborado e em
português. Ver PENDENCIAS.md para o registro completo dessa decisão.

Uso:
    python baixar_modelo_ia_local.py

Idempotente: se o arquivo já existe com o tamanho esperado, não baixa de
novo. Roda automaticamente durante o build da imagem Docker (ver
Dockerfile) — só precisa rodar manualmente se for trocar o modelo, ou se
por algum motivo o arquivo não tiver entrado na imagem (ex: teste local
fora do Docker).
"""
import os
import sys
import urllib.request

REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
ARQUIVO = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
URL = f"https://huggingface.co/{REPO}/resolve/main/{ARQUIVO}"

# Tamanho esperado do arquivo, em bytes (~1,12 GB) — usado só como checagem
# grosseira de download incompleto/corrompido, não precisa ser exato.
TAMANHO_MINIMO_ESPERADO = 1_000_000_000  # 1 GB

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DESTINO_DIR = os.path.join(BASE_DIR, "app", "ia_local", "modelos")
DESTINO = os.path.join(DESTINO_DIR, ARQUIVO)


def _formatar_mb(num_bytes):
    return f"{num_bytes / (1024 * 1024):.0f} MB"


def baixar():
    os.makedirs(DESTINO_DIR, exist_ok=True)

    if os.path.isfile(DESTINO) and os.path.getsize(DESTINO) >= TAMANHO_MINIMO_ESPERADO:
        print(f"Modelo já presente em {DESTINO} ({_formatar_mb(os.path.getsize(DESTINO))}) — nada a fazer.")
        return

    print(f"Baixando {ARQUIVO} de {URL} ...")
    print("Isso é ~1,1 GB — pode demorar alguns minutos dependendo da conexão.")

    def _progresso(num_blocos, tamanho_bloco, tamanho_total):
        baixado = num_blocos * tamanho_bloco
        if tamanho_total > 0:
            pct = min(100, baixado * 100 // tamanho_total)
            sys.stdout.write(f"\r  {pct}% ({_formatar_mb(baixado)} / {_formatar_mb(tamanho_total)})")
        else:
            sys.stdout.write(f"\r  {_formatar_mb(baixado)} baixados")
        sys.stdout.flush()

    destino_temporario = DESTINO + ".parcial"
    try:
        urllib.request.urlretrieve(URL, destino_temporario, reporthook=_progresso)
        print()
    except Exception as e:
        if os.path.isfile(destino_temporario):
            os.remove(destino_temporario)
        print(f"\nFalha ao baixar o modelo: {e}")
        sys.exit(1)

    tamanho_final = os.path.getsize(destino_temporario)
    if tamanho_final < TAMANHO_MINIMO_ESPERADO:
        os.remove(destino_temporario)
        print(
            f"Download incompleto/corrompido: só {_formatar_mb(tamanho_final)} "
            f"(esperado pelo menos {_formatar_mb(TAMANHO_MINIMO_ESPERADO)}). Tente de novo."
        )
        sys.exit(1)

    os.replace(destino_temporario, DESTINO)
    print(f"Modelo salvo em {DESTINO} ({_formatar_mb(tamanho_final)}). Pronto para uso.")


if __name__ == "__main__":
    baixar()
