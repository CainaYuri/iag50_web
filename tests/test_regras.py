"""Testes da validação dos arquivos de regras (pydantic)."""
import pytest  # framework de testes
from pydantic import ValidationError  # erro que o pydantic gera quando algo está inválido

from conftest import RAIZ  # pasta principal do projeto
from modelos import Regra, carregar_configuracao  # o que será testado


def test_arquivos_atuais_sao_validos():
    regras, trat = carregar_configuracao(RAIZ / "regras_classificacao_v1.yaml",  # carrega os dois YAMLs
                                         RAIZ / "tratamento_tributario_v1.yaml")
    assert len(regras.regras) > 50  # há regras suficientes carregadas
    assert "T01" in trat.tratamentos  # o tratamento do Anexo IX existe


def test_regex_invalida_e_recusada():
    with pytest.raises(ValidationError):  # espera que a validação falhe
        Regra.model_validate({"id": "X", "camada": 5, "condicoes": {"desc_regex": "(ADUBO"},  # parêntese sem fechar
                              "resultado": {"categoria": "INS.FER", "confianca": 0.8}})


def test_confianca_fora_do_intervalo_e_recusada():
    with pytest.raises(ValidationError):  # espera falha
        Regra.model_validate({"id": "X", "camada": 3, "condicoes": {"ncm_prefixos": ["31"]},
                              "resultado": {"categoria": "INS.FER", "confianca": 1.5}})  # 1,5 > 1


def test_regra_sem_condicao_e_recusada():
    with pytest.raises(ValidationError):  # regra sem condição casaria com qualquer item
        Regra.model_validate({"id": "X", "camada": 3, "condicoes": {},
                              "resultado": {"categoria": "INS.FER", "confianca": 0.8}})


def test_campo_desconhecido_e_recusado():
    with pytest.raises(ValidationError):  # erro de digitação no nome da condição
        Regra.model_validate({"id": "X", "camada": 3, "condicoes": {"ncm_prefixo": ["31"]},  # falta o "s"
                              "resultado": {"categoria": "INS.FER", "confianca": 0.8}})
