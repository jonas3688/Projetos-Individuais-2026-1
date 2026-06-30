# Plano de Testes, TDD e Validação de Clean Code (UDA Pipeline)

Este plano avalia a implementação atual em relação à especificação do desafio do Ministério das Cidades, apresenta uma validação de Clean Code detalhada e define uma estratégia abrangente de testes unitários (TDD) para garantir robustez máxima.

---

## 1. Avaliação de Aderência aos Requisitos (Comitado vs. Especificação)

Abaixo está o mapeamento dos requisitos obrigatórios da especificação original e o status de sua cobertura no código comitado:

| Requisito do Desafio | Status | Componente | Observações / Melhorias Identificadas |
|---|---|---|---|
| **A.1 Gatilho de Ingestão** | 🟢 Aderente | `main.py` & `pipeline.py` | Crawler periódico implementado em background para monitorar sites de RI (MRV, Cury, Direcional) e endpoints para ingestão manual. |
| **A.2 Idempotência** | 🟢 Aderente | `pipeline.py` & `database.py` | Cálculo de hash SHA-256 antes do LLM. Skip automático com retorno transparente se o hash já existir. |
| **B.1 Chunking & Parsing** | 🟢 Aderente | `pipeline.py` | Justificativa do *Full-Scan* documentada no código (ideal para relatórios curtos de 1-5 páginas). |
| **B.2 Extração Semântica** | 🟢 Aderente | `pipeline.py` | Suporte nativo ao Gemini (com `response_schema`) e OpenAI (com `parse`), além de um motor de fallback com expressões regulares. |
| **B.3 Contrato Semântico** | 🟡 Parcial | `schema.py` | Pydantic valida tipos e ranges, porém a validação e normalização do campo `empresa` contra a lista restrita de incorporadoras válidas (`EMPRESAS_VALIDAS`) não está sendo estritamente imposta. O normalizador aceita qualquer string. |
| **C. Camada de Serviço (API)**| 🟢 Aderente | `main.py` | Endpoints REST bem modelados e documentados (Swagger), com filtros dinâmicos de empresa, ano e trimestre. |
| **Catálogo e Linhagem** | 🟢 Aderente | `database.py` & `pipeline.py` | Tabela de documentos registrando a linhagem do arquivo e campo `linhagem_trecho` associado a cada registro extraído. |

---

## 2. Validação de Clean Code e Oportunidades de Refatoração

Durante a análise estática do código, identificamos as seguintes melhorias para aplicar as melhores práticas de Clean Code:

### 2.1 schema.py (Contrato Semântico)
- **Brecha de Segurança no Contrato**: O campo `empresa` aceita qualquer valor textual que for convertido para maiúsculas (ex: `"GOOGLE"` ou `"FACEBOOK"` passariam no validador). 
- **Melhoria**: O `@field_validator("empresa")` deve validar se o nome normalizado pertence estritamente ao conjunto de `EMPRESAS_VALIDAS` e mapear grafias alternativas (ex: `"PLANO E PLANO"` para `"PLANO & PLANO"`, ou `"MRV ENGENHARIA"` para `"MRV"`).

### 2.2 database.py (Persistência e Recursos)
- **Gerenciamento de Conexão SQLite**: O uso direto de `conn = get_connection(); conn.commit(); conn.close()` é suscetível a vazamentos de conexão ou falhas de commit caso uma exceção ocorra no meio da transação.
- **Melhoria**: Refatorar o uso de conexões para utilizar gerenciadores de contexto (`with get_connection() as conn:`), garantindo commits e rollbacks automáticos e fechamento seguro do cursor.

### 2.3 pipeline.py (Motor de Ingestão e Fallback)
- **Crawler com URLs Estáticas**: Se um site de RI mudar sua estrutura, o crawler pode falhar.
- **Melhoria**: Adicionar timeouts rigorosos em cada requisição de monitoramento, encapsular as requisições de raspagem em blocos `try-except` específicos e melhorar as expressões regulares do parser de fallback para lidar com mais formatos de valores financeiros (ex: tratar pontos e vírgulas de milhares de forma genérica).
- **Tratamento de PDF Escaneado (Sem Camada de Texto)**: Se o PDF for uma imagem pura, a extração de texto retornará uma string vazia.
- **Melhoria**: Lançar uma exceção explícita ou logar um aviso de que o arquivo requer OCR para processamento.

### 2.4 main.py (FastAPI Routes)
- **Erros Genéricos (HTTP 500)**: Muitas rotas capturam `Exception` e retornam a mensagem de erro diretamente para o cliente em texto limpo.
- **Melhoria**: Criar classes de exceções customizadas do pipeline (ex: `SemanticValidationError`, `DuplicateDocumentError`) e utilizar Exception Handlers globais do FastAPI para retornar JSON estruturado com os códigos HTTP corretos (400, 409, 422).

---

## 3. Plano de Testes Unitários e TDD (Casos de Borda e Corner Cases)

Para blindar a robustez do pipeline, propomos expandir a suite de testes adicionando validações para os seguintes cenários de borda:

### 3.1 Testes do Contrato Semântico (`schema.py`)
1. **Normalização de Variações de Nomes**: Garantir que `"MRV Engenharia S.A."`, `"Cury Construtora"` ou `"Plano e Plano"` sejam convertidos para a grafia unificada correspondente.
2. **Rejeição de Empresas Não Habitacionais**: Garantir que uma tentativa de cadastrar `"PETROBRAS"` dispare `ValidationError`.
3. **Métricas Financeiras Extemas**: Testar o comportamento com valores extremamente altos de VGV (ex: `999999.9`) e zero (`0.0`).

### 3.2 Testes da Camada de Banco de Dados (`database.py`)
1. **Exclusão em Cascata (Cascade Delete)**: Garantir que se um documento for removido da tabela `documentos`, todos os seus registros associados na tabela `dados_conjuntura` sejam automaticamente deletados pelo SQLite (`ON DELETE CASCADE`).
2. **Tentativa de Inserir Registro Sem Documento Pai**: Garantir que a integridade referencial impeça a inserção na tabela `dados_conjuntura` se o `documento_id` for inexistente.

### 3.3 Testes do Pipeline (`pipeline.py`)
1. **PDF Sem Camada de Texto (Scanned PDF)**: Criar um PDF puramente com imagem (ou sem caracteres textuais) e garantir que o pipeline aborte a extração de forma elegante, registrando o status de erro correspondente.
2. **Robustez do Regex de Fallback**: Testar o extrator heurístico com textos contendo múltiplos formatos numéricos:
   - `"R$ 1.500,8 milhões"`
   - `"R$ 450 mi"`
   - `"1200 milhões de reais"`
   - `"R$ 3.200.150,00"` (valores inteiros a serem convertidos para milhões)
3. **Simulação de Falha de Rede no Downloader**: Testar o comportamento da ingestão por URL quando o servidor remoto retorna `500 Internal Server Error`, `404 Not Found` ou estoura o timeout de conexão.

### 3.4 Testes da API (`main.py`)
1. **Upload de PDF Corrompido**: Garantir que a API retorne `400 Bad Request` ou `500` apropriado se um PDF quebrado for enviado.
2. **Disparo de Crawler Concorrente**: Garantir que disparar múltiplos crawlers de RI em paralelo seja gerenciado corretamente sem gerar duplicidade de processamento caso encontrem o mesmo PDF de forma concorrente.

---

## 4. Plano de Ação para Implementação

Para implementar este plano com o próximo agente desenvolvedor, execute os seguintes passos:

1. **Refatorar o Contrato Semântico**: Atualizar `schema.py` com o validador rigoroso de nomes de incorporadoras válidas.
2. **Refatorar Conexões de Banco**: Ajustar `database.py` utilizando o gerenciador de contexto `with get_connection()`.
3. **Aprimorar Resiliência do Pipeline**: Tratar PDFs sem texto e ajustar as regex do fallback.
4. **Implementar os Novos Casos de Teste**: Adicionar os novos métodos à suite `test_pipeline.py`.
5. **Rodar os Testes e Medir Cobertura**: Garantir que a cobertura continue em 100% dos cenários previstos.
