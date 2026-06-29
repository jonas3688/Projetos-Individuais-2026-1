"""
Camada de persistência — Catálogo de Dados + Linhagem.

Utiliza SQLite para armazenar:
  - documentos: metadados e hash SHA-256 para idempotência
  - dados_conjuntura: métricas estruturadas com FK para o documento de origem
"""

import sqlite3
import os
from typing import Optional, Dict, Any, List

# Caminho padrão do banco; pode ser sobrescrito nos testes
_db_path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados_habitacionais.db")


def set_db_path(path: str) -> None:
    """Permite sobrescrever o caminho do banco (útil para testes)."""
    global _db_path
    _db_path = path


def get_db_path() -> str:
    """Retorna o caminho atual do banco."""
    return _db_path


def get_connection() -> sqlite3.Connection:
    """Cria e retorna uma conexão com o SQLite."""
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Cria as tabelas caso ainda não existam."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS documentos (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_arquivo        TEXT    NOT NULL,
        url_origem          TEXT,
        hash_sha256         TEXT    UNIQUE NOT NULL,
        empresa_fonte       TEXT,
        data_processamento  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status              TEXT    NOT NULL DEFAULT 'pendente'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dados_conjuntura (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id         INTEGER NOT NULL,
        empresa              TEXT    NOT NULL,
        ano                  INTEGER NOT NULL,
        trimestre            INTEGER NOT NULL CHECK(trimestre BETWEEN 1 AND 4),
        lancamentos_vgv      REAL,
        lancamentos_unidades INTEGER,
        vendas_liquidas_vgv  REAL,
        vendas_unidades      INTEGER,
        unidade_medida       TEXT    DEFAULT 'R$ Milhões',
        data_extracao        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        linhagem_trecho      TEXT    NOT NULL,
        FOREIGN KEY (documento_id) REFERENCES documentos(id) ON DELETE CASCADE
    )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# CRUD — Documentos (Catálogo)
# ---------------------------------------------------------------------------

def documento_existe(hash_sha256: str) -> bool:
    """Verifica se um documento com o hash dado já foi processado."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM documentos WHERE hash_sha256 = ?", (hash_sha256,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def registrar_documento(
    nome_arquivo: str,
    hash_sha256: str,
    url_origem: Optional[str] = None,
    empresa_fonte: Optional[str] = None,
    status: str = "processado",
) -> int:
    """Insere um documento no catálogo. Retorna o id gerado."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO documentos
               (nome_arquivo, url_origem, hash_sha256, empresa_fonte, status)
               VALUES (?, ?, ?, ?, ?)""",
            (nome_arquivo, url_origem, hash_sha256, empresa_fonte, status),
        )
        doc_id = cur.lastrowid
        conn.commit()
        return doc_id
    except sqlite3.IntegrityError:
        cur.execute("SELECT id FROM documentos WHERE hash_sha256 = ?", (hash_sha256,))
        row = cur.fetchone()
        return row["id"] if row else -1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CRUD — Dados de Conjuntura
# ---------------------------------------------------------------------------

def salvar_dados_conjuntura(doc_id: int, dados: Dict[str, Any]) -> int:
    """Persiste um registro de conjuntura vinculado a um documento."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO dados_conjuntura (
        documento_id, empresa, ano, trimestre,
        lancamentos_vgv, lancamentos_unidades,
        vendas_liquidas_vgv, vendas_unidades,
        unidade_medida, linhagem_trecho
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        doc_id,
        dados["empresa"],
        dados["ano"],
        dados["trimestre"],
        dados.get("lancamentos_vgv"),
        dados.get("lancamentos_unidades"),
        dados.get("vendas_liquidas_vgv"),
        dados.get("vendas_unidades"),
        dados.get("unidade_medida", "R$ Milhões"),
        dados["linhagem_trecho"],
    ))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def buscar_dados_conjuntura(
    empresa: Optional[str] = None,
    ano: Optional[int] = None,
    trimestre: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Consulta dados com filtros opcionais; retorna lista de dicts."""
    conn = get_connection()
    cur = conn.cursor()

    query = """
    SELECT d.*, doc.nome_arquivo, doc.url_origem, doc.hash_sha256
    FROM dados_conjuntura d
    JOIN documentos doc ON d.documento_id = doc.id
    WHERE 1=1
    """
    params: list = []

    if empresa:
        query += " AND UPPER(d.empresa) LIKE UPPER(?)"
        params.append(f"%{empresa}%")
    if ano is not None:
        query += " AND d.ano = ?"
        params.append(ano)
    if trimestre is not None:
        query += " AND d.trimestre = ?"
        params.append(trimestre)

    query += " ORDER BY d.ano DESC, d.trimestre DESC, d.empresa ASC"
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def obter_catalogo() -> List[Dict[str, Any]]:
    """Retorna todos os documentos do catálogo."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM documentos ORDER BY data_processamento DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]
