"""
IAG 5.0 — Diagnóstico de créditos IBS/CBS (aplicativo web).

Rodar no computador:  streamlit run app.py
Os XMLs são processados em memória e não são gravados em disco.
"""
from __future__ import annotations  # anotações de tipo modernas

import io  # cria o arquivo Excel em memória
import re  # separa e limpa os documentos digitados
from pathlib import Path  # caminhos de arquivo

import pandas as pd  # tabelas de dados (DataFrame)
import streamlit as st  # framework que transforma o script em página web
from validate_docbr import CNPJ, CPF  # valida CPF e CNPJ do produtor

from fornecedores import ConsultaCNPJ  # consulta de CNAE na BrasilAPI
from leitor_nfe import importar  # leitura dos XMLs com a nfelib
from motor_regras import BASE, Motor, estimar_credito  # motor de regras e estimativa de crédito

# ------------------------------------------------------------------ proteção contra execução direta
# Se o arquivo for iniciado com "python app.py" ou pelo botão ▶ do VS Code, o Streamlit não está
# ativo e a página não funcionaria. Nesse caso, o próprio app se reinicia do jeito certo.
if __name__ == "__main__":  # o arquivo foi executado diretamente (não importado)
    import sys  # acesso aos argumentos da linha de comando
    from streamlit import runtime  # informa se o Streamlit está rodando este script
    if not runtime.exists():  # executado pelo Python puro, sem o Streamlit
        from streamlit.web import cli as stcli  # interface de linha de comando do Streamlit
        sys.argv = ["streamlit", "run", str(Path(__file__).resolve())]  # equivale a "streamlit run app.py"
        sys.exit(stcli.main())  # inicia o app pelo Streamlit e encerra esta execução

# ------------------------------------------------------------------ identidade visual (Manual da Marca OBDC)
PASTA_MARCA = BASE / "assets"  # pasta com os arquivos da marca
LOGO = next((p for p in (PASTA_MARCA / "logo_obdc.png",  # arquivo oficial, quando for fornecido
                         PASTA_MARCA / "logo_obdc_provisorio.png")  # recorte provisório do manual
             if p.exists()), None)  # usa o primeiro que existir; None se nenhum existir
ISOTIPO = PASTA_MARCA / "isotipo_obdc.png"  # símbolo "C." oficial (opcional)
VERDE, AMARELO, CINZA = "#009490", "#FFC600", "#EBEBEB"  # cores institucionais do manual

st.set_page_config(  # deve ser o primeiro comando do Streamlit
    page_title="OBDC | Diagnóstico de créditos IBS/CBS",  # título da aba do navegador
    page_icon=str(ISOTIPO) if ISOTIPO.exists() else "🌾",  # ícone da aba: isotipo oficial, se houver
    layout="wide",  # usa a largura toda da tela
)
if LOGO:  # há arquivo de logotipo disponível
    # logotipo horizontal no topo da barra lateral, na largura de referência do manual (214 px),
    # para que o slogan "Você, satisfeito." continue legível
    st.sidebar.image(str(LOGO), width=214)

st.html(f"""<style>
/* faixa institucional no topo da página: segmento verde seguido de segmento amarelo */
.obdc-faixa {{display:flex; height:6px; margin:0 0 1rem 0; border-radius:3px; overflow:hidden;}}
.obdc-faixa span:first-child {{flex:0 0 72%; background:{VERDE};}}
.obdc-faixa span:last-child {{flex:1; background:{AMARELO};}}
/* títulos no verde da marca, em caixa alta, com o filete amarelo à esquerda */
h1 {{color:{VERDE} !important; text-transform:uppercase; border-left:6px solid {AMARELO};
     padding-left:0.75rem !important;}}
h2, h3 {{color:{VERDE} !important;}}
/* indicadores: cartão cinza com filete amarelo, valor em verde */
[data-testid="stMetric"] {{background:{CINZA}; border-left:5px solid {AMARELO}; border-radius:0.5rem;
                           padding:0.75rem 1rem;}}
[data-testid="stMetricValue"] {{color:{VERDE};}}
</style>""")  # estilos complementares que o tema do Streamlit não cobre

PASTA_EXEMPLOS = BASE / "exemplos"  # pasta com as notas fictícias
ARQUIVO_CACHE_CNPJ = BASE / ".cache" / "cnpj.json"  # cache das consultas de CNPJ
EXEMPLO = {"docs": "529.982.247-25", "ies": "0012345670012", "nome": "João da Silva (exemplo)"}  # produtor fictício

EFEITOS = {  # tradução dos códigos de efeito para o contador
    "CREDITO_INTEGRAL": "Crédito integral",
    "SEM_CREDITO_DIFERIDO": "Sem crédito (insumo com diferimento)",
    "CREDITO_AD_REM": "Crédito por litro (combustível)",
    "CREDITO_SIMPLES": "Crédito limitado (fornecedor do Simples)",
    "CREDITO_PRESUMIDO": "Crédito presumido (produtor não contribuinte)",
    "VEDADO": "Vedado (uso pessoal)",
    "NEUTRO": "Sem aquisição (não conta)",
    "REVISAR": "Depende de revisão",
}
ALERTAS = {  # tradução dos códigos de alerta
    "REVISAR_USO_PESSOAL": "possível uso pessoal",
    "REVISAR_BONIFICACAO": "bonificação: conferir destaque",
    "NCM_DESCRICAO_INCOERENTE": "NCM não combina com a descrição",
    "DESCRICAO_GENERICA": "descrição genérica",
    "DEFINIR_DESTINACAO": "definir destinação do grão",
    "CONFERIR_REGISTRO_GENEALOGICO": "conferir registro genealógico",
    "CONFIRMAR_VINCULO": "confirmar uso na atividade",
    "TRATAMENTO_INDEFINIDO": "NCM fora do Anexo IX",
    "DESTINATARIO_DIVERGENTE": "destinatário divergente",
    "CCLASSTRIB_DIVERGENTE": "cClassTrib da nota diverge do esperado",
    "FORNECEDOR_SIMPLES": "fornecedor do Simples",
    "FORNECEDOR_PESSOA_FISICA": "fornecedor pessoa física",
    "ENTREGA_FUTURA": "faturamento de entrega futura",
}


# ------------------------------------------------------------------ funções auxiliares
@st.cache_resource  # cria o motor uma única vez e reaproveita entre usuários e cliques
def carregar_motor() -> Motor:
    return Motor(BASE / "regras_classificacao_v1.yaml", BASE / "tratamento_tributario_v1.yaml")  # lê e valida as regras


def separar_documentos(texto: str) -> set[str]:
    """Transforma '529.982.247-25, 11.222.333/0001-81' em {'52998224725', '11222333000181'}."""
    partes = re.split(r"[,;\s]+", texto or "")  # separa por vírgula, ponto e vírgula ou espaço
    return {re.sub(r"\D", "", p) for p in partes if re.sub(r"\D", "", p)}  # mantém só os dígitos


def documentos_invalidos(docs: set[str]) -> list[str]:
    """Lista os CPFs/CNPJs com dígito verificador errado."""
    cpf, cnpj = CPF(), CNPJ()  # validadores da validate-docbr
    return [d for d in docs  # devolve os documentos que falharem
            # inválido se não for CPF de 11 nem CNPJ de 14 dígitos válidos
            if not ((len(d) == 11 and cpf.validate(d)) or (len(d) == 14 and cnpj.validate(d)))]


@st.cache_data(show_spinner=False)  # se os mesmos arquivos e opções forem enviados, reaproveita o resultado
def processar(arquivos: tuple, docs: frozenset, ies: frozenset, validar_schema: bool, consultar_cnae: bool):
    """Lê os XMLs, consulta CNAEs (opcional) e classifica cada item."""
    motor = carregar_motor()  # motor já carregado
    taxonomia = motor.cfg_regras.taxonomia  # nomes das categorias
    itens, rel = importar(list(arquivos), set(docs), set(ies), validar_schema)  # lê as notas
    resolvidos = 0  # quantos fornecedores tiveram o CNAE encontrado
    if consultar_cnae:  # usuário autorizou a consulta na internet
        resolvidos = ConsultaCNPJ(ARQUIVO_CACHE_CNPJ).enriquecer_itens(itens)  # preenche os CNAEs
    linhas = []  # linhas da tabela de resultado
    for it in itens:  # cada item importado
        r = motor.classificar(it)  # aplica as regras
        n = it["nota"]  # nota de origem
        linhas.append({  # uma linha por item
            "Nota": f'{n["numero"]}/{n["serie"]}',  # número/série
            "Emissão": n["data_emissao"],  # data de emissão
            "Emitente": n["emitente_nome"],  # fornecedor
            "Item": it["n_item"],  # número do item
            "Descrição": it["descricao"],  # descrição do produto
            "NCM": it["ncm"],  # NCM
            "CFOP": it["cfop"],  # CFOP
            "Unid.": it["unidade"],  # unidade
            "Qtd.": it["quantidade"],  # quantidade
            "Valor (R$)": it["valor"],  # valor líquido
            "Categoria": r.categoria,  # código da categoria
            "Categoria (nome)": taxonomia.get(r.categoria, r.categoria),  # nome da categoria
            "efeito": r.efeito_credito,  # código do efeito (uso interno)
            "Efeito no crédito": EFEITOS.get(r.efeito_credito, r.efeito_credito),  # efeito legível
            "reducao": r.reducao or 0.0,  # redução de alíquota (uso interno)
            "Confiança": r.confianca,  # confiança de 0 a 1
            "Alertas": "; ".join(ALERTAS.get(a, a) for a in r.alertas),  # alertas legíveis
            "Revisar": r.revisar,  # precisa de revisão?
            "Regra": r.regra,  # regra que decidiu
            "Explicação": r.explicacao,  # porquê da decisão
            "Tratamento": f"{r.tratamento} {r.tratamento_nome}",  # tratamento aplicado
            "CST IBS/CBS na nota": it["cst_ibscbs"],  # CST informado pelo fornecedor
            "cClassTrib na nota": it["cclasstrib"],  # cClassTrib informado pelo fornecedor
            "CNAE fornecedor": it.get("cnae_emitente", ""),  # CNAE usado na camada 4
            "Chave": n["chave"],  # chave de acesso
        })
    return pd.DataFrame(linhas), rel, resolvidos  # tabela, relatório de importação e CNAEs resolvidos


def brl(v: float) -> str:
    """Formata número como moeda brasileira: 1234.5 -> 'R$ 1.234,50'."""
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # troca separadores


def gerar_excel(df: pd.DataFrame, resumo: list[tuple], rel) -> bytes:
    """Cria o arquivo Excel com resumo, itens e ocorrências da importação."""
    buf = io.BytesIO()  # arquivo em memória
    colunas = [c for c in df.columns if c not in {"efeito", "reducao"}]  # esconde colunas internas
    ocorrencias = ([("Duplicada", c, "") for c in rel.duplicadas]  # notas repetidas
                   + [("Cancelada", a, c) for a, c in rel.canceladas]  # notas canceladas
                   + [("Inválida", a, m) for a, m in rel.invalidas]  # arquivos ilegíveis
                   + [("Fora do schema", a, m) for a, m in rel.fora_do_schema]  # erros de schema
                   + [("Outro destinatário", a, c) for a, c in rel.outro_destinatario]  # destinatário diferente
                   + [("Venda do produtor", a, c) for a, c in rel.emitidas_pelo_produtor]  # notas de saída
                   + [("Ignorada", a, m) for a, m in rel.ignoradas])  # demais
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:  # escritor de Excel
        pd.DataFrame(resumo, columns=["Indicador", "Valor"]).to_excel(xw, sheet_name="Resumo", index=False)  # aba 1
        df[colunas].to_excel(xw, sheet_name="Itens", index=False)  # aba 2
        pd.DataFrame(ocorrencias, columns=["Ocorrência", "Arquivo / chave", "Detalhe"]).to_excel(
            xw, sheet_name="Importação", index=False)  # aba 3
        for ws in xw.book.worksheets:  # ajusta cada aba
            for col in ws.columns:  # cada coluna
                largura = max(len(str(c.value or "")) for c in col[:200])  # maior texto (até 200 linhas)
                ws.column_dimensions[col[0].column_letter].width = min(max(10, largura + 2), 60)  # largura
            ws.freeze_panes = "A2"  # congela o cabeçalho
    return buf.getvalue()  # bytes do arquivo


def usar_exemplos():
    """Carrega as notas fictícias e preenche o produtor de exemplo."""
    st.session_state["arquivos_exemplo"] = tuple(  # guarda os arquivos na sessão do usuário
        (p.name, p.read_bytes()) for p in sorted(PASTA_EXEMPLOS.glob("*.xml")))  # lê cada XML de exemplo
    st.session_state["docs"] = EXEMPLO["docs"]  # preenche o CPF
    st.session_state["ies"] = EXEMPLO["ies"]  # preenche a IE
    st.session_state["nome"] = EXEMPLO["nome"]  # preenche o nome


def limpar():
    """Apaga tudo da sessão e reinicia o campo de upload."""
    for k in ("arquivos_exemplo", "docs", "ies", "nome"):  # chaves usadas
        st.session_state.pop(k, None)  # remove se existir
    st.session_state["id_upload"] = st.session_state.get("id_upload", 0) + 1  # nova chave = upload vazio


# ------------------------------------------------------------------ barra lateral
with st.sidebar:  # tudo dentro deste bloco aparece na lateral
    st.subheader("Produtor")  # título da seção
    st.text_input("Nome", key="nome")  # nome do produtor
    st.text_input("CPF ou CNPJ", key="docs",  # documento(s) do produtor
                  help="Separe vários por vírgula. Notas destinadas a outro documento recebem alerta.")
    st.text_input("Inscrições estaduais (opcional)", key="ies", help="Separe várias por vírgula.")  # IEs

    st.subheader("Cenário")  # parâmetros da simulação
    aliquota = st.number_input("Alíquota de referência IBS + CBS (%)", 10.0, 40.0, 27.0, 0.5,  # mín, máx, padrão, passo
                               help="27% é o cenário base do artigo; a alíquota definitiva ainda depende de calibragem.") / 100
    diesel = st.number_input("Crédito do diesel (R$ por litro)", 0.0, 5.0, 0.0, 0.05,  # valor ad rem
                             help="Combustíveis têm valor fixo por litro. Deixe 0 para não estimar.")
    simples = st.number_input("Crédito de fornecedor do Simples (% do valor)", 0.0, 15.0, 0.0, 0.5,  # percentual
                              help="Percentual efetivamente recolhido no Simples. Deixe 0 para não estimar.") / 100
    presumido = st.number_input("Crédito presumido de produtor não contribuinte (%)", 0.0, 15.0, 0.0, 0.5,  # percentual
                                help="Definido em ato do Executivo (art. 168). Deixe 0 para não estimar.") / 100

    st.subheader("Opções")  # opções de processamento
    validar_schema = st.toggle("Validar XML contra o schema oficial", value=False,  # liga/desliga
                               help="Usa os schemas da nfelib. Mais lento. As notas de exemplo são simplificadas e não passam.")
    consultar_cnae = st.toggle("Consultar CNAE dos fornecedores (BrasilAPI)", value=False,  # liga/desliga
                               help="Envia apenas o CNPJ dos fornecedores a um serviço público de dados abertos. CPFs nunca são enviados.")

    st.divider()  # linha separadora
    st.caption("Versão de demonstração. Use apenas notas fictícias: arquivos enviados aqui "  # texto pequeno de apoio
               "não devem conter dados reais de clientes.")  # aviso de LGPD

# ------------------------------------------------------------------ área principal
st.html('<div class="obdc-faixa"><span></span><span></span></div>')  # faixa verde e amarela do manual
st.title("Diagnóstico de créditos IBS/CBS")  # título da página
# parágrafo de texto
st.write("Envie os XMLs das notas de compra do produtor. O sistema classifica cada item, aponta o efeito "
         "no crédito de IBS/CBS no regime pleno e separa o que precisa de revisão do contador.")  # explicação

col_upload, col_botoes, _ = st.columns([2, 1, 1])  # três colunas; a última fica vazia
with col_upload:  # coluna do upload
    enviados = st.file_uploader("Notas fiscais (.xml ou .zip)", type=["xml", "zip"],  # tipos aceitos
                                accept_multiple_files=True,  # vários arquivos de uma vez
                                key=f"upload_{st.session_state.get('id_upload', 0)}")  # chave muda ao limpar
with col_botoes:  # coluna dos botões
    st.write("")  # espaço para alinhar com o upload
    st.button("Usar notas de exemplo", on_click=usar_exemplos, width="stretch",  # carrega exemplos
              disabled=not PASTA_EXEMPLOS.exists())  # desabilita se a pasta não existir
    st.button("Limpar", on_click=limpar, width="stretch")  # reinicia a tela

arquivos = tuple((f.name, f.getvalue()) for f in (enviados or []))  # arquivos enviados como (nome, bytes)
arquivos = arquivos or st.session_state.get("arquivos_exemplo", ())  # se não houver, usa os exemplos
docs = separar_documentos(st.session_state.get("docs", ""))  # documentos do produtor
ies = separar_documentos(st.session_state.get("ies", ""))  # inscrições estaduais

if not arquivos:  # nada enviado ainda
    # caixa azul de orientação
    st.info("Envie os XMLs de compra ou clique em **Usar notas de exemplo** para ver o diagnóstico.")
    st.stop()  # interrompe o script aqui (o resto da página não é desenhado)
if not docs:  # falta o documento do produtor
    # caixa amarela de aviso
    st.warning("Informe o CPF ou CNPJ do produtor na barra lateral. Ele separa as compras das demais notas.")
    st.stop()  # interrompe
invalidos = documentos_invalidos(docs)  # confere os dígitos verificadores
if invalidos:  # algum documento inválido
    # caixa amarela de aviso
    st.warning(f"Documento com dígito verificador inválido: {', '.join(invalidos)}. Confira a digitação.")

with st.spinner("Lendo e classificando as notas..."):  # mostra "carregando" enquanto processa
    df, rel, cnaes = processar(arquivos, frozenset(docs), frozenset(ies), validar_schema, consultar_cnae)

if df.empty:  # nenhum item de compra
    # caixa vermelha de erro
    st.error("Nenhum item de compra encontrado. Confira o CPF/CNPJ do produtor e a aba Importação.")
    st.stop()  # interrompe

df["Crédito estimado (R$)"] = [  # calcula o crédito item a item com os parâmetros da barra lateral
    estimar_credito(v, q, u, e, red, aliquota, diesel, simples, presumido)  # função do motor
    # percorre as colunas linha a linha
    for v, q, u, e, red in zip(df["Valor (R$)"], df["Qtd."], df["Unid."], df["efeito"], df["reducao"])]

contam = df[df["efeito"] != "NEUTRO"]  # itens que são aquisições de verdade
total = contam["Valor (R$)"].sum()  # total de compras analisadas
credito = df["Crédito estimado (R$)"].fillna(0).sum()  # soma dos créditos estimados
diferido = df.loc[df["efeito"] == "SEM_CREDITO_DIFERIDO", "Valor (R$)"].sum()  # insumos com diferimento
pendente = df.loc[df["Crédito estimado (R$)"].isna(), "Valor (R$)"].sum()  # valor sem estimativa
n_revisar = int(df["Revisar"].sum())  # quantidade de itens para revisão

st.subheader(st.session_state.get("nome") or "Produtor")  # nome do produtor como subtítulo
m1, m2, m3, m4 = st.columns(4)  # quatro indicadores lado a lado
# indicador 1: total de compras
m1.metric("Compras analisadas", brl(total), help="Exclui remessas, comodato e faturamento de entrega futura.")
# indicador 2: crédito estimado
m2.metric("Crédito estimado", brl(credito), help=f"Com alíquota de referência de {aliquota:.1%}.")
m3.metric("Insumos com diferimento", brl(diferido),  # indicador 3: insumos sem crédito por diferimento
          help="Insumos do Anexo IX: chegam sem IBS/CBS no preço e não geram crédito (art. 138, § 2º).")
# indicador 4: valor que ainda depende de revisão
m4.metric("Dependem de revisão ou parâmetro", brl(pendente), help=f"{n_revisar} itens marcados para revisão.")
if diferido > 0:  # explica o efeito do diferimento
    st.caption(f"Se o diferimento fosse ignorado, o crédito apareceria inflado em cerca de "  # texto pequeno de apoio
               f"{brl(diferido * aliquota * 0.4)}. Esse valor não existe para o produtor.")
if consultar_cnae:  # informa o resultado da consulta de CNAE
    st.caption(f"CNAE obtido na BrasilAPI para {cnaes} fornecedor(es).")  # texto pequeno de apoio

colunas = ["Nota", "Emitente", "Descrição", "NCM", "CFOP", "Valor (R$)", "Categoria (nome)",  # colunas exibidas
           "Efeito no crédito", "Crédito estimado (R$)", "Confiança", "Alertas", "cClassTrib na nota",
           "Regra", "Explicação"]
formato = {  # formatação das colunas na tela
    "Valor (R$)": st.column_config.NumberColumn(format="%.2f"),  # duas casas decimais
    "Crédito estimado (R$)": st.column_config.NumberColumn(format="%.2f"),
    "Confiança": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),  # barra de progresso
    "Explicação": st.column_config.TextColumn(width="large"),  # coluna larga
}
vis = df.copy()  # cópia para exibição
vis["Confiança"] = (vis["Confiança"] * 100).round().astype(int)  # 0,93 vira 93

aba1, aba2, aba3, aba4 = st.tabs([f"Para revisar ({n_revisar})", f"Todos os itens ({len(df)})",  # quatro abas
                                  "Por efeito e categoria", "Importação"])
with aba1:  # itens que precisam do contador
    rev = vis[vis["Revisar"]].sort_values("Valor (R$)", ascending=False)  # maiores valores primeiro
    if rev.empty:  # nada a revisar
        st.success("Nenhum item precisa de revisão.")  # caixa verde de confirmação
    else:  # mostra a tabela
        st.caption("Ordenado por valor: revise primeiro o que mais pesa no resultado.")  # texto pequeno de apoio
        # mostra a tabela interativa
        st.dataframe(rev[colunas], column_config=formato, hide_index=True, width="stretch")
with aba2:  # todos os itens, com filtro
    filtro = st.multiselect("Filtrar por efeito no crédito", sorted(df["Efeito no crédito"].unique()))  # filtro
    base = vis[vis["Efeito no crédito"].isin(filtro)] if filtro else vis  # aplica o filtro, se houver
    st.dataframe(base[colunas], column_config=formato, hide_index=True, width="stretch")  # mostra a tabela interativa
with aba3:  # resumos agrupados
    esq, dir_ = st.columns(2)  # duas colunas
    with esq:  # por efeito
        st.markdown("**Por efeito no crédito**")  # título em negrito
        g = df.groupby("Efeito no crédito", as_index=False).agg(  # agrupa por efeito
            Itens=("Valor (R$)", "size"),  # quantidade de itens
            Valor=("Valor (R$)", "sum"),  # soma dos valores
            Crédito=("Crédito estimado (R$)", lambda s: s.fillna(0).sum()),  # soma dos créditos
        ).sort_values("Valor", ascending=False)  # maiores primeiro
        st.dataframe(g, hide_index=True, width="stretch",  # mostra a tabela interativa
                     column_config={"Valor": st.column_config.NumberColumn(format="%.2f"),
                                    "Crédito": st.column_config.NumberColumn(format="%.2f")})
    with dir_:  # por categoria
        st.markdown("**Por categoria**")  # título em negrito
        g2 = df.groupby("Categoria (nome)", as_index=False).agg(  # agrupa por categoria
            Itens=("Valor (R$)", "size"), Valor=("Valor (R$)", "sum")).sort_values("Valor", ascending=False)
        st.dataframe(g2, hide_index=True, width="stretch",  # mostra a tabela interativa
                     column_config={"Valor": st.column_config.NumberColumn(format="%.2f")})
with aba4:  # relatório da importação
    st.markdown(  # lista com os números da importação
        f"- Arquivos lidos: **{rel.arquivos_lidos}**\n"
        f"- Notas de compra válidas: **{rel.notas_validas}** ({rel.itens} itens)\n"
        f"- Duplicadas (mesma chave): **{len(rel.duplicadas)}**\n"
        f"- Canceladas por evento: **{len(rel.canceladas)}**\n"
        f"- Destinadas a outro CPF/CNPJ (incluídas com alerta): **{len(rel.outro_destinatario)}**\n"
        f"- Emitidas pelo próprio produtor (vendas, fora do MVP): **{len(rel.emitidas_pelo_produtor)}**\n"
        f"- Fora do schema oficial: **{len(rel.fora_do_schema)}**"
        f"{'' if validar_schema else ' (validação desligada)'}\n"
        f"- Inválidas: **{len(rel.invalidas)}**\n"
        f"- Ignoradas: **{len(rel.ignoradas)}**")
    problemas = ([("Inválida", a, m) for a, m in rel.invalidas]  # junta as ocorrências em uma tabela
                 + [("Cancelada", a, c) for a, c in rel.canceladas]
                 + [("Fora do schema", a, m) for a, m in rel.fora_do_schema]
                 + [("Ignorada", a, m) for a, m in rel.ignoradas])
    if problemas:  # mostra só se houver
        # mostra a tabela interativa
        st.dataframe(pd.DataFrame(problemas, columns=["Ocorrência", "Arquivo", "Detalhe"]),
                     hide_index=True, width="stretch")

resumo = [  # linhas da aba Resumo do Excel
    ("Produtor", st.session_state.get("nome") or "Produtor"),
    ("Alíquota de referência", f"{aliquota:.1%}"),
    ("Compras analisadas (R$)", round(total, 2)),
    ("Crédito estimado (R$)", round(credito, 2)),
    ("Insumos com diferimento, sem crédito (R$)", round(diferido, 2)),
    ("Dependem de revisão ou parâmetro (R$)", round(pendente, 2)),
    ("Itens para revisar", n_revisar),
    ("Versão das regras", "v1 (rascunho pendente de revisão por especialista)"),
]
st.download_button("Baixar resultado em Excel", gerar_excel(df, resumo, rel),  # botão de download
                   file_name="diagnostico_creditos_ibs_cbs.xlsx",  # nome do arquivo baixado
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")  # tipo do arquivo

st.divider()  # linha separadora
# texto pequeno de apoio
st.caption("OBDC | Você, satisfeito. Estimativa de apoio à decisão, baseada na LC 214/2025 consolidada e em regras v1 ainda em revisão. "
           "Não substitui a análise do contador ou parecer jurídico.")  # aviso final