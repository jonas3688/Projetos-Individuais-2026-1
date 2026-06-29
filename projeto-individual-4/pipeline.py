"""
Pipeline de UDA (Unstructured Data Analysis).

Fluxo completo:
  1. Calcular hash SHA-256 do PDF
  2. Verificar idempotência no catálogo
  3. Extrair texto via PyMuPDF (Full-Scan)
  4. Enviar ao LLM para extração semântica (Gemini > OpenAI > Fallback)
  5. Validar saída com Contrato Semântico (Pydantic)
  6. Persistir dados + linhagem no banco
"""

import os
import hashlib
import json
import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urljoin

import fitz  # PyMuPDF
import requests
from bs4 import BeautifulSoup
from pydantic import ValidationError

import schema
import database

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


# ---- Configuração de LLMs ------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# ---- Fontes de RI para monitoramento contínuo -----------------------------

FONTES_RI = [
    {
        "empresa": "MRV",
        "url": "https://ri.mrv.com.br/divulgacao-de-resultados/",
        "termos": ["Prévia Operacional", "Release", "Operacional"],
    },
    {
        "empresa": "CURY",
        "url": "https://ri.cury.net/central-de-resultados/",
        "termos": ["Prévia", "Resultados", "Release"],
    },
    {
        "empresa": "DIRECIONAL",
        "url": "https://ri.direcional.com.br/central-de-resultados/",
        "termos": ["Prévia", "Operacional", "Release"],
    },
    {
        "empresa": "TENDA",
        "url": "https://ri.tenda.com/central-de-resultados",
        "termos": ["Prévia", "Operacional", "Release"],
    },
    {
        "empresa": "PLANO & PLANO",
        "url": "https://ri.planoplano.com.br/central-de-resultados/",
        "termos": ["Prévia", "Operacional", "Release"],
    },
    {
        "empresa": "PACAEMBU",
        "url": "https://ri.pacaembu.com/central-de-resultados",
        "termos": ["Prévia", "Operacional", "Release"],
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}

# ---- Funções puras (testáveis isoladamente) --------------------------------


def calcular_hash_arquivo(caminho: str) -> str:
    """Retorna o SHA-256 hex de um arquivo."""
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(8192), b""):
            h.update(bloco)
    return h.hexdigest()


def calcular_hash_bytes(conteudo: bytes) -> str:
    """Retorna o SHA-256 hex de bytes em memória."""
    return hashlib.sha256(conteudo).hexdigest()


def extrair_texto_pdf(caminho: str) -> str:
    """
    Full-Scan: extrai todo o texto de um PDF usando PyMuPDF.

    Justificativa da escolha de Full-Scan:
    As prévias operacionais têm em média 1-5 páginas. O custo de tokens
    é baixo, e o Full-Scan garante que nenhuma tabela operacional seja
    perdida por um chunking inadequado. Para documentos maiores (>20 págs),
    um Chunking Semântico baseado em títulos seria mais indicado.
    """
    paginas = []
    with fitz.open(caminho) as doc:
        for i, page in enumerate(doc):
            paginas.append(f"=== PÁGINA {i + 1} ===\n{page.get_text()}")
    return "\n\n".join(paginas)


def construir_prompt(texto_pdf: str) -> str:
    """
    Monta o prompt do sistema + usuário para extração estruturada.

    O prompt contém as Regras de Negócio que blindam a saída do LLM:
    - Ignorar porcentagens
    - Extrair apenas valores absolutos
    - Tratar ausências como null
    - Fornecer trecho de linhagem
    """
    return f"""Você é um analista de dados do Ministério das Cidades.
Analise o texto de um relatório/prévia operacional de incorporadoras habitacionais.

REGRAS OBRIGATÓRIAS:
1. Extraia APENAS valores absolutos (VGV em R$ milhões e número de unidades).
2. IGNORE completamente porcentagens de variação (ex: +14%, -32%).
3. Se um valor absoluto NÃO estiver presente no texto, retorne null. NÃO invente.
4. Padronize nomes: MRV, CURY, TENDA, DIRECIONAL, PLANO & PLANO, PACAEMBU.
5. Inclua o trecho exato do texto que justifica cada extração em "linhagem_trecho".
6. Retorne JSON conforme o schema abaixo.

Schema de resposta (JSON):
{{
  "registros": [
    {{
      "empresa": "NOME",
      "ano": 2025,
      "trimestre": 3,
      "lancamentos_vgv": 450.5,
      "lancamentos_unidades": 1200,
      "vendas_liquidas_vgv": 380.2,
      "vendas_unidades": 1050,
      "unidade_medida": "R$ Milhões",
      "linhagem_trecho": "Trecho exato do PDF..."
    }}
  ]
}}

Se o documento contém APENAS porcentagens sem valores absolutos, liste cada empresa
mencionada com null nos campos de valor e explique na linhagem_trecho que o documento
contém apenas variações percentuais.

TEXTO DO RELATÓRIO:
{texto_pdf}"""


# ---- Motor de Extração via LLM -------------------------------------------


def extrair_com_gemini(prompt: str) -> Dict[str, Any]:
    """Extração via Google Gemini com saída estruturada."""
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema.ExtrairRelatorioPDF,
        ),
    )
    return json.loads(response.text)


def extrair_com_openai(prompt: str) -> Dict[str, Any]:
    """Extração via OpenAI com Structured Outputs."""
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Extraia dados habitacionais estruturados."},
            {"role": "user", "content": prompt},
        ],
        response_format=schema.ExtrairRelatorioPDF,
    )
    return completion.choices[0].message.parsed.model_dump()


def extrair_fallback(texto_pdf: str) -> Dict[str, Any]:
    """
    Extração heurística quando nenhum LLM está disponível.

    Analisa o texto real do PDF para identificar empresas, trimestre/ano
    e quaisquer valores absolutos mencionados. Quando o PDF contém apenas
    porcentagens (como o Boletim de Conjuntura), registra todas as empresas
    citadas com valores nulos e linhagem explicativa.
    """
    empresas_encontradas = []
    empresas_padrao = {
        "MRV": "MRV",
        "CURY": "CURY",
        "TENDA": "TENDA",
        "PLANO & PLANO": "PLANO & PLANO",
        "PLANO E PLANO": "PLANO & PLANO",
        "DIRECIONAL": "DIRECIONAL",
        "PACAEMBU": "PACAEMBU",
    }

    texto_upper = texto_pdf.upper()
    for chave, nome in empresas_padrao.items():
        if chave in texto_upper and nome not in empresas_encontradas:
            empresas_encontradas.append(nome)

    # Detectar ano e trimestre
    ano = 2025
    trimestre = 3
    m_ano = re.search(r"(20[2-3]\d)", texto_pdf)
    if m_ano:
        ano = int(m_ano.group(1))
    m_tri = re.search(r"([1-4])\s*[ºo°]?\s*[Tt][Rr][Ii]", texto_pdf)
    if m_tri:
        trimestre = int(m_tri.group(1))
    # Formato alternativo "3T25"
    m_alt = re.search(r"([1-4])T\d{2}", texto_pdf)
    if m_alt:
        trimestre = int(m_alt.group(1))

    # Tentar encontrar valores absolutos de VGV (ex: "R$ 1.234,5 milhões")
    padrao_vgv = re.compile(
        r"R\$\s*([\d\.]+[,\.]\d+)\s*(?:milhões|mi\b|MM|bi)",
        re.IGNORECASE,
    )
    valores_absolutos = padrao_vgv.findall(texto_pdf)

    # Se encontrou valores absolutos e empresas, tenta casar
    registros = []
    if valores_absolutos and len(empresas_encontradas) == 1:
        emp = empresas_encontradas[0]
        vgv_lanc = float(valores_absolutos[0].replace(".", "").replace(",", ".")) if len(valores_absolutos) > 0 else None
        vgv_vend = float(valores_absolutos[1].replace(".", "").replace(",", ".")) if len(valores_absolutos) > 1 else None
        registros.append({
            "empresa": emp,
            "ano": ano,
            "trimestre": trimestre,
            "lancamentos_vgv": vgv_lanc,
            "lancamentos_unidades": None,
            "vendas_liquidas_vgv": vgv_vend,
            "vendas_unidades": None,
            "unidade_medida": "R$ Milhões",
            "linhagem_trecho": f"Valores extraídos heuristicamente do texto: {texto_pdf[:300].strip()}",
        })
    else:
        # Documento com apenas porcentagens ou multi-empresa sem VGV
        if not empresas_encontradas:
            empresas_encontradas = ["DESCONHECIDA"]

        for emp in empresas_encontradas:
            registros.append({
                "empresa": emp,
                "ano": ano,
                "trimestre": trimestre,
                "lancamentos_vgv": None,
                "lancamentos_unidades": None,
                "vendas_liquidas_vgv": None,
                "vendas_unidades": None,
                "unidade_medida": "R$ Milhões",
                "linhagem_trecho": (
                    f"O documento contém apenas variações percentuais, sem valores absolutos de VGV. "
                    f"Texto inicial: {texto_pdf[:200].strip()}"
                ),
            })

    return {"registros": registros}


def extrair_dados_com_llm(texto_pdf: str) -> schema.ExtrairRelatorioPDF:
    """
    Orquestra a extração semântica:
      1. Gemini (se GEMINI_API_KEY)
      2. OpenAI (se OPENAI_API_KEY)
      3. Fallback heurístico

    Valida a saída com o Contrato Semântico (Pydantic) antes de retornar.
    """
    prompt = construir_prompt(texto_pdf)
    resultado_raw: Optional[Dict] = None

    # 1. Gemini
    if GEMINI_API_KEY:
        try:
            resultado_raw = extrair_com_gemini(prompt)
            logger.info("Extração via Gemini concluída com sucesso.")
        except Exception as e:
            logger.warning("Gemini falhou: %s. Tentando próximo provider...", e)

    # 2. OpenAI
    if resultado_raw is None and OPENAI_API_KEY:
        try:
            resultado_raw = extrair_com_openai(prompt)
            logger.info("Extração via OpenAI concluída com sucesso.")
        except Exception as e:
            logger.warning("OpenAI falhou: %s. Usando fallback heurístico...", e)

    # 3. Fallback
    if resultado_raw is None:
        logger.info("Nenhum LLM configurado/disponível. Usando extração heurística.")
        resultado_raw = extrair_fallback(texto_pdf)

    # Validar com Pydantic (Contrato Semântico)
    try:
        resultado = schema.ExtrairRelatorioPDF(**resultado_raw)
    except ValidationError as e:
        logger.error("Saída do LLM violou o Contrato Semântico: %s", e)
        raise ValueError(f"Contrato Semântico violado: {e}")

    return resultado


# ---- Crawler de RI --------------------------------------------------------


def monitorar_fontes_ri() -> List[Dict[str, str]]:
    """
    Varre as Centrais de Resultados de RI em busca de links para PDFs.

    Retorna lista de dicts com 'url' e 'empresa'.
    """
    links: List[Dict[str, str]] = []

    for fonte in FONTES_RI:
        empresa = fonte["empresa"]
        url_base = fonte["url"]
        logger.info("Varrendo RI de %s (%s)...", empresa, url_base)
        try:
            r = requests.get(url_base, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                logger.warning("%s retornou HTTP %d", empresa, r.status_code)
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                texto_link = a.get_text(strip=True).lower()
                if not href.lower().endswith(".pdf"):
                    continue
                if any(
                    t.lower() in href.lower() or t.lower() in texto_link
                    for t in fonte["termos"]
                ):
                    url_abs = href if href.startswith("http") else urljoin(url_base, href)
                    links.append({"url": url_abs, "empresa": empresa})

        except Exception as e:
            logger.warning("Falha ao raspar RI de %s: %s", empresa, e)

    # Deduplica por URL
    seen = set()
    deduplicado = []
    for item in links:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduplicado.append(item)

    logger.info("Total de PDFs detectados: %d", len(deduplicado))
    return deduplicado


# ---- Orquestrador do Pipeline ---------------------------------------------


def processar_relatorio(
    caminho_arquivo: str,
    url_origem: Optional[str] = None,
    empresa_fonte: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Pipeline completo de ingestão de um PDF:
      1. Hash SHA-256
      2. Idempotência (skip se já processado)
      3. Full-Scan do texto
      4. Extração semântica via LLM
      5. Persistência + Linhagem

    Retorna (sucesso: bool, mensagem: str).
    """
    nome = os.path.basename(caminho_arquivo)
    file_hash = calcular_hash_arquivo(caminho_arquivo)

    # Idempotência
    if database.documento_existe(file_hash):
        msg = f"Documento ignorado (já processado). Hash: {file_hash[:16]}..."
        logger.info(msg)
        return False, msg

    try:
        # Extrair texto
        texto = extrair_texto_pdf(caminho_arquivo)
        if not texto.strip():
            raise ValueError("PDF vazio ou não legível textualmente.")

        # Extração semântica (retorna modelo Pydantic validado)
        resultado = extrair_dados_com_llm(texto)

        # Persistir documento no catálogo
        doc_id = database.registrar_documento(
            nome_arquivo=nome,
            hash_sha256=file_hash,
            url_origem=url_origem,
            empresa_fonte=empresa_fonte,
            status="processado",
        )

        # Persistir cada registro com linhagem
        for reg in resultado.registros:
            database.salvar_dados_conjuntura(doc_id, reg.model_dump())

        n = len(resultado.registros)
        msg = f"Sucesso! {n} registro(s) extraído(s) de '{nome}'."
        logger.info(msg)
        return True, msg

    except Exception as e:
        # Registrar falha no catálogo
        database.registrar_documento(
            nome_arquivo=nome,
            hash_sha256=file_hash,
            url_origem=url_origem,
            empresa_fonte=empresa_fonte,
            status=f"erro: {e}",
        )
        logger.error("Pipeline falhou para '%s': %s", nome, e)
        raise
