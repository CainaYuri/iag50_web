"""Configuração comum dos testes (o pytest lê este arquivo automaticamente)."""
import sys  # acesso à lista de pastas onde o Python procura módulos
from pathlib import Path  # caminhos de arquivo

import pytest  # framework de testes

RAIZ = Path(__file__).resolve().parent.parent  # pasta principal do projeto (um nível acima de tests/)
sys.path.insert(0, str(RAIZ))  # permite "import motor_regras" etc. dentro dos testes


@pytest.fixture(scope="session")  # cria o objeto uma vez e compartilha entre todos os testes
def motor():
    from motor_regras import Motor  # importa aqui para usar o sys.path ajustado acima
    return Motor(RAIZ / "regras_classificacao_v1.yaml", RAIZ / "tratamento_tributario_v1.yaml")  # motor pronto


@pytest.fixture(scope="session")
def arquivos_exemplo():
    return [(p.name, p.read_bytes()) for p in sorted((RAIZ / "exemplos").glob("*.xml"))]  # (nome, bytes) dos exemplos
