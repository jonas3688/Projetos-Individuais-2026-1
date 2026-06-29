import sqlite3
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

DATABASE_PATH = os.path.join(os.path.dirname(__file__), "dados_habitacionais.db")

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inicializa as tabelas do banco de dados se não existirem."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Tabela de Catálogo de Documentos (Idempotência e Linhagem)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_arquivo TEXT NOT NULL,
        url_origem TEXT,
        hash_sha256 TEXT UNIQUE NOT NULL,
        data_processamento TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL
    )
    """)
    
    # Tabela de Dados Estruturados da Conjuntura
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dados_conjuntura (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL,
        empresa TEXT NOT NULL,
        ano INTEGER NOT NULL,
        trimestre INTEGER NOT NULL,
        lancamentos_vgv REAL,
        lancamentos_unidades INTEGER,
        vendas_liquidas_vgv REAL,
        vendas_unidades INTEGER,
        unidade_medida TEXT DEFAULT 'R$ Milhões',
        data_extracao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        linhagem_trecho TEXT,
        FOREIGN KEY (documento_id) REFERENCES documentos (id) ON DELETE CASCADE
    )
    """)
    
    conn.commit()
    conn.close()

def documento_existe(hash_sha256: str) -> bool:
    """Verifica se um documento já foi processado usando seu hash SHA-256."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM documentos WHERE hash_sha256 = ?", (hash_sha256,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def registrar_documento(nome_arquivo: str, url_origem: Optional[str], hash_sha256: str, status: str = "processado") -> int:
    """Registra um documento no catálogo e retorna o ID gerado."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO documentos (nome_arquivo, url_origem, hash_sha256, status) VALUES (?, ?, ?, ?)",
            (nome_arquivo, url_origem, hash_sha256, status)
        )
        doc_id = cursor.lastrowid
        conn.commit()
        return doc_id
    except sqlite3.IntegrityError:
        # Se já existir, pega o ID existente
        cursor.execute("SELECT id FROM documentos WHERE hash_sha256 = ?", (hash_sha256,))
        row = cursor.fetchone()
        return row[0] if row else -1
    finally:
        conn.close()

def salvar_dados_conjuntura(doc_id: int, dados: Dict[str, Any]):
    """Salva os dados estruturados extraídos pelo LLM no banco."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO dados_conjuntura (
        documento_id, empresa, ano, trimestre, 
        lancamentos_vgv, lancamentos_unidades, 
        vendas_liquidas_vgv, vendas_unidades, 
        unidade_medida, linhagem_trecho
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        doc_id,
        dados.get("empresa"),
        dados.get("ano"),
        dados.get("trimestre"),
        dados.get("lancamentos_vgv"),
        dados.get("lancamentos_unidades"),
        dados.get("vendas_liquidas_vgv"),
        dados.get("vendas_unidades"),
        dados.get("unidade_medida", "R$ Milhões"),
        dados.get("linhagem_trecho")
    ))
    conn.commit()
    conn.close()

def buscar_dados_conjuntura(empresa: Optional[str] = None, ano: Optional[int] = None, trimestre: Optional[int] = None) -> List[Dict[str, Any]]:
    """Busca os dados de conjuntura aplicando filtros dinâmicos."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
    SELECT d.*, doc.nome_arquivo, doc.url_origem, doc.hash_sha256 
    FROM dados_conjuntura d
    JOIN documentos doc ON d.documento_id = doc.id
    WHERE 1=1
    """
    params = []
    
    if empresa:
        query += " AND d.empresa LIKE ?"
        params.append(f"%{empresa}%")
    if ano:
        query += " AND d.ano = ?"
        params.append(ano)
    if trimestre:
        query += " AND d.trimestre = ?"
        params.append(trimestre)
        
    query += " ORDER BY d.ano DESC, d.trimestre DESC, d.empresa ASC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def obter_catalogo() -> List[Dict[str, Any]]:
    """Retorna a lista de todos os documentos processados."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM documentos ORDER BY data_processamento DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
