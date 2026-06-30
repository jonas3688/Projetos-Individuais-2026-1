"""
Contrato Semântico de Dados — Pipeline UDA Habitacional.

Define os modelos Pydantic que blindam a saída do LLM, forçando
tipos corretos e tratando ausências como NULL.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from enum import Enum


class EmpresaEnum(str, Enum):
    """Empresas-alvo do pipeline, padronizadas."""
    MRV = "MRV"
    CURY = "CURY"
    TENDA = "TENDA"
    DIRECIONAL = "DIRECIONAL"
    PLANO_E_PLANO = "PLANO & PLANO"
    PACAEMBU = "PACAEMBU"


EMPRESAS_VALIDAS = {e.value for e in EmpresaEnum}


class DadosEmpresaTrimestre(BaseModel):
    """
    Dados operacionais de uma construtora em um trimestre.

    Todos os valores financeiros de VGV (Valor Geral de Vendas)
    devem ser informados em valores brutos absolutos (R$ Milhões).
    Porcentagens de variação devem ser IGNORADAS.
    """
    empresa: str = Field(
        ...,
        description="Nome padronizado da incorporadora (MRV, CURY, TENDA, DIRECIONAL, PLANO & PLANO, PACAEMBU)"
    )
    ano: int = Field(
        ...,
        ge=2000,
        le=2100,
        description="Ano do relatório (ex: 2025)"
    )
    trimestre: int = Field(
        ...,
        ge=1,
        le=4,
        description="Trimestre (1, 2, 3 ou 4)"
    )
    lancamentos_vgv: Optional[float] = Field(
        None,
        ge=0,
        description="Lançamentos em VGV (R$ Milhões). NULL se ausente no documento."
    )
    lancamentos_unidades: Optional[int] = Field(
        None,
        ge=0,
        description="Unidades lançadas. NULL se ausente no documento."
    )
    vendas_liquidas_vgv: Optional[float] = Field(
        None,
        ge=0,
        description="Vendas Líquidas em VGV (R$ Milhões). NULL se ausente no documento."
    )
    vendas_unidades: Optional[int] = Field(
        None,
        ge=0,
        description="Unidades vendidas. NULL se ausente no documento."
    )
    unidade_medida: str = Field(
        "R$ Milhões",
        description="Unidade padrão dos valores financeiros."
    )
    linhagem_trecho: str = Field(
        ...,
        min_length=5,
        description="Trecho exato do PDF que justifica a extração (data lineage)."
    )

    @field_validator("empresa")
    @classmethod
    def normalizar_empresa(cls, v: str) -> str:
        """Normaliza e valida o nome da empresa contra o catálogo padronizado."""
        v_upper = v.strip().upper()
        # Normalizações comuns
        if v_upper in ("PLANO E PLANO", "PLANO & PLANO", "PLANO&PLANO", "PLANO"):
            return "PLANO & PLANO"
        if "MRV" in v_upper:
            return "MRV"
        if "CURY" in v_upper:
            return "CURY"
        if "TENDA" in v_upper:
            return "TENDA"
        if "DIRECIONAL" in v_upper:
            return "DIRECIONAL"
        if "PACAEMBU" in v_upper:
            return "PACAEMBU"
        
        # Se não bater com nenhuma das conhecidas, levanta ValueError
        if v_upper not in EMPRESAS_VALIDAS:
            raise ValueError(f"Empresa '{v}' não é uma incorporadora habitacional válida no escopo do pipeline.")
        return v_upper


class ExtrairRelatorioPDF(BaseModel):
    """
    Contrato semântico para a resposta completa do LLM.
    Pode conter registros de múltiplas empresas.
    """
    registros: List[DadosEmpresaTrimestre] = Field(
        ...,
        min_length=1,
        description="Lista de registros trimestrais extraídos do documento."
    )
