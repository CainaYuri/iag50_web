# IAG 5.0 — Diagnóstico de créditos IBS/CBS (aplicativo web)

Versão de demonstração. Regras v1 em rascunho, pendentes de revisão por especialista tributário.
**Não envie XMLs reais de clientes para uma instalação pública.**

## Estrutura

| Arquivo | Para quê | Bibliotecas principais |
|---|---|---|
| `app.py` | Interface web | streamlit, pandas, validate-docbr |
| `leitor_nfe.py` | Lê XML de NF-e (.xml ou .zip), eventos, duplicatas, schema | nfelib, lxml |
| `motor_regras.py` | Classifica itens, define tratamento e efeito no crédito | — |
| `modelos.py` | Valida os YAMLs de regras antes do uso | pydantic, pyyaml |
| `fornecedores.py` | Busca o CNAE do fornecedor pelo CNPJ, com cache | requests, validate-docbr |
| `regras_classificacao_v1.yaml` | Taxonomia, regras por camada, validações | — |
| `tratamento_tributario_v1.yaml` | Tratamentos T00–T07, NCM x categoria, cClassTrib esperado | — |
| `casos_teste_v1.yaml` | 34 casos de teste do motor | — |
| `tests/` | 50 testes automatizados | pytest |
| `exemplos/` | NF-e fictícias e simplificadas para demonstração | — |
| `gerar_planilha.py` | Planilha de revisão para os contadores | openpyxl |

## Rodar no computador (Python 3.10 ou superior)

    python -m venv .venv
    .venv\Scripts\activate              (Windows)
    source .venv/bin/activate           (Mac/Linux)
    pip install -r requirements-dev.txt
    pytest                              # deve mostrar 50 passed
    streamlit run app.py                # abre em http://localhost:8501

## Publicar na web (demonstração com dados fictícios)

1. Crie um repositório no GitHub e envie todos os arquivos desta pasta (inclusive `.streamlit/`).
2. Acesse share.streamlit.io, entre com a conta do GitHub e crie um app.
3. Escolha o repositório, a branch `main` e o arquivo `app.py`; em configurações avançadas, Python 3.12.
4. Publique. Cada novo envio ao GitHub atualiza o app.

## Antes de usar com dados reais (piloto)

Hospedagem com login e, de preferência, região no Brasil; HTTPS; dados separados por escritório;
política de exclusão dos XMLs; contrato com cláusulas de LGPD.

## Fluxo de mudança nas regras

Revisor aprova/ajusta na planilha → ajuste vai para o YAML com nova versão → `modelos.py` valida
o arquivo → novo caso em `casos_teste_v1.yaml` → `pytest` precisa passar 100% antes de publicar.
