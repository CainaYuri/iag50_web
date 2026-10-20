"""Testes da leitura de XML com a nfelib."""
import io  # cria zip em memória
import zipfile  # monta um .zip para o teste

from leitor_nfe import importar, ler_xml  # o que será testado

PRODUTOR = {"52998224725"}  # CPF fictício do produtor dos exemplos
IE = {"0012345670012"}  # IE fictícia


def test_importacao_dos_exemplos(arquivos_exemplo):
    itens, rel = importar(arquivos_exemplo, PRODUTOR, IE)  # importa todos os exemplos
    assert rel.arquivos_lidos == 10  # 9 notas + 1 evento
    assert len(rel.duplicadas) == 1  # a cópia da nota 001
    assert len(rel.canceladas) == 1  # a nota 008, cancelada pelo evento
    assert len(rel.outro_destinatario) == 1  # a nota 007
    assert rel.notas_validas == 7 and len(itens) == 22  # notas e itens aproveitados


def test_campos_ibscbs_sao_lidos(arquivos_exemplo):
    itens, _ = importar(arquivos_exemplo, PRODUTOR, IE)  # importa
    adubo = next(i for i in itens if i["descricao"].startswith("ADUBO NPK 04"))  # primeiro item da nota 001
    assert adubo["cst_ibscbs"] == "515" and adubo["cclasstrib"] == "515001"  # grupo IBSCBS lido pela nfelib
    assert adubo["crt_emitente"] == "3"  # Enum do CRT convertido em texto


def test_zip_em_memoria(arquivos_exemplo):
    buf = io.BytesIO()  # arquivo zip em memória
    with zipfile.ZipFile(buf, "w") as z:  # cria o zip
        for nome, conteudo in arquivos_exemplo:  # adiciona cada exemplo
            z.writestr(nome, conteudo)
    itens, rel = importar([("notas.zip", buf.getvalue())], PRODUTOR, IE)  # importa o zip
    assert len(itens) == 22  # mesmo resultado dos arquivos soltos


def test_xml_malformado():
    r = ler_xml(b"<nfeProc><quebrado></nfeProc>", "x.xml")  # XML inválido
    assert r["tipo"] == "invalido"  # deve ser recusado sem quebrar o app


def test_xml_com_entidade_externa_e_bloqueado():
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><nfeProc>&e;</nfeProc>'  # ataque XXE
    r = ler_xml(xml, "ataque.xml")  # tenta ler
    assert r["tipo"] in {"invalido", "ignorado"}  # nunca lê o arquivo do servidor
