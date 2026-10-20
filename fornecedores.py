"""
IAG 5.0 — Dados do fornecedor a partir do CNPJ.

O XML da NF-e raramente traz o CNAE do fornecedor, mas a camada 4 do motor
depende dele (posto, supermercado, revenda de insumos...). Este módulo consulta
a BrasilAPI, que publica dados cadastrais abertos de CNPJ, e guarda as respostas
em cache para não repetir consultas.

Só consulta CNPJ (empresas). CPF de pessoa física nunca é enviado.
"""
from __future__ import annotations  # anotações de tipo modernas

import json  # grava e lê o cache em formato JSON
import time  # usado para calcular a idade do cache e pausar entre consultas
from pathlib import Path  # caminhos de arquivo

import requests  # faz as chamadas HTTP à BrasilAPI
from validate_docbr import CNPJ  # valida o dígito verificador do CNPJ

URL_BRASILAPI = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"  # endereço da consulta
VALIDADE_CACHE_DIAS = 30  # depois de 30 dias a informação é consultada de novo
TIMEOUT_SEGUNDOS = 6  # tempo máximo de espera por resposta
PAUSA_ENTRE_CONSULTAS = 0.3  # intervalo entre chamadas, para respeitar o serviço

_validador_cnpj = CNPJ()  # instância reutilizável do validador


class ConsultaCNPJ:
    """Consulta CNPJs com cache em arquivo."""

    def __init__(self, arquivo_cache: Path | None = None, sessao: requests.Session | None = None):
        self.arquivo_cache = arquivo_cache  # onde o cache é salvo (None = só em memória)
        self.sessao = sessao or requests.Session()  # sessão HTTP reaproveita a conexão
        self.sessao.headers.update({"User-Agent": "IAG50-diagnostico/1.0"})  # identifica o aplicativo
        self.cache: dict[str, dict] = {}  # dicionário CNPJ -> dados
        if arquivo_cache and arquivo_cache.exists():  # já existe cache salvo?
            try:  # o arquivo pode estar corrompido
                self.cache = json.loads(arquivo_cache.read_text(encoding="utf-8"))  # carrega o cache
            except (json.JSONDecodeError, OSError):  # arquivo ilegível
                self.cache = {}  # começa do zero

    def _salvar(self) -> None:
        """Grava o cache no disco, se houver arquivo configurado."""
        if self.arquivo_cache:  # cache em arquivo habilitado
            self.arquivo_cache.parent.mkdir(parents=True, exist_ok=True)  # cria a pasta, se faltar
            # grava o dicionário como JSON
            self.arquivo_cache.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    def _valido_no_cache(self, cnpj: str) -> bool:
        """Diz se o CNPJ está no cache e ainda dentro da validade."""
        registro = self.cache.get(cnpj)  # procura o CNPJ
        if not registro:  # não está no cache
            return False  # resultado negativo
        idade_dias = (time.time() - registro.get("consultado_em", 0)) / 86400  # idade em dias
        return idade_dias < VALIDADE_CACHE_DIAS  # válido se for mais novo que o limite

    def consultar(self, cnpj: str) -> dict | None:
        """
        Devolve {'cnae', 'cnae_descricao', 'razao_social', 'simples'} ou None.
        None significa: CNPJ inválido, não encontrado ou serviço indisponível.
        """
        cnpj = "".join(c for c in str(cnpj) if c.isdigit())  # mantém só os dígitos
        if len(cnpj) != 14 or not _validador_cnpj.validate(cnpj):  # não é CNPJ válido (ou é CPF)
            return None  # não consulta
        if self._valido_no_cache(cnpj):  # já consultado recentemente
            return self.cache[cnpj]["dados"]  # devolve sem acessar a internet
        try:  # a chamada pode falhar
            resp = self.sessao.get(URL_BRASILAPI.format(cnpj=cnpj), timeout=TIMEOUT_SEGUNDOS)  # consulta
        except requests.RequestException:  # sem internet, tempo esgotado etc.
            return None  # segue sem o CNAE
        if resp.status_code != 200:  # 404 = não encontrado; 429 = limite de consultas
            return None  # nada encontrado
        d = resp.json()  # converte a resposta em dicionário
        dados = {  # guarda só o que o motor usa
            "cnae": str(d.get("cnae_fiscal") or "").zfill(7) if d.get("cnae_fiscal") else "",  # CNAE com 7 dígitos
            "cnae_descricao": d.get("cnae_fiscal_descricao", ""),  # descrição do CNAE
            "razao_social": d.get("razao_social", ""),  # nome empresarial
            "simples": d.get("opcao_pelo_simples"),  # True/False/None conforme o cadastro
        }
        self.cache[cnpj] = {"dados": dados, "consultado_em": time.time()}  # registra no cache
        self._salvar()  # persiste o cache
        time.sleep(PAUSA_ENTRE_CONSULTAS)  # pausa curta antes da próxima chamada
        return dados  # devolve os dados

    def enriquecer_itens(self, itens: list[dict], limite: int = 200) -> int:
        """
        Preenche o CNAE dos itens cujo fornecedor não informou no XML.
        Devolve quantos CNPJs foram resolvidos. 'limite' evita centenas de chamadas.
        """
        sem_cnae = sorted({it["doc_emitente"] for it in itens  # CNPJs únicos dos fornecedores
                           # só fornecedores PJ sem CNAE no XML
                           if not it.get("cnae_emitente") and len(it.get("doc_emitente", "")) == 14})
        resolvidos = {}  # CNPJ -> CNAE encontrado
        for cnpj in sem_cnae[:limite]:  # consulta até o limite
            dados = self.consultar(cnpj)  # busca (cache ou internet)
            if dados and dados["cnae"]:  # encontrou CNAE
                resolvidos[cnpj] = dados["cnae"]  # guarda
        for it in itens:  # aplica o CNAE em todos os itens daquele fornecedor
            if it["doc_emitente"] in resolvidos:  # fornecedor resolvido
                it["cnae_emitente"] = resolvidos[it["doc_emitente"]]  # preenche o campo
                it["cnae_origem"] = "BrasilAPI"  # registra a origem da informação
        return len(resolvidos)  # quantidade de fornecedores enriquecidos
