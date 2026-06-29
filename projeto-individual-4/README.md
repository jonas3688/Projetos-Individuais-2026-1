# HabitaData — Pipeline de UDA para Conjuntura Habitacional

Pipeline de Análise de Dados Não Estruturados (UDA) que extrai automaticamente métricas operacionais de incorporadoras habitacionais a partir de PDFs de Prévias Operacionais e Releases de Resultados, disponibilizando os dados via API REST.

## Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                    FONTES DE DADOS                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐              │
│  │ Upload   │  │ URL      │  │ Crawler RI   │              │
│  │ Manual   │  │ Remota   │  │ (MRV, Cury,  │              │
│  │ (PDF)    │  │ (PDF)    │  │  Direcional, │              │
│  │          │  │          │  │  Tenda, etc.) │              │
│  └────┬─────┘  └────┬─────┘  └──────┬───────┘              │
│       │              │               │                      │
│       └──────────────┼───────────────┘                      │
│                      ▼                                      │
│  ┌──────────────────────────────────────┐                   │
│  │  1. IDEMPOTÊNCIA (SHA-256)           │                   │
│  │  Calcula hash → Verifica catálogo    │                   │
│  │  Se já existe → SKIP (sem custo)     │                   │
│  └──────────────┬───────────────────────┘                   │
│                 ▼                                           │
│  ┌──────────────────────────────────────┐                   │
│  │  2. PARSING (PyMuPDF / Full-Scan)    │                   │
│  │  Extrai texto integral do PDF        │                   │
│  └──────────────┬───────────────────────┘                   │
│                 ▼                                           │
│  ┌──────────────────────────────────────┐                   │
│  │  3. EXTRAÇÃO SEMÂNTICA               │                   │
│  │  Gemini → OpenAI → Fallback          │                   │
│  │  Prompt com regras de blindagem      │                   │
│  └──────────────┬───────────────────────┘                   │
│                 ▼                                           │
│  ┌──────────────────────────────────────┐                   │
│  │  4. CONTRATO SEMÂNTICO (Pydantic)    │                   │
│  │  Valida tipos, ranges, nulls         │                   │
│  │  Rejeita dados inválidos             │                   │
│  └──────────────┬───────────────────────┘                   │
│                 ▼                                           │
│  ┌──────────────────────────────────────┐                   │
│  │  5. PERSISTÊNCIA + LINHAGEM          │                   │
│  │  SQLite: documentos + dados_conj.    │                   │
│  │  FK + linhagem_trecho por registro   │                   │
│  └──────────────┬───────────────────────┘                   │
│                 ▼                                           │
│  ┌──────────────────────────────────────┐                   │
│  │  6. API REST (FastAPI)               │                   │
│  │  GET /api/conjuntura?empresa=MRV     │                   │
│  │  GET /api/catalog                    │                   │
│  │  POST /api/ingest/file|url|crawl     │                   │
│  └──────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

## Decisões de Arquitetura

### Estratégia de Parsing: Full-Scan

As Prévias Operacionais das incorporadoras têm em média 1-5 páginas. O custo de tokens é baixo, e o Full-Scan garante que nenhuma tabela operacional seja perdida por um chunking inadequado. Para relatórios maiores (>20 páginas), seria recomendável migrar para Chunking Semântico baseado em títulos.

### Extração Semântica: LLM com Fallback

O pipeline tenta, em ordem:
1. **Gemini** (via `google-generativeai` com `response_schema` nativo)
2. **OpenAI** (via `beta.chat.completions.parse` com Structured Outputs)
3. **Fallback Heurístico** (regex + análise textual)

O fallback é honesto: quando o PDF contém apenas variações percentuais (como o Boletim de Conjuntura), ele retorna `null` nos campos de VGV em vez de inventar dados.

### Contrato Semântico: Pydantic

Toda saída do LLM passa por validação rigorosa via Pydantic (`schema.py`):
- Ano deve estar entre 2000 e 2100
- Trimestre deve estar entre 1 e 4
- VGV e unidades não podem ser negativos
- A linhagem deve ter pelo menos 5 caracteres
- O nome da empresa é normalizado para caixa alta

### Idempotência: SHA-256

Antes de enviar qualquer PDF para o LLM (gerando custos de API), o pipeline calcula o hash SHA-256 do arquivo e verifica no catálogo. Se o hash já existe, o processamento é cancelado imediatamente.

### Linhagem de Dados (Data Lineage)

Cada registro no banco possui:
- `documento_id` → FK para a tabela `documentos` (com hash, URL de origem, data)
- `linhagem_trecho` → trecho exato do texto do PDF que justifica a extração

## Estrutura do Projeto

```
projeto-individual-4/
├── main.py                 # API FastAPI (endpoints REST)
├── pipeline.py             # Motor de ingestão, hash, LLM, crawler
├── schema.py               # Contrato Semântico (Pydantic)
├── database.py             # Persistência SQLite + Linhagem
├── test_pipeline.py        # Suite de testes TDD (34 testes)
├── requirements.txt        # Dependências Python
├── plano_de_implementacao.md  # Especificação original do desafio
└── exemplo_Boletim_Conjuntura_2025_3T.pdf  # PDF de exemplo
```

## Como Executar

### 1. Criar ambiente virtual e instalar dependências
```bash
cd projeto-individual-4
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. (Opcional) Configurar chaves de LLM
```bash
export GEMINI_API_KEY="sua_chave_aqui"
# ou
export OPENAI_API_KEY="sua_chave_aqui"
```
> Sem chaves configuradas, o pipeline usa o motor de fallback heurístico.

### 3. Rodar os testes
```bash
python -m pytest test_pipeline.py -v
```

### 4. Iniciar o servidor
```bash
uvicorn main:app --reload
```
Acesse a documentação interativa em: http://127.0.0.1:8000/docs

## Endpoints da API

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Health check |
| `GET` | `/api/conjuntura` | Dados com filtros `?empresa=MRV&ano=2025&trimestre=3` |
| `GET` | `/api/catalog` | Catálogo de documentos processados |
| `POST` | `/api/ingest/file` | Upload de PDF |
| `POST` | `/api/ingest/url` | Ingestão por URL remota |
| `POST` | `/api/ingest/crawl` | Disparo do crawler de RI |

### Exemplos de uso

```bash
# Consultar dados da MRV no 3T25
curl "http://127.0.0.1:8000/api/conjuntura?empresa=MRV&ano=2025&trimestre=3"

# Enviar PDF para processamento
curl -X POST "http://127.0.0.1:8000/api/ingest/file" \
  -F "file=@exemplo_Boletim_Conjuntura_2025_3T.pdf"

# Ver catálogo de linhagem
curl "http://127.0.0.1:8000/api/catalog"

# Disparar crawler
curl -X POST "http://127.0.0.1:8000/api/ingest/crawl"
```

## Testes (TDD)

A suite cobre **34 testes** organizados em 4 classes:

| Classe | Testes | Cobertura |
|--------|--------|-----------|
| `TestContratoSemantico` | 9 | Validações Pydantic (rejeição de dados inválidos) |
| `TestDatabase` | 7 | CRUD, idempotência por hash, CHECK constraints |
| `TestPipeline` | 10 | Hash, parsing, fallback, pipeline e2e, idempotência |
| `TestAPI` | 8 | Endpoints REST, upload, filtros, duplicatas |

Cada teste roda em um banco SQLite temporário isolado.

## Tecnologias

- **Python 3.10+**
- **FastAPI** + **Uvicorn** — API REST
- **PyMuPDF (fitz)** — Parsing de PDF
- **Pydantic v2** — Contrato Semântico
- **SQLite3** — Banco relacional com linhagem
- **BeautifulSoup4** — Crawler de RI
- **pytest** + **httpx** — Testes automatizados
