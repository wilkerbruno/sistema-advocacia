"""
Baixa os pesos do modelo de IA local usado pelo Agente de IA jurídica
(Operação/Gestão/Negócios, ver app/routes/agente_ia.py) e pela Análise de
processo (resumo dos autos / rascunho de petição, ver
app/utils/analise_processo_ia.py).

Dois tamanhos disponíveis (ver PENDENCIAS.md, seção -6, para o registro
completo dessa decisão e o passo a passo de troca):

  - "pequeno" (padrão) — Qwen2.5-1.5B-Instruct, GGUF Q4_K_M, ~1,1 GB.
    Roda com folga em servidores com pouca RAM sobrando.
  - "grande" — Qwen3-4B-Instruct-2507, GGUF Q4_K_M, ~2,5 GB. Geração mais
    nova, com ganhos relatados de raciocínio e cobertura multilíngue, mas
    exige bem mais RAM (~2,5 GB por worker do gunicorn que carregar o
    modelo, contra ~1,1 GB do "pequeno") — só vale a pena se o servidor
    tiver folga de memória confirmada (ver checagem do painel do EasyPanel
    antes de trocar). O arquivo GGUF do "grande" vem do repositório da
    bartowski (quantizador GGUF confiável e muito usado na comunidade
    llama.cpp) — a Qwen não publica um repositório GGUF oficial próprio
    para essa variante "2507-Instruct", só os pesos originais.

Continua 100% local via llama-cpp-python nos dois casos: sem chave de API,
sem custo por mensagem, sem dado saindo do servidor.

⚠️ Trade-off consciente, não bug, nos dois tamanhos: um modelo desse porte
é bem mais fraco que uma API de ponta como a da Anthropic — alucina mais,
principalmente em raciocínio jurídico mais elaborado e em português (o
"grande" alucina menos que o "pequeno", mas não deixa de alucinar).

Também baixa, à parte, o modelo de EMBEDDING local (item "embedding" —
ver PENDENCIAS.md, seção -123, e app/utils/ia_local.py::gerar_embeddings_lote):
um modelo bem menor (~120 MB, baseado em MiniLM/BERT, multilíngue —
suporta português), usado só para gerar o Índice vetorial da indexação de
documentos quando a empresa NÃO tem chave do Gemini cadastrada — antes
dessa seção, busca semântica só existia com chave do Gemini; agora
funciona de graça pra qualquer empresa. Roda pela MESMA biblioteca já
instalada pro chat (llama-cpp-python), nenhuma dependência nova.

Uso:
    python baixar_modelo_ia_local.py              # baixa o modelo de chat "pequeno" (padrão)
    python baixar_modelo_ia_local.py grande        # baixa o modelo de chat "grande"
    python baixar_modelo_ia_local.py embedding     # baixa o modelo de embedding
    IA_LOCAL_MODELO_TAMANHO=grande python baixar_modelo_ia_local.py   # mesma coisa, via variável de ambiente

Idempotente: se o arquivo já existe com o tamanho esperado, não baixa de
novo. O Dockerfile roda este script automaticamente durante o build, duas
vezes: sem argumento nenhum (baixa o modelo de chat "pequeno", que é o
padrão de produção hoje) e com "embedding" (best-effort — se o download do
modelo de embedding falhar, o build NÃO quebra por causa disso, só a busca
semântica sem chave do Gemini fica indisponível, degradando exatamente como
já degrada hoje sem chave nenhuma configurada — ver
app/utils/indexacao_documentos.py). Trocar para o modelo de chat "grande" em
produção exige editar a linha do Dockerfile que chama este script, além de
ajustar `IA_LOCAL_MODELO_PATH`, o número de workers do gunicorn e
`IA_LOCAL_CONTEXT_SIZE` (ver PENDENCIAS.md, seção -6, para o passo a passo
completo com os números certos de cada arquivo).

⚠️ Repositório/arquivo do modelo de embedding pesquisado, mas **não
confirmado por um download real a partir deste ambiente de geração de
código** (rede de saída restrita, mesma ressalva já registrada para outras
integrações deste projeto — ver PENDENCIAS.md, seção -123). Se o nome do
arquivo tiver mudado no Hugging Face, o download falha com uma mensagem
clara (nunca finge sucesso) — troque o `repo`/`arquivo` abaixo por uma
variante atual do mesmo modelo (qualquer conversão GGUF do
"paraphrase-multilingual-MiniLM-L12-v2" serve, é um modelo bem conhecido e
tem várias cópias/quantizações publicadas).
"""
import os
import sys
import urllib.request

MODELOS = {
    "pequeno": {
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "arquivo": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "tamanho_minimo": 1_000_000_000,  # ~1 GB
        "tamanho_legivel": "~1,1 GB",
    },
    "grande": {
        "repo": "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF",
        "arquivo": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "tamanho_minimo": 2_300_000_000,  # ~2,3 GB
        "tamanho_legivel": "~2,5 GB",
    },
    "embedding": {
        "repo": "second-state/paraphrase-multilingual-MiniLM-L12-v2-GGUF",
        "arquivo": "paraphrase-multilingual-MiniLM-L12-v2-Q8_0.gguf",
        "tamanho_minimo": 90_000_000,  # ~90-130 MB dependendo da quantização
        "tamanho_legivel": "~130 MB",
    },
}

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DESTINO_DIR = os.path.join(BASE_DIR, "app", "ia_local", "modelos")


def _formatar_mb(num_bytes):
    return f"{num_bytes / (1024 * 1024):.0f} MB"


def baixar(tamanho=None):
    tamanho = tamanho or os.environ.get("IA_LOCAL_MODELO_TAMANHO", "pequeno")
    if tamanho not in MODELOS:
        print(f"Tamanho de modelo desconhecido: '{tamanho}'. Opções válidas: {', '.join(MODELOS)}.")
        sys.exit(1)

    info = MODELOS[tamanho]
    repo, arquivo, tamanho_minimo = info["repo"], info["arquivo"], info["tamanho_minimo"]
    url = f"https://huggingface.co/{repo}/resolve/main/{arquivo}"
    destino = os.path.join(DESTINO_DIR, arquivo)

    os.makedirs(DESTINO_DIR, exist_ok=True)

    if os.path.isfile(destino) and os.path.getsize(destino) >= tamanho_minimo:
        print(f"Modelo '{tamanho}' já presente em {destino} ({_formatar_mb(os.path.getsize(destino))}) — nada a fazer.")
        return

    print(f"Baixando modelo '{tamanho}' ({arquivo}) de {url} ...")
    print(f"Isso é {info['tamanho_legivel']} — pode demorar alguns minutos dependendo da conexão.")

    def _progresso(num_blocos, tamanho_bloco, tamanho_total):
        baixado = num_blocos * tamanho_bloco
        if tamanho_total > 0:
            pct = min(100, baixado * 100 // tamanho_total)
            sys.stdout.write(f"\r  {pct}% ({_formatar_mb(baixado)} / {_formatar_mb(tamanho_total)})")
        else:
            sys.stdout.write(f"\r  {_formatar_mb(baixado)} baixados")
        sys.stdout.flush()

    destino_temporario = destino + ".parcial"
    try:
        urllib.request.urlretrieve(url, destino_temporario, reporthook=_progresso)
        print()
    except Exception as e:
        if os.path.isfile(destino_temporario):
            os.remove(destino_temporario)
        print(f"\nFalha ao baixar o modelo: {e}")
        sys.exit(1)

    tamanho_final = os.path.getsize(destino_temporario)
    if tamanho_final < tamanho_minimo:
        os.remove(destino_temporario)
        print(
            f"Download incompleto/corrompido: só {_formatar_mb(tamanho_final)} "
            f"(esperado pelo menos {_formatar_mb(tamanho_minimo)}). Tente de novo."
        )
        sys.exit(1)

    os.replace(destino_temporario, destino)
    print(f"Modelo '{tamanho}' salvo em {destino} ({_formatar_mb(tamanho_final)}). Pronto para uso.")
    if tamanho == "grande":
        print(
            "Lembrete: baixar o arquivo não é o suficiente — ainda falta apontar "
            "IA_LOCAL_MODELO_PATH para este arquivo, ajustar o número de workers do "
            "gunicorn e IA_LOCAL_CONTEXT_SIZE. Ver PENDENCIAS.md, seção -6."
        )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    baixar(arg)
