"""Testes da consulta de CNPJ, sem acessar a internet (a resposta é simulada)."""
from fornecedores import ConsultaCNPJ  # o que será testado


class RespostaFalsa:
    """Imita a resposta da biblioteca requests."""

    def __init__(self, status, dados):
        self.status_code = status  # código HTTP
        self._dados = dados  # corpo da resposta

    def json(self):
        return self._dados  # devolve o corpo como dicionário


class SessaoFalsa:
    """Imita requests.Session e conta quantas chamadas foram feitas."""

    def __init__(self):
        self.headers = {}  # cabeçalhos (exigido pelo código)
        self.chamadas = 0  # contador de chamadas

    def get(self, url, timeout):
        self.chamadas += 1  # registra a chamada
        return RespostaFalsa(200, {"cnae_fiscal": 4683400, "cnae_fiscal_descricao": "Atacado de defensivos",
                                   "razao_social": "EXEMPLO", "opcao_pelo_simples": False})  # resposta típica


def test_consulta_e_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("fornecedores.PAUSA_ENTRE_CONSULTAS", 0)  # não espera entre chamadas no teste
    sessao = SessaoFalsa()  # sessão simulada
    c = ConsultaCNPJ(tmp_path / "cache.json", sessao)  # cache em pasta temporária
    assert c.consultar("11.222.333/0001-81")["cnae"] == "4683400"  # primeira consulta vai à "internet"
    assert c.consultar("11222333000181")["cnae"] == "4683400"  # segunda vem do cache
    assert sessao.chamadas == 1  # só houve uma chamada real


def test_cpf_e_cnpj_invalido_nao_sao_consultados(tmp_path):
    sessao = SessaoFalsa()  # sessão simulada
    c = ConsultaCNPJ(tmp_path / "cache.json", sessao)  # consulta com cache temporário
    assert c.consultar("529.982.247-25") is None  # CPF nunca é enviado
    assert c.consultar("11222333000180") is None  # CNPJ com dígito errado
    assert sessao.chamadas == 0  # nenhuma chamada foi feita
