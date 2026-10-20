"""Testes do motor: cada caso do casos_teste_v1.yaml vira um teste separado."""
import pytest  # framework de testes
import yaml  # lê os casos

from conftest import RAIZ  # pasta principal do projeto
from motor_regras import estimar_credito  # função de estimativa de crédito

CASOS = yaml.safe_load((RAIZ / "casos_teste_v1.yaml").read_text(encoding="utf-8"))["casos"]  # lista de casos


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])  # um teste por caso, com o id no nome
def test_caso(motor, caso):
    r, e = motor.classificar(caso["item"]), caso["esperado"]  # obtido e esperado
    assert r.categoria == e["categoria"]  # categoria correta
    assert r.tratamento == e["tratamento"]  # tratamento correto
    assert r.efeito_credito == e["efeito_credito"]  # efeito correto
    for alerta in e.get("alertas", []):  # cada alerta esperado
        assert alerta in r.alertas  # precisa estar presente


def test_cclasstrib_divergente(motor):
    item = {"descricao": "SAL MINERAL BOVINOS", "ncm": "25010090", "cfop": "5102", "cclasstrib": "200038"}  # sem diferimento
    assert "CCLASSTRIB_DIVERGENTE" in motor.classificar(item).alertas  # o motor deve apontar


def test_cclasstrib_correto_nao_gera_alerta(motor):
    item = {"descricao": "SAL MINERAL BOVINOS", "ncm": "25010090", "cfop": "5102", "cclasstrib": "515001"}  # com diferimento
    assert "CCLASSTRIB_DIVERGENTE" not in motor.classificar(item).alertas  # nada a apontar


def test_estimativa_de_credito():
    assert estimar_credito(1000, 1, "UN", "CREDITO_INTEGRAL", 0, 0.27) == 270.0  # 1000 x 27%
    assert estimar_credito(1000, 1, "UN", "CREDITO_INTEGRAL", 0.6, 0.27) == 108.0  # com redução de 60%
    assert estimar_credito(1000, 1, "UN", "SEM_CREDITO_DIFERIDO", 0.6, 0.27) == 0.0  # diferido: zero
    assert estimar_credito(6000, 1000, "LT", "CREDITO_AD_REM", None, 0.27, diesel_rs_litro=0.5) == 500.0  # 1000 L x 0,50
    assert estimar_credito(6000, 1000, "LT", "CREDITO_AD_REM", None, 0.27) is None  # sem parâmetro: indefinido
