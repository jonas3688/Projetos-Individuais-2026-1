"""
HabitaData UDA API — Camada de Serviço REST.

Endpoints:
  GET  /                       → Health check
  GET  /api/conjuntura         → Consulta dados com filtros
  GET  /api/catalog            → Catálogo de documentos processados
  POST /api/ingest/file        → Upload de PDF para processamento
  POST /api/ingest/url         → Processamento de PDF por URL remota
  POST /api/ingest/crawl       → Disparo do crawler de RI
"""

import os
import shutil
import tempfile
from typing import Optional

import requests
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

import database
import pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o banco de dados ao iniciar o servidor."""
    database.init_db()
    yield


app = FastAPI(
    title="HabitaData UDA API",
    description=(
        "Pipeline de Ingestão e Análise de Dados Não Estruturados (UDA) "
        "para Relatórios de Conjuntura do Setor Habitacional."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Endpoints ------------------------------------------------------------


@app.get("/", tags=["Health"])
async def health_check():
    """Retorna status do servidor e lista de endpoints disponíveis."""
    return {
        "status": "online",
        "service": "HabitaData UDA API",
        "version": "1.0.0",
        "endpoints": {
            "GET /api/conjuntura": "Dados estruturados com filtros por empresa/ano/trimestre",
            "GET /api/catalog": "Catálogo de documentos processados e linhagem",
            "POST /api/ingest/file": "Upload de PDF para extração",
            "POST /api/ingest/url": "Ingestão de PDF via URL remota",
            "POST /api/ingest/crawl": "Crawler de Centrais de RI",
        },
    }


@app.get("/api/conjuntura", tags=["Consulta"])
async def consultar_conjuntura(
    empresa: Optional[str] = Query(None, description="Nome da incorporadora (ex: MRV)"),
    ano: Optional[int] = Query(None, description="Ano (ex: 2025)"),
    trimestre: Optional[int] = Query(None, ge=1, le=4, description="Trimestre (1-4)"),
):
    """Retorna dados de lançamentos e vendas com filtros opcionais."""
    dados = database.buscar_dados_conjuntura(empresa, ano, trimestre)
    return {
        "count": len(dados),
        "filtros": {"empresa": empresa, "ano": ano, "trimestre": trimestre},
        "dados": dados,
    }


@app.get("/api/catalog", tags=["Catálogo"])
async def obter_catalogo():
    """Retorna o catálogo de todos os documentos processados com linhagem."""
    docs = database.obter_catalogo()
    return {"count": len(docs), "documentos": docs}


@app.post("/api/ingest/file", tags=["Ingestão"])
async def ingerir_arquivo(file: UploadFile = File(...)):
    """Upload manual de PDF para extração pelo pipeline."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos .pdf são aceitos.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        ok, msg = pipeline.processar_relatorio(tmp_path, url_origem="upload_manual")
        status = "processado" if ok else "ignorado"
        return {"status": status, "detail": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no pipeline: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/ingest/url", tags=["Ingestão"])
async def ingerir_por_url(url: str = Query(..., description="URL direta do PDF")):
    """Baixa um PDF remoto e processa no pipeline."""
    if not url.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A URL deve apontar para um .pdf")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name

    try:
        r = requests.get(url, headers=pipeline.HEADERS, stream=True, timeout=30)
        if r.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Falha ao baixar: HTTP {r.status_code}",
            )

        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        ok, msg = pipeline.processar_relatorio(tmp_path, url_origem=url)
        status = "processado" if ok else "ignorado"
        return {"status": status, "detail": msg}
    except HTTPException:
        raise
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Falha ao baixar: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no pipeline: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _executar_crawl():
    """Tarefa de background: varre RI, baixa e processa novos PDFs."""
    resultados = pipeline.monitorar_fontes_ri()
    for item in resultados:
        url = item["url"]
        empresa = item["empresa"]

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name

        try:
            r = requests.get(url, headers=pipeline.HEADERS, stream=True, timeout=30)
            if r.status_code == 200:
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                pipeline.processar_relatorio(
                    tmp_path, url_origem=url, empresa_fonte=empresa,
                )
        except Exception as e:
            pipeline.logger.warning("Crawler falhou para %s: %s", url, e)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


@app.post("/api/ingest/crawl", tags=["Ingestão"])
async def disparar_crawler(background_tasks: BackgroundTasks):
    """Dispara o crawler de Centrais de RI em segundo plano."""
    background_tasks.add_task(_executar_crawl)
    return {
        "status": "iniciado",
        "detail": "Crawler de Centrais de Resultados iniciado em background.",
    }
