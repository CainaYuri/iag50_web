"""
IAG 5.0 — Motor de regras de classificação.

Para cada item de NF-e de compra, decide a CATEGORIA (o que é o item), o
TRATAMENTO tributário de IBS/CBS e o EFEITO no crédito do produtor, sempre
indicando qual regra decidiu e por quê.

Uso direto:  python motor_regras.py            (roda os casos de teste)
             python motor_regras.py --mostrar  (mostra cada resultado)
"""
from __future__ import annotations  # anotações de tipo modernas

import re  # expressões regulares
import sys  # lê os argumentos da linha de comando
import unicodedata  # remove acentos
from dataclasses import dataclass, field  # classes simples de dados
from pathlib import Path  # caminhos de arquivo

import yaml  # lê os casos de teste

from modelos import carregar_configuracao  # carrega e VALIDA os YAMLs com pydantic

BASE = Path(__file__).parent  # pasta onde este arquivo está (os YAMLs ficam aqui)

ALERTAS_DE_REVISAO = {  # alertas que obrigam revisão humana; os demais são informativos
    "REVISAR_USO_PESSOAL", "REVISAR_BONIFICACAO", "NCM_DESCRICAO_INCOERENTE",
    "DESCRICAO_GENERICA", "DEFINIR_DESTINACAO", "CONFERIR_REGISTRO_GENEALOGICO",
    "CONFIRMAR_VINCULO", "TRATAMENTO_INDEFINIDO", "DESTINATARIO_DIVERGENTE",
    "CCLASSTRIB_DIVERGENTE",
}
CATEGORIAS_ANEXO_IX = {  # categorias de insumo que só têm benefício se o NCM estiver no Anexo IX
    "INS.FER", "INS.COR", "INS.DEF", "INS.BIO", "INS.INO", "INS.SEM", "INS.MUD",
    "PEC.NUT", "PEC.SAN", "PEC.GEN", "PEC.REP",
}
LIMIAR_CONFIANCA = 0.75  # abaixo disso o item vai para revisão
CRT_SIMPLES = {"1", "2", "4"}  # códigos de regime tributário do Simples Nacional e MEI


def normalizar(texto: str, abreviacoes: list[tuple[re.Pattern, str]]) -> str:
    """Deixa a descrição em maiúsculas, sem acento e com abreviações expandidas."""
    t = unicodedata.normalize("NFKD", texto or "")  # separa letras de seus acentos
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()  # descarta acentos e põe em maiúsculas
    t = re.sub(r"\s+", " ", t).strip()  # troca vários espaços por um só
    for padrao, substituto in abreviacoes:  # aplica cada abreviação do dicionário
        t = padrao.sub(substituto, t)  # ex.: "OL DIES" vira "OLEO DIESEL"
    return t  # descrição normalizada


def so_digitos(valor) -> str:
    """Mantém apenas os números de um código."""
    return re.sub(r"\D", "", str(valor or ""))  # remove tudo que não é dígito


@dataclass  # gera automaticamente o construtor da classe
class Resultado:
    """Resposta do motor para um item."""

    categoria: str  # código da taxonomia
    confianca: float  # confiança de 0 a 1
    regra: str  # id da regra que decidiu
    explicacao: str  # texto legível da regra
    tratamento: str = ""  # código do tratamento (T00 a T07)
    tratamento_nome: str = ""  # nome do tratamento
    efeito_credito: str = ""  # efeito no crédito do produtor
    reducao: float | None = None  # redução de alíquota aplicável
    alertas: list[str] = field(default_factory=list)  # alertas para o contador

    @property  # permite usar como atributo: resultado.revisar
    def revisar(self) -> bool:
        """True se o item precisa passar pelo contador."""
        return (self.confianca < LIMIAR_CONFIANCA  # confiança baixa
                or self.efeito_credito == "REVISAR"  # tratamento depende de destinação
                or any(a in ALERTAS_DE_REVISAO for a in self.alertas))  # algum alerta grave


class Motor:
    """Aplica as regras dos YAMLs aos itens de NF-e."""

    def __init__(self, arq_regras: Path, arq_tratamento: Path):
        self.cfg_regras, self.cfg_trat = carregar_configuracao(arq_regras, arq_tratamento)  # lê e valida
        self.abreviacoes = [(re.compile(p), s)  # compila as abreviações uma única vez (mais rápido)
                            for p, s in self.cfg_regras.abreviacoes.items()]  # para cada par (padrão, substituto)
        self.regras = []  # lista de regras preparadas para uso
        for ordem, r in enumerate(self.cfg_regras.regras):  # percorre as regras na ordem do arquivo
            c = r.condicoes  # condições da regra
            self.regras.append({  # guarda a regra com as regex já compiladas
                "id": r.id, "camada": r.camada, "ordem": ordem,  # identificação e posição no arquivo
                "cfop_em": set(c.cfop_em or []),  # CFOPs como conjunto (busca rápida)
                "ncm_prefixos": c.ncm_prefixos or [],  # prefixos de NCM
                "ncm_excluir": c.ncm_excluir or [],  # prefixos excluídos
                "cnae_em": set(c.cnae_emitente_em or []),  # CNAEs como conjunto
                "desc": re.compile(c.desc_regex) if c.desc_regex else None,  # regex positiva compilada
                "desc_nao": re.compile(c.desc_nao_regex) if c.desc_nao_regex else None,  # regex negativa
                "tem": c.model_dump(exclude_none=True).keys(),  # quais condições a regra usa
                "resultado": r.resultado, "explicacao": " ".join(r.explicacao.split()),  # decisão e texto
            })
        v = self.cfg_regras.validacoes  # parâmetros das validações documentais
        self.re_generica = re.compile(v["DESCRICAO_GENERICA"]["desc_regex"])  # regex de descrição genérica

    # ------------------------------------------------------------ casamento
    @staticmethod  # método que não depende do objeto (self)
    def _prefixo(ncm: str, prefixos: list[str]) -> str | None:
        """Devolve o prefixo MAIS LONGO da lista com que o NCM começa, ou None."""
        casados = [p for p in prefixos if ncm.startswith(p)]  # prefixos compatíveis
        return max(casados, key=len) if casados else None  # o mais específico vence

    def _casa(self, r: dict, it: dict, desc: str) -> tuple[bool, int]:
        """Testa uma regra contra um item. Devolve (casou?, tamanho do prefixo de NCM)."""
        if "cfop_em" in r["tem"] and it["cfop"] not in r["cfop_em"]:  # CFOP exigido não confere
            return False, 0  # a regra não se aplica a este item
        espec = 0  # especificidade do NCM (0 se a regra não usa NCM)
        if "ncm_prefixos" in r["tem"]:  # regra exige NCM
            p = self._prefixo(it["ncm"], r["ncm_prefixos"]) if it["ncm"] else None  # procura prefixo
            if p is None:  # NCM não confere ou está vazio
                return False, 0  # a regra não se aplica a este item
            espec = len(p)  # quanto maior o prefixo, mais específica a regra
        if r["ncm_excluir"] and it["ncm"] and self._prefixo(it["ncm"], r["ncm_excluir"]):  # NCM excluído
            return False, 0  # a regra não se aplica a este item
        if r["desc"] and not r["desc"].search(desc):  # descrição não contém o exigido
            return False, 0  # a regra não se aplica a este item
        if r["desc_nao"] and r["desc_nao"].search(desc):  # descrição contém o proibido
            return False, 0  # a regra não se aplica a este item
        if "cnae_emitente_em" in r["tem"] and it["cnae"] not in r["cnae_em"]:  # CNAE não confere
            return False, 0  # a regra não se aplica a este item
        return True, espec  # todas as condições foram atendidas

    def _melhor(self, camada: int, it: dict, desc: str) -> dict | None:
        """Escolhe a regra vencedora de uma camada."""
        cands = []  # candidatas no formato (especificidade, ordem invertida, regra)
        for r in self.regras:  # percorre todas as regras
            if r["camada"] != camada:  # ignora regras de outras camadas
                continue  # pula para o próximo da lista
            casou, espec = self._casa(r, it, desc)  # testa a regra no item
            if casou:  # a regra se aplica
                cands.append((espec, -r["ordem"], r))  # ordem negativa: a primeira do arquivo fica maior
        if not cands:  # nenhuma regra da camada casou
            return None  # nada encontrado
        if camada == 3:  # na camada NCM, vence o prefixo mais longo; empate, a que vem antes no arquivo
            return max(cands, key=lambda x: (x[0], x[1]))[2]  # devolve a regra vencedora
        return max(cands, key=lambda x: x[1])[2]  # nas demais, vence a primeira do arquivo

    @staticmethod  # método que não depende do objeto (self)
    def _resultado(r: dict) -> Resultado:
        """Converte a regra vencedora em um Resultado."""
        res = r["resultado"]  # decisão da regra
        return Resultado(res.categoria, res.confianca, r["id"], r["explicacao"],  # dados principais
                         alertas=[res.alerta] if res.alerta else [])  # alerta, se houver

    # ------------------------------------------------------------ classificação
    def classificar(self, item: dict) -> Resultado:
        """Classifica um item de NF-e."""
        it = {  # normaliza os campos usados nas regras
            "ncm": so_digitos(item.get("ncm")),  # NCM só com dígitos
            "cfop": so_digitos(item.get("cfop")),  # CFOP só com dígitos
            "cnae": so_digitos(item.get("cnae_emitente")),  # CNAE só com dígitos
            "crt": str(item.get("crt_emitente") or ""),  # regime do fornecedor
            "doc": so_digitos(item.get("doc_emitente")),  # CNPJ/CPF do fornecedor
            "dest_ok": item.get("destinatario_ok", True),  # destinatário confere?
            "cclasstrib": so_digitos(item.get("cclasstrib")),  # cClassTrib informado na nota
        }
        desc = normalizar(item.get("descricao", ""), self.abreviacoes)  # descrição pronta para as regex

        for camada in (1, 2):  # camadas 1 (operação) e 2 (vedação) decidem sozinhas
            r = self._melhor(camada, it, desc)  # procura regra vencedora
            if r:  # encontrou
                return self._finalizar(self._resultado(r), it, desc)  # conclui e devolve

        r3 = self._melhor(3, it, desc)  # regra por NCM
        r4 = self._melhor(4, it, desc)  # regra por fornecedor
        r5 = self._melhor(5, it, desc)  # regra por descrição
        n3 = self._resultado(r3) if r3 else None  # resultado da camada 3
        n4 = self._resultado(r4) if r4 else None  # resultado da camada 4
        n5 = self._resultado(r5) if r5 else None  # resultado da camada 5

        if n3 and n5 and n3.categoria != n5.categoria:  # NCM e descrição discordam
            if n3.confianca < 0.70:  # NCM genérico (ex.: grão): a descrição refina
                final = n5  # usa a descrição
                final.explicacao += f" (refina {n3.regra})"  # registra que refinou o NCM
            else:  # NCM específico: prevalece, mas com alerta
                final = n3  # usa o NCM
                final.confianca = round(final.confianca - 0.15, 2)  # reduz a confiança
                final.alertas.append("NCM_DESCRICAO_INCOERENTE")  # pede revisão
        elif n3:  # só o NCM decidiu (ou os dois concordam)
            final = n3  # usa o NCM
            if n5:  # descrição concorda com o NCM
                final.confianca = min(0.99, round(final.confianca + 0.04, 2))  # aumenta a confiança
        elif n5:  # só a descrição decidiu
            final = n5  # usa a descrição
            if n4 and n4.categoria == n5.categoria:  # fornecedor confirma a descrição
                final.confianca = min(0.95, round(final.confianca + 0.05, 2))  # aumenta a confiança
        elif n4:  # só o fornecedor indicou algo
            final = n4  # usa o fornecedor
        else:  # nada casou
            final = Resultado("NC", 0.0, "-", "Nenhuma regra casou")  # não classificado
        return self._finalizar(final, it, desc)  # aplica tratamento e validações

    # ------------------------------------------------------------ tratamento
    def _tratamento(self, categoria: str, ncm: str) -> tuple[str, list[str]]:
        """Encontra o tratamento tributário pela categoria e pelo NCM."""
        t = self.cfg_trat  # atalho para o arquivo de tratamento
        if categoria in t.mapa_por_categoria:  # categoria com tratamento fixo
            return t.mapa_por_categoria[categoria], []  # devolve o tratamento fixo da categoria
        melhor, tam_melhor = None, -1  # melhor linha encontrada e tamanho do seu prefixo
        for linha in t.mapa_entradas:  # percorre o mapa NCM x categoria
            if linha.categorias and categoria not in linha.categorias:  # linha é de outra categoria
                continue  # pula para o próximo da lista
            if linha.ncm:  # linha exige NCM
                p = self._prefixo(ncm, linha.ncm) if ncm else None  # procura prefixo compatível
                if p is None:  # NCM fora da linha
                    continue  # pula para o próximo da lista
                tam = len(p)  # especificidade
            else:  # linha vale para qualquer NCM
                tam = 0  # especificidade zero
            if tam > tam_melhor:  # mais específica que a anterior
                melhor, tam_melhor = linha, tam  # passa a ser a melhor
        if melhor:  # alguma linha serviu
            return melhor.tratamento, []  # devolve o tratamento da linha mais específica
        if categoria in CATEGORIAS_ANEXO_IX:  # parece insumo, mas o NCM não está no Anexo IX
            return "T07", ["TRATAMENTO_INDEFINIDO"]  # não presume benefício: manda revisar
        return t.tratamento_padrao, []  # alíquota padrão com crédito integral

    def _finalizar(self, res: Resultado, it: dict, desc: str) -> Resultado:
        """Completa o resultado com tratamento, modificadores e validações."""
        codigo, alertas_trat = self._tratamento(res.categoria, it["ncm"])  # busca o tratamento
        trat = self.cfg_trat.tratamentos[codigo]  # dados do tratamento
        res.tratamento, res.tratamento_nome = codigo, trat.nome  # código e nome
        res.efeito_credito, res.reducao = trat.efeito_credito, trat.reducao  # efeito e redução
        res.alertas += alertas_trat  # alertas vindos do tratamento

        creditavel = res.efeito_credito in {"CREDITO_INTEGRAL", "SEM_CREDITO_DIFERIDO"}  # efeitos ajustáveis
        if it["crt"] in CRT_SIMPLES:  # fornecedor do Simples Nacional
            res.alertas.append("FORNECEDOR_SIMPLES")  # informa o contador
            if creditavel:  # no Simples não há diferimento e o crédito é limitado
                res.efeito_credito = "CREDITO_SIMPLES"  # crédito limitado ao recolhido no Simples
        elif len(it["doc"]) == 11:  # fornecedor pessoa física (CPF)
            res.alertas.append("FORNECEDOR_PESSOA_FISICA")  # informa o contador
            if creditavel:  # compra de produtor não contribuinte
                res.efeito_credito = "CREDITO_PRESUMIDO"  # crédito presumido (art. 168)

        esperados = trat.cclasstrib_esperados  # códigos que o fornecedor deveria ter usado
        if it["cclasstrib"] and esperados and it["crt"] not in CRT_SIMPLES:  # há código e há o que comparar
            if it["cclasstrib"] not in esperados:  # código diferente do esperado
                res.alertas.append("CCLASSTRIB_DIVERGENTE")  # ex.: insumo do Anexo IX sem diferimento

        if self.re_generica.search(desc):  # descrição genérica demais
            res.alertas.append("DESCRICAO_GENERICA")  # pede descrição melhor ao fornecedor
        if it["dest_ok"] is False:  # nota destinada a outra pessoa
            res.alertas.append("DESTINATARIO_DIVERGENTE")
        res.alertas = list(dict.fromkeys(res.alertas))  # remove alertas repetidos mantendo a ordem
        return res  # resultado completo


def estimar_credito(valor: float, quantidade: float, unidade: str, efeito: str, reducao: float | None,
                    aliquota: float, diesel_rs_litro: float = 0, simples_pct: float = 0,
                    presumido_pct: float = 0) -> float | None:
    """
    Estima o crédito de um item. Devolve None quando depende de parâmetro
    não informado (zero) ou de revisão do contador.
    """
    if efeito == "CREDITO_INTEGRAL":  # crédito do imposto incidente
        return round(valor * aliquota * (1 - (reducao or 0)), 2)  # valor x alíquota x (1 - redução)
    if efeito in {"SEM_CREDITO_DIFERIDO", "VEDADO", "NEUTRO"}:  # efeitos sem crédito
        return 0.0
    if efeito == "CREDITO_AD_REM" and diesel_rs_litro > 0 and unidade.upper() in {"L", "LT", "LTS", "LITRO", "LITROS"}:
        return round(quantidade * diesel_rs_litro, 2)  # combustível: litros x R$ por litro
    if efeito == "CREDITO_SIMPLES" and simples_pct > 0:  # fornecedor do Simples
        return round(valor * simples_pct, 2)  # percentual informado pelo usuário
    if efeito == "CREDITO_PRESUMIDO" and presumido_pct > 0:  # produtor não contribuinte
        return round(valor * presumido_pct, 2)  # percentual informado pelo usuário
    return None  # depende de parâmetro ou de revisão


def rodar_casos(motor: Motor, arq_casos: Path, mostrar: bool = False) -> bool:
    """Roda os casos de teste do YAML e imprime o resultado. Devolve True se todos passarem."""
    casos = yaml.safe_load(arq_casos.read_text(encoding="utf-8"))["casos"]  # lê os casos
    falhas = 0  # contador de falhas
    for caso in casos:  # cada caso
        r, e = motor.classificar(caso["item"]), caso["esperado"]  # resultado obtido e esperado
        ok = (r.categoria == e["categoria"] and r.tratamento == e["tratamento"]  # compara categoria e tratamento
              and r.efeito_credito == e["efeito_credito"]  # compara efeito
              and all(a in r.alertas for a in e.get("alertas", [])))  # exige os alertas esperados
        falhas += not ok  # soma 1 se falhou
        if mostrar or not ok:  # imprime se pedido ou se falhou
            print(f"[{'OK' if ok else 'FALHA'}] {caso['id']}: {caso['item']['descricao']} -> "
                  f"{r.categoria} | {r.tratamento} | {r.efeito_credito} | {r.alertas}")
    print(f"{len(casos) - falhas}/{len(casos)} casos de teste passaram.")  # resumo
    return falhas == 0  # True se nenhum falhou


if __name__ == "__main__":  # só roda quando o arquivo é executado diretamente
    m = Motor(BASE / "regras_classificacao_v1.yaml", BASE / "tratamento_tributario_v1.yaml")  # cria o motor
    sys.exit(0 if rodar_casos(m, BASE / "casos_teste_v1.yaml", "--mostrar" in sys.argv) else 1)  # código de saída
