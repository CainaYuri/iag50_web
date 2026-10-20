"""
IAG 5.0 — Validação dos arquivos de regras (YAML) com pydantic.

Antes de o motor usar as regras, este módulo confere se os arquivos estão
bem escritos: campos obrigatórios presentes, confiança entre 0 e 1, regex
válida, categorias e tratamentos que realmente existem. Uma regra mal escrita
gera erro aqui, e não um resultado errado no relatório do cliente.
"""
from __future__ import annotations  # permite usar tipos como "list[str]" em versões antigas do Python

import re  # usado para testar se as expressões regulares das regras são válidas
from pathlib import Path  # manipula caminhos de arquivo de forma independente do sistema operacional
from typing import Literal, Optional  # Literal restringe valores; Optional indica campo que pode faltar

import yaml  # lê os arquivos .yaml (biblioteca pyyaml)
from pydantic import BaseModel, Field, field_validator, model_validator  # ferramentas de validação


# --------------------------------------------------------------------------
# Modelos do arquivo de REGRAS (regras_classificacao_v1.yaml)
# --------------------------------------------------------------------------
class Condicoes(BaseModel):
    """Condições de uma regra. Todas são opcionais e combinadas com E."""

    cfop_em: Optional[list[str]] = None  # lista de CFOPs do emitente que ativam a regra
    ncm_prefixos: Optional[list[str]] = None  # prefixos de NCM (ex.: "31" = capítulo 31)
    ncm_excluir: Optional[list[str]] = None  # prefixos de NCM que impedem a regra
    desc_regex: Optional[str] = None  # expressão que a descrição PRECISA conter
    desc_nao_regex: Optional[str] = None  # expressão que a descrição NÃO pode conter
    cnae_emitente_em: Optional[list[str]] = None  # CNAEs do fornecedor que ativam a regra

    model_config = {"extra": "forbid"}  # recusa campos desconhecidos (pega erros de digitação)

    @field_validator("desc_regex", "desc_nao_regex")  # roda esta checagem nos dois campos de regex
    @classmethod  # método da classe, exigido pelo validador do pydantic
    def regex_valida(cls, valor: Optional[str]) -> Optional[str]:
        if valor is not None:  # só testa se o campo foi preenchido
            try:  # tenta compilar a expressão
                re.compile(valor)  # se a regex estiver errada, o Python gera re.error
            except re.error as e:  # captura o erro de regex
                raise ValueError(f"regex inválida '{valor}': {e}") from e  # o pydantic só entende ValueError
        return valor  # devolve o valor sem alterar

    @field_validator("cfop_em", "ncm_prefixos", "ncm_excluir", "cnae_emitente_em")
    @classmethod  # método da classe, exigido pelo validador do pydantic
    def somente_digitos(cls, valores: Optional[list[str]]) -> Optional[list[str]]:
        if valores is None:  # campo não usado nesta regra
            return valores  # nada a validar
        for v in valores:  # percorre cada código da lista
            if not str(v).isdigit():  # códigos fiscais só têm dígitos
                raise ValueError(f"código '{v}' deve conter apenas dígitos")  # erro explicativo
        return [str(v) for v in valores]  # garante que todos fiquem como texto


class ResultadoRegra(BaseModel):
    """O que a regra decide quando casa."""

    categoria: str  # código da taxonomia (ex.: INS.FER)
    confianca: float = Field(ge=0, le=1)  # confiança entre 0 e 1 (ge = maior ou igual, le = menor ou igual)
    alerta: Optional[str] = None  # alerta opcional para o contador

    model_config = {"extra": "forbid"}  # recusa campos desconhecidos


class Regra(BaseModel):
    """Uma regra de classificação."""

    id: str  # identificador único (ex.: R3-NCM-31)
    camada: Literal[1, 2, 3, 4, 5]  # só aceita as cinco camadas definidas no motor
    condicoes: Condicoes = Field(default_factory=Condicoes)  # condições; vazio se não informado
    resultado: ResultadoRegra  # decisão da regra
    explicacao: str = ""  # texto exibido ao contador

    model_config = {"extra": "forbid"}  # recusa campos desconhecidos

    @model_validator(mode="after")  # roda depois de validar todos os campos
    def tem_alguma_condicao(self) -> "Regra":
        if not self.condicoes.model_dump(exclude_none=True):  # nenhuma condição preenchida?
            raise ValueError(f"regra {self.id} não tem nenhuma condição")  # regra vazia casaria com tudo
        return self  # regra aprovada


class ConfigRegras(BaseModel):
    """Arquivo de regras completo."""

    versao: int  # número da versão do arquivo
    vigencia_inicio: str  # data de início de vigência (texto, ex.: 2026-10-14)
    taxonomia: dict[str, str]  # código da categoria -> nome legível
    abreviacoes: dict[str, str] = {}  # padrão regex -> texto expandido
    regras: list[Regra]  # lista de regras
    validacoes: dict[str, dict]  # parâmetros das validações documentais

    @field_validator("vigencia_inicio", mode="before")  # converte antes da validação de tipo
    @classmethod  # método da classe, exigido pelo validador do pydantic
    def data_como_texto(cls, v):
        return str(v)  # o YAML lê datas como objeto date; aqui viram texto

    @model_validator(mode="after")
    def consistencia(self) -> "ConfigRegras":
        ids = [r.id for r in self.regras]  # coleta todos os identificadores
        repetidos = {i for i in ids if ids.count(i) > 1}  # acha os que aparecem mais de uma vez
        if repetidos:  # identificadores precisam ser únicos para a auditoria funcionar
            # interrompe com mensagem de erro explicativa
            raise ValueError(f"ids de regra repetidos: {sorted(repetidos)}")
        for r in self.regras:  # confere cada regra
            if r.resultado.categoria not in self.taxonomia:  # categoria inexistente?
                # interrompe com mensagem de erro explicativa
                raise ValueError(f"regra {r.id} usa categoria '{r.resultado.categoria}' fora da taxonomia")
        for padrao in self.abreviacoes:  # confere cada abreviação
            try:  # tenta compilar o padrão
                re.compile(padrao)  # padrão inválido gera re.error
            except re.error as e:  # captura o erro de regex
                raise ValueError(f"abreviação com regex inválida '{padrao}': {e}") from e  # converte para o pydantic
        return self  # arquivo aprovado


# --------------------------------------------------------------------------
# Modelos do arquivo de TRATAMENTO (tratamento_tributario_v1.yaml)
# --------------------------------------------------------------------------
EFEITOS_VALIDOS = Literal[  # lista fechada de efeitos que o simulador sabe tratar
    "CREDITO_INTEGRAL", "SEM_CREDITO_DIFERIDO", "CREDITO_AD_REM", "VEDADO", "NEUTRO", "REVISAR",
]


class Tratamento(BaseModel):
    """Um tratamento tributário (T00 a T07)."""

    nome: str  # descrição legível
    reducao: Optional[float] = Field(default=None, ge=0, le=1)  # redução de alíquota (0,60 = 60%)
    diferimento: Optional[bool] = None  # se há diferimento; None = depende
    efeito_credito: EFEITOS_VALIDOS  # efeito no crédito do produtor
    fundamento: str  # base legal
    cclasstrib_ref: str = ""  # texto de referência do cClassTrib
    cclasstrib_esperados: list[str] = []  # códigos que o fornecedor deveria informar na nota
    nota: str = ""  # observação para o revisor


class LinhaMapa(BaseModel):
    """Linha do mapa NCM x categoria -> tratamento."""

    id: str  # identificador da linha
    ncm: list[str]  # prefixos de NCM; lista vazia = qualquer NCM
    categorias: list[str]  # categorias às quais a linha se aplica; vazia = qualquer
    tratamento: str  # código do tratamento (ex.: T01)
    anexo_item: str = ""  # referência ao anexo/artigo
    descricao: str = ""  # descrição da linha


class ConfigTratamento(BaseModel):
    """Arquivo de tratamento completo."""

    versao: int  # versão do arquivo
    vigencia_inicio: str  # início de vigência
    referencia_legal: str  # texto da base legal consolidada
    tratamentos: dict[str, Tratamento]  # código -> tratamento
    mapa_entradas: list[LinhaMapa]  # mapa para compras
    mapa_por_categoria: dict[str, str]  # categoria -> tratamento direto
    tratamento_padrao: str  # tratamento quando nada casa
    modificadores_fornecedor: dict[str, dict]  # regras pelo regime do fornecedor
    tratamento_saidas: list[dict]  # tratamento das vendas (ainda não usado pelo motor)

    @field_validator("vigencia_inicio", mode="before")
    @classmethod  # método da classe, exigido pelo validador do pydantic
    def data_como_texto(cls, v):
        return str(v)  # data do YAML vira texto

    @model_validator(mode="after")
    def consistencia(self) -> "ConfigTratamento":
        codigos = set(self.tratamentos)  # códigos de tratamento existentes
        usados = [l.tratamento for l in self.mapa_entradas]  # códigos usados no mapa por NCM
        usados += list(self.mapa_por_categoria.values())  # mais os usados no mapa por categoria
        usados.append(self.tratamento_padrao)  # mais o tratamento padrão
        faltando = sorted(set(usados) - codigos)  # códigos usados que não existem
        if faltando:  # referência quebrada
            raise ValueError(f"tratamentos não definidos: {faltando}")  # interrompe com mensagem de erro explicativa
        return self  # arquivo aprovado


# --------------------------------------------------------------------------
# Carga e checagem cruzada dos dois arquivos
# --------------------------------------------------------------------------
def carregar_configuracao(arq_regras: Path, arq_tratamento: Path) -> tuple[ConfigRegras, ConfigTratamento]:
    """Lê os dois YAMLs, valida cada um e confere se conversam entre si."""
    regras = ConfigRegras.model_validate(  # valida o arquivo de regras
        yaml.safe_load(arq_regras.read_text(encoding="utf-8")))  # lê o YAML como dicionário
    trat = ConfigTratamento.model_validate(  # valida o arquivo de tratamento
        yaml.safe_load(arq_tratamento.read_text(encoding="utf-8")))  # lê o YAML de tratamento
    cats_tratamento = set(trat.mapa_por_categoria)  # categorias citadas no mapa por categoria
    for linha in trat.mapa_entradas:  # percorre o mapa por NCM
        cats_tratamento.update(linha.categorias)  # acrescenta as categorias de cada linha
    fora = sorted(cats_tratamento - set(regras.taxonomia))  # categorias que a taxonomia não conhece
    if fora:  # inconsistência entre os dois arquivos
        # interrompe com mensagem de erro explicativa
        raise ValueError(f"categorias do tratamento ausentes da taxonomia: {fora}")
    return regras, trat  # devolve os dois arquivos validados
