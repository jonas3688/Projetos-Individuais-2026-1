"""
Suite de Testes — Pipeline UDA Habitacional.

Abordagem TDD: cada camada é testada isoladamente.
  - test_schema:   Contrato Semântico (Pydantic)
  - test_database: Persistência e Idempotência (SQLite)
  - test_pipeline: Hash, Extração, Fallback
  - test_api:      Endpoints REST (FastAPI + httpx)
"""

import os
import sys
import tempfile
import hashlib

import pytest

# Garante que o diretório do projeto está no sys.path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import schema
import database
import pipeline


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def db_temporario(tmp_path):
    """
    Cria um banco SQLite temporário para cada teste.
    Garante isolamento total entre testes.
    """
    db_file = str(tmp_path / "test.db")
    database.set_db_path(db_file)
    database.init_db()
    yield db_file
    # Cleanup automático pelo tmp_path


@pytest.fixture
def pdf_exemplo():
    """Caminho para o PDF de exemplo do Boletim de Conjuntura."""
    path = os.path.join(PROJECT_DIR, "exemplo_Boletim_Conjuntura_2025_3T.pdf")
    if not os.path.exists(path):
        pytest.skip("PDF de exemplo não encontrado")
    return path


@pytest.fixture
def pdf_fake(tmp_path):
    """Cria um PDF fake simples para testes de hash e parsing."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Prévia Operacional - 3º Trimestre 2025\n"
        "Empresa: MRV Engenharia\n"
        "Lançamentos: R$ 2.341,2 milhões (8.740 unidades)\n"
        "Vendas Líquidas: R$ 2.105,5 milhões (7.920 unidades)\n"
    )
    path = str(tmp_path / "previa_mrv_3t25.pdf")
    doc.save(path)
    doc.close()
    return path


# ============================================================================
# 1. TESTES DO CONTRATO SEMÂNTICO (schema.py)
# ============================================================================

class TestContratoSemantico:
    """Verifica que o Pydantic rejeita dados inválidos e aceita dados corretos."""

    def test_registro_valido_completo(self):
        """Dados completos devem ser aceitos sem erros."""
        reg = schema.DadosEmpresaTrimestre(
            empresa="MRV",
            ano=2025,
            trimestre=3,
            lancamentos_vgv=2341.2,
            lancamentos_unidades=8740,
            vendas_liquidas_vgv=2105.5,
            vendas_unidades=7920,
            linhagem_trecho="Trecho extraído da Prévia Operacional MRV 3T25.",
        )
        assert reg.empresa == "MRV"
        assert reg.ano == 2025
        assert reg.trimestre == 3
        assert reg.lancamentos_vgv == 2341.2

    def test_registro_valido_com_nulls(self):
        """Campos financeiros opcionais podem ser None (null)."""
        reg = schema.DadosEmpresaTrimestre(
            empresa="CURY",
            ano=2025,
            trimestre=3,
            lancamentos_vgv=None,
            lancamentos_unidades=None,
            vendas_liquidas_vgv=None,
            vendas_unidades=None,
            linhagem_trecho="Documento contém apenas porcentagens.",
        )
        assert reg.lancamentos_vgv is None
        assert reg.vendas_liquidas_vgv is None

    def test_normalizacao_empresa_caixa_alta(self):
        """O validador deve normalizar 'mrv' para 'MRV'."""
        reg = schema.DadosEmpresaTrimestre(
            empresa="mrv",
            ano=2025,
            trimestre=3,
            linhagem_trecho="Texto de teste com mais de cinco chars.",
        )
        assert reg.empresa == "MRV"

    def test_trimestre_invalido_rejeitado(self):
        """Trimestre fora de 1-4 deve ser rejeitado."""
        with pytest.raises(Exception):
            schema.DadosEmpresaTrimestre(
                empresa="MRV",
                ano=2025,
                trimestre=5,
                linhagem_trecho="Trimestre inválido deve falhar.",
            )

    def test_ano_invalido_rejeitado(self):
        """Ano fora do range 2000-2100 deve ser rejeitado."""
        with pytest.raises(Exception):
            schema.DadosEmpresaTrimestre(
                empresa="MRV",
                ano=1999,
                trimestre=3,
                linhagem_trecho="Ano inválido deve falhar.",
            )

    def test_vgv_negativo_rejeitado(self):
        """VGV negativo deve ser rejeitado (ge=0)."""
        with pytest.raises(Exception):
            schema.DadosEmpresaTrimestre(
                empresa="MRV",
                ano=2025,
                trimestre=3,
                lancamentos_vgv=-100.0,
                linhagem_trecho="VGV negativo deve falhar na validação.",
            )

    def test_linhagem_curta_rejeitada(self):
        """Linhagem com menos de 5 chars deve ser rejeitada."""
        with pytest.raises(Exception):
            schema.DadosEmpresaTrimestre(
                empresa="MRV",
                ano=2025,
                trimestre=3,
                linhagem_trecho="abc",
            )

    def test_extracao_relatorio_lista_vazia_rejeitada(self):
        """ExtrairRelatorioPDF exige ao menos 1 registro."""
        with pytest.raises(Exception):
            schema.ExtrairRelatorioPDF(registros=[])

    def test_extracao_relatorio_valida(self):
        """ExtrairRelatorioPDF com 1 registro válido deve ser aceito."""
        reg = schema.DadosEmpresaTrimestre(
            empresa="TENDA",
            ano=2025,
            trimestre=3,
            linhagem_trecho="Trecho do relatório da Tenda 3T25.",
        )
        relatorio = schema.ExtrairRelatorioPDF(registros=[reg])
        assert len(relatorio.registros) == 1


# ============================================================================
# 2. TESTES DO BANCO DE DADOS (database.py)
# ============================================================================

class TestDatabase:
    """Testa operações CRUD e controle de idempotência."""

    def test_init_db_cria_tabelas(self, db_temporario):
        """init_db deve criar as tabelas documentos e dados_conjuntura."""
        conn = database.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tabelas = {row["name"] for row in cur.fetchall()}
        conn.close()
        assert "documentos" in tabelas
        assert "dados_conjuntura" in tabelas

    def test_registrar_e_buscar_documento(self, db_temporario):
        """Deve registrar e depois encontrar o documento pelo hash."""
        doc_id = database.registrar_documento(
            nome_arquivo="teste.pdf",
            hash_sha256="abc123",
            url_origem="https://example.com/teste.pdf",
        )
        assert doc_id > 0
        assert database.documento_existe("abc123")
        assert not database.documento_existe("xyz999")

    def test_idempotencia_hash_duplicado(self, db_temporario):
        """Registrar o mesmo hash duas vezes não deve gerar erro."""
        id1 = database.registrar_documento("a.pdf", "hash1")
        id2 = database.registrar_documento("b.pdf", "hash1")
        assert id1 == id2  # retorna o ID existente

    def test_salvar_e_buscar_conjuntura(self, db_temporario):
        """Deve salvar dados e recuperá-los com filtros."""
        doc_id = database.registrar_documento("rel.pdf", "h1")
        database.salvar_dados_conjuntura(doc_id, {
            "empresa": "MRV",
            "ano": 2025,
            "trimestre": 3,
            "lancamentos_vgv": 2341.2,
            "lancamentos_unidades": 8740,
            "vendas_liquidas_vgv": 2105.5,
            "vendas_unidades": 7920,
            "linhagem_trecho": "Trecho de teste da MRV.",
        })

        # Sem filtro
        todos = database.buscar_dados_conjuntura()
        assert len(todos) == 1
        assert todos[0]["empresa"] == "MRV"

        # Filtro por empresa
        mrv = database.buscar_dados_conjuntura(empresa="MRV")
        assert len(mrv) == 1

        # Filtro que não casa
        cury = database.buscar_dados_conjuntura(empresa="CURY")
        assert len(cury) == 0

        # Filtro por ano e trimestre
        filtrado = database.buscar_dados_conjuntura(ano=2025, trimestre=3)
        assert len(filtrado) == 1

    def test_buscar_conjuntura_com_nulls(self, db_temporario):
        """Dados com campos financeiros NULL devem ser salvos e recuperados."""
        doc_id = database.registrar_documento("bol.pdf", "h2")
        database.salvar_dados_conjuntura(doc_id, {
            "empresa": "CURY",
            "ano": 2025,
            "trimestre": 3,
            "lancamentos_vgv": None,
            "lancamentos_unidades": None,
            "vendas_liquidas_vgv": None,
            "vendas_unidades": None,
            "linhagem_trecho": "Apenas porcentagens no documento.",
        })
        dados = database.buscar_dados_conjuntura(empresa="CURY")
        assert len(dados) == 1
        assert dados[0]["lancamentos_vgv"] is None

    def test_catalogo_retorna_documentos(self, db_temporario):
        """obter_catalogo deve listar todos os documentos registrados."""
        database.registrar_documento("a.pdf", "h_a")
        database.registrar_documento("b.pdf", "h_b")
        catalogo = database.obter_catalogo()
        assert len(catalogo) == 2

    def test_trimestre_invalido_no_banco(self, db_temporario):
        """CHECK constraint deve impedir trimestre=5."""
        doc_id = database.registrar_documento("x.pdf", "hx")
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            database.salvar_dados_conjuntura(doc_id, {
                "empresa": "MRV",
                "ano": 2025,
                "trimestre": 5,
                "linhagem_trecho": "Deveria falhar.",
            })


# ============================================================================
# 3. TESTES DO PIPELINE (pipeline.py)
# ============================================================================

class TestPipeline:
    """Testa hash, extração de texto, fallback heurístico e pipeline completo."""

    def test_hash_determinístico(self, tmp_path):
        """O mesmo conteúdo deve gerar sempre o mesmo hash."""
        f = tmp_path / "test.bin"
        f.write_bytes(b"conteudo fixo para hash")
        h1 = pipeline.calcular_hash_arquivo(str(f))
        h2 = pipeline.calcular_hash_arquivo(str(f))
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_bytes(self):
        """calcular_hash_bytes deve retornar o mesmo hash para os mesmos bytes."""
        data = b"teste de hash em memoria"
        h = pipeline.calcular_hash_bytes(data)
        assert h == hashlib.sha256(data).hexdigest()

    def test_extrair_texto_pdf_exemplo(self, pdf_exemplo):
        """Deve extrair texto do PDF de exemplo contendo 'Conjuntura'."""
        texto = pipeline.extrair_texto_pdf(pdf_exemplo)
        assert "Conjuntura" in texto or "CONJUNTURA" in texto.upper()
        assert "MRV" in texto.upper()

    def test_extrair_texto_pdf_fake(self, pdf_fake):
        """Deve extrair o texto inserido no PDF fake."""
        texto = pipeline.extrair_texto_pdf(pdf_fake)
        assert "MRV" in texto
        assert "2.341,2" in texto

    def test_fallback_boletim_retorna_empresas_com_nulls(self, pdf_exemplo):
        """
        O Boletim de Conjuntura 3T25 contém APENAS porcentagens.
        O fallback deve identificar as 6 empresas e retornar null nos VGVs.
        """
        texto = pipeline.extrair_texto_pdf(pdf_exemplo)
        resultado = pipeline.extrair_fallback(texto)

        assert "registros" in resultado
        registros = resultado["registros"]
        assert len(registros) >= 4  # Pelo menos MRV, Cury, Tenda, Direcional

        # Todos os VGV devem ser None (só tem porcentagens no PDF)
        for reg in registros:
            assert reg["lancamentos_vgv"] is None, f"{reg['empresa']} deveria ter VGV null"
            assert reg["vendas_liquidas_vgv"] is None
            assert "porcentag" in reg["linhagem_trecho"].lower() or "variação" in reg["linhagem_trecho"].lower() or "variações" in reg["linhagem_trecho"].lower() or "percentuais" in reg["linhagem_trecho"].lower()

    def test_fallback_previa_com_valores_absolutos(self, pdf_fake):
        """
        Um PDF com valores absolutos de VGV deve extraí-los via regex.
        """
        texto = pipeline.extrair_texto_pdf(pdf_fake)
        resultado = pipeline.extrair_fallback(texto)

        registros = resultado["registros"]
        assert len(registros) >= 1
        # Deve ter encontrado a MRV
        mrv = [r for r in registros if r["empresa"] == "MRV"]
        assert len(mrv) >= 1

    def test_construir_prompt_contem_regras(self):
        """O prompt deve conter as regras de blindagem."""
        prompt = pipeline.construir_prompt("Texto qualquer")
        assert "IGNORE" in prompt.upper()
        assert "porcentag" in prompt.lower()
        assert "null" in prompt.lower()
        assert "linhagem" in prompt.lower()

    def test_pipeline_completo_com_pdf_exemplo(self, pdf_exemplo, db_temporario):
        """Pipeline end-to-end com o PDF de exemplo."""
        ok, msg = pipeline.processar_relatorio(pdf_exemplo)
        assert ok is True
        assert "Sucesso" in msg

        # Verificar no banco
        dados = database.buscar_dados_conjuntura()
        assert len(dados) >= 1

        # Verificar catálogo
        catalogo = database.obter_catalogo()
        assert len(catalogo) == 1
        assert catalogo[0]["status"] == "processado"

    def test_pipeline_idempotencia(self, pdf_exemplo, db_temporario):
        """Processar o mesmo PDF duas vezes deve ignorar na segunda."""
        ok1, _ = pipeline.processar_relatorio(pdf_exemplo)
        assert ok1 is True

        ok2, msg2 = pipeline.processar_relatorio(pdf_exemplo)
        assert ok2 is False
        assert "ignorado" in msg2.lower() or "já processado" in msg2.lower()

        # Apenas 1 documento no catálogo
        assert len(database.obter_catalogo()) == 1

    def test_pipeline_pdf_fake(self, pdf_fake, db_temporario):
        """Pipeline deve funcionar com o PDF sintético."""
        ok, msg = pipeline.processar_relatorio(pdf_fake, url_origem="test://fake.pdf")
        assert ok is True

        dados = database.buscar_dados_conjuntura()
        assert len(dados) >= 1


# ============================================================================
# 4. TESTES DA API (main.py)
# ============================================================================

class TestAPI:
    """Testa os endpoints da API FastAPI usando httpx."""

    @pytest.fixture
    def client(self, db_temporario):
        """Cria um TestClient da FastAPI com banco temporário."""
        from httpx import AsyncClient, ASGITransport
        from main import app
        import asyncio

        # Re-inicializa o banco para garantir limpeza
        database.init_db()

        # Cria client síncrono wrapper
        transport = ASGITransport(app=app)

        class SyncClient:
            def __init__(self):
                self.base_url = "http://test"
                self.transport = transport

            def _run(self, coro):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            def get(self, url, **kwargs):
                async def _get():
                    async with AsyncClient(transport=self.transport, base_url=self.base_url) as ac:
                        return await ac.get(url, **kwargs)
                return self._run(_get())

            def post(self, url, **kwargs):
                async def _post():
                    async with AsyncClient(transport=self.transport, base_url=self.base_url) as ac:
                        return await ac.post(url, **kwargs)
                return self._run(_post())

        return SyncClient()

    def test_health_check(self, client):
        """GET / deve retornar status online."""
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "online"

    def test_conjuntura_vazia(self, client):
        """GET /api/conjuntura sem dados deve retornar lista vazia."""
        r = client.get("/api/conjuntura")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert data["dados"] == []

    def test_catalogo_vazio(self, client):
        """GET /api/catalog sem documentos deve retornar lista vazia."""
        r = client.get("/api/catalog")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_ingest_file_pdf(self, client, pdf_exemplo):
        """POST /api/ingest/file com PDF válido deve processar."""
        with open(pdf_exemplo, "rb") as f:
            r = client.post(
                "/api/ingest/file",
                files={"file": ("boletim.pdf", f, "application/pdf")},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "processado"

        # Verificar que os dados apareceram
        r2 = client.get("/api/conjuntura")
        assert r2.json()["count"] >= 1

    def test_ingest_file_duplicado(self, client, pdf_exemplo):
        """Upload do mesmo PDF 2x: segundo deve retornar 'ignorado'."""
        with open(pdf_exemplo, "rb") as f:
            client.post("/api/ingest/file", files={"file": ("a.pdf", f, "application/pdf")})
        with open(pdf_exemplo, "rb") as f:
            r = client.post("/api/ingest/file", files={"file": ("a.pdf", f, "application/pdf")})
        assert r.json()["status"] == "ignorado"

    def test_ingest_file_nao_pdf(self, client):
        """Upload de arquivo não-PDF deve retornar 400."""
        r = client.post(
            "/api/ingest/file",
            files={"file": ("texto.txt", b"conteudo", "text/plain")},
        )
        assert r.status_code == 400

    def test_conjuntura_filtro_empresa(self, client, pdf_exemplo):
        """Filtro por empresa deve funcionar após ingestão."""
        with open(pdf_exemplo, "rb") as f:
            client.post("/api/ingest/file", files={"file": ("b.pdf", f, "application/pdf")})

        r = client.get("/api/conjuntura", params={"empresa": "MRV"})
        data = r.json()
        assert data["count"] >= 1
        for item in data["dados"]:
            assert "MRV" in item["empresa"].upper()

    def test_conjuntura_filtro_sem_resultado(self, client, pdf_exemplo):
        """Filtro por empresa inexistente deve retornar vazio."""
        with open(pdf_exemplo, "rb") as f:
            client.post("/api/ingest/file", files={"file": ("c.pdf", f, "application/pdf")})

        r = client.get("/api/conjuntura", params={"empresa": "INEXISTENTE"})
        assert r.json()["count"] == 0
