import os
import shutil
import tempfile
from typing import Optional, List
import requests
from fastapi import FastAPI, UploadFile, File, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

import database
import pipeline

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa as tabelas do SQLite na inicialização do servidor
    database.init_db()
    yield

app = FastAPI(
    title="HabitaData UDA API",
    description="Pipeline de Ingestão e Análise de Dados Não Estruturados (UDA) para Relatórios de Conjuntura Habitacional",
    version="1.0.0",
    lifespan=lifespan
)

# Habilita CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {
        "message": "Bem-vindo à API HabitaData UDA!",
        "status": "online",
        "endpoints": {
            "GET /api/conjuntura": "Consulta de dados de lançamentos e vendas estruturados",
            "GET /api/catalog": "Histórico de linhagem e catálogo de arquivos processados",
            "POST /api/ingest/file": "Envio manual de arquivo PDF para extração",
            "POST /api/ingest/url": "Solicitação de download e processamento de PDF por URL",
            "POST /api/ingest/crawl": "Disparo manual do crawler para monitoramento de sites de RI"
        }
    }

@app.get("/api/conjuntura")
def consultar_conjuntura(
    empresa: Optional[str] = Query(None, description="Filtrar por nome da incorporadora (ex: MRV, Cury)"),
    ano: Optional[int] = Query(None, description="Filtrar por ano (ex: 2025)"),
    trimestre: Optional[int] = Query(None, description="Filtrar por trimestre (1, 2, 3 ou 4)")
):
    try:
        dados = database.buscar_dados_conjuntura(empresa, ano, trimestre)
        return {
            "count": len(dados),
            "filtros": {"empresa": empresa, "ano": ano, "trimestre": trimestre},
            "dados": dados
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar dados: {str(e)}")

@app.get("/api/catalog")
def obter_catalogo_linhagem():
    try:
        docs = database.obter_catalogo()
        return {
            "count": len(docs),
            "documentos": docs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar catálogo: {str(e)}")

@app.post("/api/ingest/file")
async def ingerir_arquivo_upload(file: UploadFile = File(...)):
    """Faz o upload direto de um arquivo PDF e o processa no pipeline."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos PDF são aceitos.")
        
    # Salva temporariamente para processamento
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
        
    try:
        sucesso, msg = pipeline.processar_relatorio(tmp_path, url_origem="Upload Manual")
        if not sucesso:
            # Já existia
            return {"status": "ignorado", "detail": msg}
        return {"status": "processado", "detail": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no pipeline: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/ingest/url")
def ingerir_por_url(url: str = Query(..., description="URL direta do arquivo PDF do relatório")):
    """Baixa um PDF a partir de uma URL e faz a ingestão."""
    if not url.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A URL fornecida deve apontar para um arquivo .pdf")
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }
    
    # Salva temporariamente para processamento
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name
        
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=15)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Erro ao baixar o arquivo: HTTP {r.status_code}")
            
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                
        sucesso, msg = pipeline.processar_relatorio(tmp_path, url_origem=url)
        if not sucesso:
            return {"status": "ignorado", "detail": msg}
        return {"status": "processado", "detail": msg}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no pipeline: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def tarefa_background_crawl():
    """Tarefa executada em segundo plano para raspagem e processamento automático."""
    urls_pdf = pipeline.monitorar_fontes_ri()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }
    
    for url in urls_pdf:
        # Cria caminho temporário
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
            
        try:
            r = requests.get(url, headers=headers, stream=True, timeout=15)
            if r.status_code == 200:
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                # Tenta processar
                pipeline.processar_relatorio(tmp_path, url_origem=url)
        except Exception as e:
            print(f"[Crawler Background] Falha ao processar URL {url}: {e}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

@app.post("/api/ingest/crawl")
def disparar_crawler_ri(background_tasks: BackgroundTasks):
    """
    Dispara o crawler em segundo plano para varrer os portais de RI da MRV, Cury e Direcional.
    Qualquer PDF novo encontrado será baixado, hash gerado e processado pelo pipeline de LLM.
    """
    background_tasks.add_task(tarefa_background_crawl)
    return {
        "status": "iniciado",
        "detail": "Crawler de Centrais de Resultados de RI iniciado em segundo plano."
    }
