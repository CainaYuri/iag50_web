"""
IAG 5.0 — Leitura de XML de NF-e com a biblioteca nfelib.

Recebe arquivos .xml ou .zip, identifica eventos de cancelamento, descarta
duplicatas e devolve os itens das notas de COMPRA do produtor no formato
que o motor de regras espera. Tudo acontece em memória.
"""
from __future__ import annotations  # permite anotações de tipo modernas

import io  # trata bytes em memória como se fossem arquivos (para abrir .zip)
import re  # expressões regulares (usado para manter só os dígitos)
import zipfile  # abre arquivos .zip
from dataclasses import dataclass, field  # cria classes simples de dados
from enum import Enum  # a nfelib devolve alguns campos como Enum (ex.: CRT)
from functools import lru_cache  # guarda resultados para não repetir trabalho caro

from lxml import etree  # leitor de XML rápido (já vem como dependência da nfelib)
from nfelib.nfe.bindings.v4_0.nfe_v4_00 import Nfe  # classe da NF-e (usada na validação de schema)
from nfelib.nfe.bindings.v4_0.proc_nfe_v4_00 import NfeProc  # classe da NF-e autorizada (nfeProc)
from xsdata.formats.dataclass.parsers.config import ParserConfig  # configuração do leitor da nfelib

NS = "{http://www.portalfiscal.inf.br/nfe}"  # namespace oficial dos XMLs da NF-e
EVENTO_CANCELAMENTO = "110111"  # código do evento de cancelamento de NF-e
LIMITE_ARQUIVOS_ZIP = 20_000  # máximo de XMLs aceitos dentro de um .zip (proteção)
LIMITE_TAMANHO_XML = 5 * 1024 * 1024  # tamanho máximo de cada XML: 5 MB (proteção)

# Leitor tolerante: ignora campos que a nfelib ainda não conhece (leiautes futuros)
CONFIG_LEITOR = ParserConfig(fail_on_unknown_properties=False)
# Leitor lxml seguro: não resolve entidades externas nem acessa a rede
PARSER_SEGURO = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def so_digitos(valor) -> str:
    """Remove pontos, barras e traços; devolve só os números."""
    return re.sub(r"\D", "", str(valor or ""))  # \D = qualquer caractere que não seja dígito


def texto(valor) -> str:
    """Converte um campo da nfelib em texto simples (inclusive Enums)."""
    if valor is None:  # campo ausente na nota
        return ""  # devolve texto vazio
    if isinstance(valor, Enum):  # campos como CRT vêm como Enum (EmitCrt.VALUE_3)
        return str(valor.value)  # pega o valor real ("3")
    return str(valor).strip()  # qualquer outro valor vira texto sem espaços nas pontas


def numero(valor) -> float:
    """Converte um campo numérico da nota (texto) em número."""
    try:  # tenta converter
        return float(valor) if valor not in (None, "") else 0.0  # vazio vira zero
    except (TypeError, ValueError):  # texto inválido
        return 0.0  # assume zero em vez de quebrar a importação


@lru_cache(maxsize=1)  # o schema é carregado uma única vez e reaproveitado
def _schema_nfe() -> etree.XMLSchema:
    """Carrega o schema XSD oficial da NF-e que vem dentro da nfelib."""
    caminho = Nfe._get_schema_path()  # a nfelib sabe onde está o arquivo nfe_v4.00.xsd
    return etree.XMLSchema(etree.parse(caminho))  # compila o schema para validar notas


def validar_schema(raiz: etree._Element) -> list[str]:
    """Valida o elemento <NFe> contra o schema oficial e devolve as mensagens de erro."""
    nfe_el = raiz if raiz.tag == f"{NS}NFe" else raiz.find(f"{NS}NFe")  # acha o <NFe> dentro do nfeProc
    if nfe_el is None:  # não há elemento NFe
        return ["Elemento NFe não encontrado"]  # erro único
    schema = _schema_nfe()  # pega o schema já compilado
    if schema.validate(nfe_el):  # nota válida?
        return []  # nenhuma mensagem
    return [e.message for e in schema.error_log][:10]  # até 10 mensagens de erro


@dataclass  # gera automaticamente o construtor da classe
class RelatorioImportacao:
    """Resumo do que aconteceu na importação, para mostrar ao contador."""

    arquivos_lidos: int = 0  # total de XMLs abertos
    notas_validas: int = 0  # notas de compra aproveitadas
    itens: int = 0  # total de itens extraídos
    duplicadas: list[str] = field(default_factory=list)  # chaves repetidas
    canceladas: list[tuple[str, str]] = field(default_factory=list)  # (arquivo, chave) canceladas por evento
    invalidas: list[tuple[str, str]] = field(default_factory=list)  # (arquivo, motivo)
    outro_destinatario: list[tuple[str, str]] = field(default_factory=list)  # (arquivo, chave)
    emitidas_pelo_produtor: list[tuple[str, str]] = field(default_factory=list)  # vendas do produtor
    ignoradas: list[tuple[str, str]] = field(default_factory=list)  # (arquivo, motivo)
    fora_do_schema: list[tuple[str, str]] = field(default_factory=list)  # (arquivo, primeiro erro)


def ler_xml(conteudo: bytes, nome_arquivo: str, validar: bool = False) -> dict:
    """
    Lê um único XML. Devolve um dicionário com 'tipo':
    'nfe' (nota com itens), 'evento', 'ignorado' ou 'invalido'.
    """
    try:  # tenta interpretar o XML
        raiz = etree.fromstring(conteudo, PARSER_SEGURO)  # lê com o leitor seguro
    except etree.XMLSyntaxError as e:  # XML quebrado
        return {"tipo": "invalido", "motivo": f"XML malformado ({e})"}  # registra o motivo

    nome_raiz = etree.QName(raiz).localname  # nome da tag raiz sem o namespace
    if nome_raiz in {"procEventoNFe", "evento", "envEvento"}:  # é um evento, não uma nota
        inf = raiz.find(f".//{NS}infEvento")  # grupo com os dados do evento
        return {  # devolve só o necessário para tratar cancelamentos
            "tipo": "evento",
            "tp_evento": texto(inf.findtext(f"{NS}tpEvento")) if inf is not None else "",  # código do evento
            "chave": so_digitos(inf.findtext(f"{NS}chNFe")) if inf is not None else "",  # nota afetada
        }
    if nome_raiz not in {"nfeProc", "NFe"}:  # não é NF-e (pode ser CT-e, NFS-e etc.)
        return {"tipo": "ignorado", "motivo": f"Documento '{nome_raiz}' não é NF-e"}  # fica para a fase 2

    erros_schema = validar_schema(raiz) if validar else []  # validação opcional (mais lenta)

    try:  # converte o XML nos objetos da nfelib
        xml_txt = conteudo.decode("utf-8", errors="replace")  # a nfelib espera texto, não bytes
        if nome_raiz == "nfeProc":  # nota com protocolo de autorização
            proc = NfeProc.from_xml(xml_txt, CONFIG_LEITOR)  # lê o nfeProc inteiro
            nfe, prot = proc.NFe, proc.protNFe  # separa a nota e o protocolo
        else:  # nota sem protocolo (só o <NFe>)
            nfe, prot = Nfe.from_xml(xml_txt, CONFIG_LEITOR), None  # lê só a nota
    except Exception as e:  # qualquer falha de leitura estruturada
        # devolve o motivo da recusa
        return {"tipo": "invalido", "motivo": f"Não foi possível ler a NF-e ({type(e).__name__})"}

    inf = nfe.infNFe  # grupo principal da nota
    ide, emit, dest = inf.ide, inf.emit, inf.dest  # identificação, emitente e destinatário
    modelo = texto(ide.mod)  # modelo do documento (55 = NF-e)
    if modelo and modelo != "55":  # NFC-e (65) ou outro modelo
        # devolve o motivo da recusa
        return {"tipo": "ignorado", "motivo": f"Modelo {modelo} (o MVP lê apenas o modelo 55)"}

    nota = {  # dados da nota que interessam ao diagnóstico
        "chave": so_digitos(inf.Id),  # chave de acesso de 44 dígitos (o Id vem como "NFe" + chave)
        "arquivo": nome_arquivo,  # nome do arquivo de origem
        "numero": texto(ide.nNF),  # número da nota
        "serie": texto(ide.serie),  # série
        "data_emissao": texto(ide.dhEmi)[:10],  # data de emissão (AAAA-MM-DD)
        "emitente_doc": so_digitos(emit.CNPJ or emit.CPF),  # CNPJ ou CPF do fornecedor
        "emitente_nome": texto(emit.xNome),  # razão social do fornecedor
        "emitente_crt": texto(emit.CRT),  # regime tributário (1, 2 ou 4 = Simples/MEI)
        "emitente_cnae": so_digitos(emit.CNAE),  # CNAE, quando o fornecedor informa
        "dest_doc": so_digitos((dest.CNPJ or dest.CPF) if dest else ""),  # documento do destinatário
        "dest_ie": so_digitos(dest.IE if dest else ""),  # inscrição estadual do destinatário
        "dest_nome": texto(dest.xNome if dest else ""),  # nome do destinatário
        "entrega_municipio": texto(inf.entrega.xMun) if inf.entrega else "",  # município de entrega
        "protocolo": texto(prot.infProt.nProt) if prot and prot.infProt else "",  # protocolo de autorização
        "erros_schema": erros_schema,  # mensagens da validação de schema (se pedida)
    }

    itens = []  # lista que receberá os itens da nota
    for det in inf.det:  # percorre cada item (grupo det)
        prod, imposto = det.prod, det.imposto  # dados do produto e dos impostos
        ibscbs = getattr(imposto, "IBSCBS", None) if imposto else None  # grupo novo da reforma
        itens.append({  # monta o item no formato do motor
            "n_item": texto(det.nItem),  # número do item na nota
            "codigo": texto(prod.cProd),  # código do produto no fornecedor
            "descricao": texto(prod.xProd),  # descrição do produto
            "ncm": texto(prod.NCM),  # NCM
            "cfop": texto(prod.CFOP),  # CFOP do emitente
            "unidade": texto(prod.uCom),  # unidade comercial (LT, SC, KG...)
            "quantidade": numero(prod.qCom),  # quantidade
            "valor": round(numero(prod.vProd) - numero(prod.vDesc), 2),  # valor líquido de desconto
            "cst_ibscbs": texto(ibscbs.CST) if ibscbs else "",  # CST do IBS/CBS informado
            "cclasstrib": texto(ibscbs.cClassTrib) if ibscbs else "",  # cClassTrib informado
            "cnae_emitente": nota["emitente_cnae"],  # campo usado pela camada 4 do motor
            "crt_emitente": nota["emitente_crt"],  # campo usado pelos modificadores de fornecedor
            "doc_emitente": nota["emitente_doc"],  # identifica fornecedor pessoa física
        })
    return {"tipo": "nfe", "nota": nota, "itens": itens}  # nota pronta


def expandir_arquivos(nome: str, conteudo: bytes, rel: RelatorioImportacao):
    """Gera (nome, bytes) de cada XML; abre .zip em memória."""
    if nome.lower().endswith(".zip"):  # arquivo compactado
        try:  # o zip pode estar corrompido
            with zipfile.ZipFile(io.BytesIO(conteudo)) as z:  # abre o zip direto da memória
                membros = [m for m in z.infolist()  # lista o conteúdo do zip
                           if m.filename.lower().endswith(".xml") and not m.is_dir()]  # só arquivos .xml
                if len(membros) > LIMITE_ARQUIVOS_ZIP:  # zip grande demais
                    # registra a ocorrência no relatório
                    rel.invalidas.append((nome, f"Zip com mais de {LIMITE_ARQUIVOS_ZIP} XMLs"))
                    return  # interrompe a leitura deste zip
                for m in membros:  # para cada XML do zip
                    if m.file_size > LIMITE_TAMANHO_XML:  # XML grande demais
                        rel.invalidas.append((m.filename, "XML maior que 5 MB"))  # registra e pula
                        continue  # pula para o próximo da lista
                    yield m.filename, z.read(m)  # entrega o XML para leitura
        except zipfile.BadZipFile:  # zip corrompido
            rel.invalidas.append((nome, "Arquivo .zip corrompido"))  # registra a ocorrência no relatório
    elif nome.lower().endswith(".xml"):  # XML solto
        yield nome, conteudo  # entrega como está
    else:  # outra extensão
        # registra a ocorrência no relatório
        rel.ignoradas.append((nome, "Extensão não suportada (use .xml ou .zip)"))


def importar(arquivos: list[tuple[str, bytes]], docs_produtor: set[str],
             ies_produtor: set[str] | None = None, validar_schema_xml: bool = False
             ) -> tuple[list[dict], RelatorioImportacao]:
    """
    Importa vários arquivos e devolve os itens das notas de compra do produtor.

    arquivos: lista de (nome, bytes) enviados pelo usuário
    docs_produtor: CPFs/CNPJs do produtor, só com dígitos
    ies_produtor: inscrições estaduais do produtor (opcional)
    validar_schema_xml: se True, confere cada nota contra o schema oficial
    """
    ies_produtor = ies_produtor or set()  # conjunto vazio se não informado
    rel = RelatorioImportacao()  # relatório que será preenchido
    lidos, canceladas = [], set()  # notas lidas e chaves canceladas

    # 1ª passada: ler todos os XMLs e separar os eventos de cancelamento
    for nome, conteudo in arquivos:  # cada arquivo enviado
        for nome_xml, xml in expandir_arquivos(nome, conteudo, rel):  # cada XML (inclusive dentro de zip)
            rel.arquivos_lidos += 1  # conta o XML
            r = ler_xml(xml, nome_xml, validar_schema_xml)  # lê o XML
            if r["tipo"] == "evento":  # é um evento
                if r["tp_evento"] == EVENTO_CANCELAMENTO:  # é cancelamento
                    canceladas.add(r["chave"])  # guarda a chave cancelada
                else:  # outro evento (carta de correção, manifestação etc.)
                    rel.ignoradas.append((nome_xml, f"Evento {r['tp_evento']}"))  # apenas registra
                continue  # eventos não têm itens
            lidos.append((nome_xml, r))  # guarda para a 2ª passada

    # 2ª passada: aplicar as validações e extrair os itens
    itens, chaves_vistas = [], set()  # itens finais e chaves já processadas
    for nome_xml, r in lidos:  # cada documento lido
        if r["tipo"] == "invalido":  # não foi possível ler
            rel.invalidas.append((nome_xml, r["motivo"]))  # registra a ocorrência no relatório
            continue  # pula para o próximo da lista
        if r["tipo"] == "ignorado":  # não é NF-e modelo 55
            rel.ignoradas.append((nome_xml, r["motivo"]))  # registra a ocorrência no relatório
            continue  # pula para o próximo da lista
        nota = r["nota"]  # dados da nota
        if nota["chave"] in chaves_vistas:  # mesma nota enviada duas vezes
            rel.duplicadas.append(nota["chave"])  # registra a ocorrência no relatório
            continue  # pula para o próximo da lista
        chaves_vistas.add(nota["chave"])  # marca a chave como vista
        if nota["chave"] in canceladas:  # nota cancelada por evento
            rel.canceladas.append((nome_xml, nota["chave"]))  # registra a ocorrência no relatório
            continue  # pula para o próximo da lista
        if nota["emitente_doc"] in docs_produtor:  # nota emitida pelo próprio produtor = venda
            rel.emitidas_pelo_produtor.append((nome_xml, nota["chave"]))  # registra a ocorrência no relatório
            continue  # pula para o próximo da lista
        if nota["erros_schema"]:  # validação de schema encontrou problemas
            rel.fora_do_schema.append((nome_xml, nota["erros_schema"][0]))  # registra o primeiro erro
        destinatario_ok = nota["dest_doc"] in docs_produtor and (  # destinatário é o produtor?
            not ies_produtor or not nota["dest_ie"] or nota["dest_ie"] in ies_produtor)  # e a IE confere?
        if nota["dest_doc"] not in docs_produtor:  # nota para outra pessoa
            rel.outro_destinatario.append((nome_xml, nota["chave"]))  # entra com alerta
        rel.notas_validas += 1  # conta a nota aproveitada
        for it in r["itens"]:  # cada item da nota
            it["destinatario_ok"] = destinatario_ok  # o motor gera alerta se for False
            it["nota"] = nota  # mantém a referência à nota de origem
            itens.append(it)  # adiciona à lista final
            rel.itens += 1  # conta o item
    return itens, rel  # itens prontos para o motor + relatório
