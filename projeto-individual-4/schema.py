from pydantic import BaseModel, Field
from typing import Optional, List

class DadosEmpresaTrimestre(BaseModel):
    """
    Representa os dados estruturados de lançamentos e vendas de uma construtora em um trimestre específico.
    Todos os valores financeiros de VGV (Valor Geral de Vendas) devem ser informados em valores brutos absolutos (em milhões de reais).
    """
    empresa: str = Field(
        ..., 
        description="Nome da empresa/incorporadora em caixa alta unificada (ex: MRV, CURY, TENDA, DIRECIONAL, PLANO & PLANO, PACAEMBU)"
    )
    ano: int = Field(
        ..., 
        description="Ano do relatório (ex: 2025, 2026)"
    )
    trimestre: int = Field(
        ..., 
        description="Trimestre do relatório comercial (1, 2, 3 ou 4)"
    )
    lancamentos_vgv: Optional[float] = Field(
        None, 
        description="Valor absoluto de Lançamentos em VGV (Valor Geral de Vendas) em milhões de reais (ex: 450.5). Tratar como null se não informado."
    )
    lancamentos_unidades: Optional[int] = Field(
        None, 
        description="Quantidade absoluta de unidades de imóveis lançadas. Tratar como null se não informado."
    )
    vendas_liquidas_vgv: Optional[float] = Field(
        None, 
        description="Valor absoluto de Vendas Líquidas (ou vendas brutas se a líquida não estiver disponível) em VGV em milhões de reais. Tratar como null se não informado."
    )
    vendas_unidades: Optional[int] = Field(
        None, 
        description="Quantidade absoluta de unidades vendidas. Tratar como null se não informado."
    )
    unidade_medida: str = Field(
        "R$ Milhões", 
        description="Unidade de medida padrão dos valores financeiros (geralmente 'R$ Milhões')"
    )
    linhagem_trecho: str = Field(
        ..., 
        description="Trecho exato do texto do PDF do qual estes números foram extraídos, comprovando a linhagem do dado e evitando alucinações."
    )

class ExtrairRelatorioPDF(BaseModel):
    """
    Contrato semântico para a resposta do LLM ao ler o relatório completo.
    Pode conter dados de uma ou mais empresas/incorporadoras mencionadas no relatório.
    """
    registros: List[DadosEmpresaTrimestre] = Field(
        ..., 
        description="Lista de registros trimestrais de incorporadoras identificados no documento."
    )
