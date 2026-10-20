"""Gera a planilha de revisão a partir dos YAMLs (fonte de verdade) e dos casos de teste."""
from pathlib import Path

import yaml
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from motor_regras import BASE, Motor

SAIDA = BASE / "IAG50_tratamento_tributario_e_regras_v1.xlsx"

F = "Arial"
H_FILL = PatternFill("solid", start_color="1F4E3D")
H_FONT = Font(name=F, bold=True, color="FFFFFF", size=10)
BODY = Font(name=F, size=10)
BOLD = Font(name=F, size=10, bold=True)
TITLE = Font(name=F, size=14, bold=True, color="1F4E3D")
INPUT_FILL = PatternFill("solid", start_color="FFFF00")
INPUT_FONT = Font(name=F, size=10, color="0000FF")
ZEBRA = PatternFill("solid", start_color="F3F7F5")
THIN = Side(style="thin", color="C9D3CE")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
EFEITO_FILL = {
    "CREDITO_INTEGRAL": "D9EAD3", "SEM_CREDITO_DIFERIDO": "FCE5CD", "CREDITO_AD_REM": "DDEBF7",
    "VEDADO": "F4CCCC", "NEUTRO": "EEEEEE", "REVISAR": "FFF2CC",
    "CREDITO_SIMPLES": "EAD1DC", "CREDITO_PRESUMIDO": "D0E0E3",
}

regras = yaml.safe_load((BASE / "regras_classificacao_v1.yaml").read_text(encoding="utf-8"))
trat = yaml.safe_load((BASE / "tratamento_tributario_v1.yaml").read_text(encoding="utf-8"))
casos = yaml.safe_load((BASE / "casos_teste_v1.yaml").read_text(encoding="utf-8"))["casos"]
motor = Motor(BASE / "regras_classificacao_v1.yaml", BASE / "tratamento_tributario_v1.yaml")


def tabela(ws, cab, linhas, larguras, linha_ini=1, revisao=False):
    """Escreve cabeçalho + linhas com estilo. Se revisao=True, acrescenta colunas de revisão."""
    if revisao:
        cab = cab + ["Status da revisão", "Revisor", "Data", "Comentário do revisor"]
        larguras = larguras + [16, 14, 11, 34]
        linhas = [l + ["Pendente", "", "", ""] for l in linhas]
    for j, h in enumerate(cab, 1):
        c = ws.cell(row=linha_ini, column=j, value=h)
        c.font, c.fill, c.alignment, c.border = H_FONT, H_FILL, Alignment(wrap_text=True, vertical="center"), BORDER
    for i, linha in enumerate(linhas, linha_ini + 1):
        for j, v in enumerate(linha, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.font, c.alignment, c.border = BODY, WRAP, BORDER
            if (i - linha_ini) % 2 == 0:
                c.fill = ZEBRA
        if revisao:
            for j in range(len(cab) - 3, len(cab) + 1):
                c = ws.cell(row=i, column=j)
                c.fill, c.font = INPUT_FILL, INPUT_FONT
    for j, w in enumerate(larguras, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=linha_ini + 1, column=2)
    ultima = linha_ini + len(linhas)
    ws.auto_filter.ref = f"A{linha_ini}:{get_column_letter(len(cab))}{ultima}"
    if revisao:
        dv = DataValidation(type="list", formula1='"Pendente,Aprovado,Ajustar,Rejeitado"', allow_blank=True)
        col = get_column_letter(len(cab) - 3)
        dv.add(f"{col}{linha_ini + 1}:{col}{ultima}")
        ws.add_data_validation(dv)
    return ultima


def pintar_efeito(ws, col, l_ini, l_fim):
    for r in range(l_ini, l_fim + 1):
        c = ws.cell(row=r, column=col)
        if c.value in EFEITO_FILL:
            c.fill = PatternFill("solid", start_color=EFEITO_FILL[c.value])


wb = Workbook()

# ---------------- Leia-me ----------------
ws = wb.active
ws.title = "Leia-me"
ws["A1"] = "IAG 5.0 — Tabela de tratamento tributário e regras de classificação (v1)"
ws["A1"].font = TITLE
linhas = [
    ("Situação", "RASCUNHO TÉCNICO. Nenhuma linha deve ser usada em relatório de cliente antes de revisada por especialista tributário."),
    ("Base legal", "LC 214/2025, texto consolidado até a LC 235/2026 (27/08/2026). Anexo IX conforme publicação do Senado Federal. "
                   "O Anexo IX é revisado a cada 120 dias (art. 138, § 10): conferir atos de revisão posteriores."),
    ("Fonte de verdade", "Os arquivos YAML (regras_classificacao_v1.yaml e tratamento_tributario_v1.yaml). Esta planilha é a cópia para revisão. "
                         "Ajustes aprovados aqui devem ser lançados no YAML, com nova versão."),
    ("Como revisar", "Em cada aba com colunas amarelas, preencha Status (Aprovado / Ajustar / Rejeitado), Revisor, Data e Comentário. "
                     "Células amarelas com texto azul são as únicas a editar."),
    ("Ponto crítico", "Insumos do Anexo IX vendidos por fornecedor do regime regular a produtor contribuinte têm recolhimento DIFERIDO "
                      "(art. 138, § 2º, I, 'a'). O produtor NÃO toma crédito sobre eles — e o preço também não carrega IBS/CBS. "
                      "O simulador não pode tratar adubo, semente, defensivo e ração como gerador de crédito."),
    ("Aba Tratamentos", "Catálogo dos efeitos que o simulador usa (T00 a T07)."),
    ("Aba Entradas", "NCM x categoria -> tratamento, para as COMPRAS do produtor."),
    ("Aba Saídas", "Tratamento das VENDAS do produtor (cálculo do débito). Todas as linhas a validar."),
    ("Aba Regras", "Regras de classificação por camada: 1 Operação (CFOP), 2 Vedações, 3 NCM, 4 Fornecedor (CNAE), 5 Descrição."),
    ("Aba Casos de teste", "34 itens típicos processados pelo motor, com o crédito estimado a partir da alíquota de referência (célula amarela)."),
]
for i, (k, v) in enumerate(linhas, 3):
    ws.cell(row=i, column=1, value=k).font = BOLD
    c = ws.cell(row=i, column=2, value=v)
    c.font, c.alignment = BODY, WRAP
r = len(linhas) + 4
ws.cell(row=r, column=1, value="Legenda de efeitos no crédito").font = BOLD
legenda = {
    "CREDITO_INTEGRAL": "Crédito do imposto incidente (com redução, se houver)",
    "SEM_CREDITO_DIFERIDO": "Anexo IX com diferimento: sem imposto destacado e sem crédito",
    "CREDITO_AD_REM": "Combustível monofásico: crédito em R$ por unidade (parâmetro)",
    "CREDITO_SIMPLES": "Fornecedor do Simples: crédito limitado ao recolhido no Simples",
    "CREDITO_PRESUMIDO": "Fornecedor produtor não contribuinte: crédito presumido (art. 168)",
    "VEDADO": "Uso ou consumo pessoal (art. 57)",
    "NEUTRO": "Sem aquisição onerosa (remessa, comodato, entrega futura etc.)",
    "REVISAR": "Depende de destinação ou vínculo: revisão humana obrigatória",
}
for i, (k, v) in enumerate(legenda.items(), r + 1):
    c = ws.cell(row=i, column=1, value=k)
    c.font, c.fill = BOLD, PatternFill("solid", start_color=EFEITO_FILL[k])
    ws.cell(row=i, column=2, value=v).font = BODY
ws.column_dimensions["A"].width = 24
ws.column_dimensions["B"].width = 120

# ---------------- Tratamentos ----------------
ws = wb.create_sheet("Tratamentos")
lin = []
for cod, t in trat["tratamentos"].items():
    red = t.get("reducao")
    lin.append([cod, t["nome"], red if red is not None else "n/a",
                {True: "Sim", False: "Não", None: "Depende"}[t.get("diferimento")],
                t["efeito_credito"], t["fundamento"], t.get("cclasstrib_ref", ""),
                " ".join(str(t.get("nota", "")).split())])
ult = tabela(ws, ["Código", "Tratamento", "Redução", "Diferimento", "Efeito no crédito", "Fundamento", "cClassTrib (referência)", "Nota"],
             lin, [8, 40, 9, 11, 22, 30, 22, 70], revisao=True)
for rr in range(2, ult + 1):
    if isinstance(ws.cell(row=rr, column=3).value, float):
        ws.cell(row=rr, column=3).number_format = "0%"
pintar_efeito(ws, 5, 2, ult)

# ---------------- Entradas ----------------
ws = wb.create_sheet("Entradas (compras)")
lin = []
for m in trat["mapa_entradas"]:
    t = trat["tratamentos"][m["tratamento"]]
    lin.append([m["id"], ", ".join(m["ncm"]) or "(qualquer)", ", ".join(m["categorias"]) or "(qualquer)",
                m["descricao"], m["anexo_item"], m["tratamento"], t["nome"], t["efeito_credito"], t["fundamento"]])
for cat, cod in trat["mapa_por_categoria"].items():
    t = trat["tratamentos"][cod]
    lin.append([f"CAT-{cat}", "(qualquer)", cat, f"Regra por categoria: {regras['taxonomia'].get(cat, cat)}",
                "—", cod, t["nome"], t["efeito_credito"], t["fundamento"]])
ult = tabela(ws, ["ID", "NCM (prefixos)", "Categorias", "Descrição", "Anexo / item", "Trat.", "Tratamento", "Efeito no crédito", "Fundamento"],
             lin, [10, 26, 16, 46, 14, 7, 36, 22, 30], revisao=True)
pintar_efeito(ws, 8, 2, ult)
ws.cell(row=2, column=2).comment = Comment("Prefixo de NCM: '31' cobre todo o capítulo 31; '3105' só a posição 31.05. O motor usa o prefixo mais longo que casar com a categoria do item.", "IAG 5.0")

# ---------------- Saídas ----------------
ws = wb.create_sheet("Saídas (vendas)")
lin = [[s["id"], s["produto"], s["ncm"], s["tratamento"], s["efeito_debito"], s["fundamento"], s["status"]] for s in trat["tratamento_saidas"]]
tabela(ws, ["ID", "Produto vendido", "NCM", "Tratamento", "Efeito no débito", "Fundamento", "Situação"],
       lin, [8, 46, 14, 30, 34, 34, 11], revisao=True)

# ---------------- Taxonomia ----------------
ws = wb.create_sheet("Taxonomia")
lin = [[k, v] for k, v in regras["taxonomia"].items()]
tabela(ws, ["Código", "Categoria"], lin, [14, 60], revisao=True)

# ---------------- Regras ----------------
ws = wb.create_sheet("Regras v1")
NOMES_CAMADA = {1: "1 Operação", 2: "2 Vedação", 3: "3 NCM", 4: "4 Fornecedor", 5: "5 Descrição"}
lin = []
for rg in regras["regras"]:
    c = rg.get("condicoes", {})
    partes = []
    if "cfop_em" in c: partes.append("CFOP em " + ", ".join(c["cfop_em"]))
    if "ncm_prefixos" in c: partes.append("NCM inicia com " + ", ".join(c["ncm_prefixos"]))
    if "cnae_emitente_em" in c: partes.append("CNAE emitente em " + ", ".join(c["cnae_emitente_em"]))
    if "desc_regex" in c: partes.append("descrição contém " + c["desc_regex"])
    if "desc_nao_regex" in c: partes.append("descrição NÃO contém " + c["desc_nao_regex"])
    res = rg["resultado"]
    lin.append([rg["id"], NOMES_CAMADA[rg["camada"]], "\n".join(partes), res["categoria"], res["confianca"],
                res.get("alerta", ""), " ".join(str(rg.get("explicacao", "")).split())])
ult = tabela(ws, ["ID da regra", "Camada", "Condições", "Categoria", "Confiança", "Alerta", "Explicação"],
             lin, [30, 13, 52, 12, 10, 26, 60], revisao=True)
for rr in range(2, ult + 1):
    ws.cell(row=rr, column=5).number_format = "0%"

# ---------------- Casos de teste ----------------
ws = wb.create_sheet("Casos de teste")
ws["A1"] = "Alíquota de referência IBS+CBS (cenário)"
ws["A1"].font = BOLD
ws["D1"] = 0.27
ws["D1"].font, ws["D1"].fill, ws["D1"].number_format = INPUT_FONT, INPUT_FILL, "0.0%"
ws["D1"].comment = Comment("Premissa editável. 27% é o cenário base usado no artigo do IAG 5.0; a alíquota definitiva ainda depende de calibragem.", "IAG 5.0")
ws["E1"] = "← premissa editável (cenário base do artigo)"
ws["E1"].font = Font(name=F, size=9, italic=True)
cab = ["Caso", "Descrição na NF-e", "NCM", "CFOP", "Fornecedor", "Valor (R$)", "Categoria", "Regra aplicada", "Trat.",
       "Efeito no crédito", "Redução", "Confiança", "Alertas", "Revisar?", "Teste", "Crédito estimado (R$)"]
lin = []
for caso in casos:
    it, res = caso["item"], motor.classificar(caso["item"])
    esp = caso["esperado"]
    ok = (res.categoria == esp["categoria"] and res.tratamento == esp["tratamento"]
          and res.efeito_credito == esp["efeito_credito"] and all(a in res.alertas for a in esp.get("alertas", [])))
    forn = "Simples" if str(it.get("crt_emitente", "")) in ("1", "2", "4") else ("Pessoa física" if it.get("doc_emitente") else "Regime regular")
    lin.append([caso["id"], it["descricao"], it.get("ncm") or "(vazio)", it["cfop"], forn, it["valor"], res.categoria,
                res.regra, res.tratamento, res.efeito_credito, res.reducao if res.reducao is not None else 0,
                res.confianca, ", ".join(res.alertas), "Sim" if res.revisar else "Não", "OK" if ok else "FALHA", None])
L0 = 3
ult = tabela(ws, cab, lin, [7, 34, 12, 7, 14, 13, 12, 28, 6, 22, 9, 10, 34, 9, 7, 18], linha_ini=L0)
for rr in range(L0 + 1, ult + 1):
    ws.cell(row=rr, column=6).number_format = '#,##0;(#,##0);"-"'
    ws.cell(row=rr, column=11).number_format = "0%"
    ws.cell(row=rr, column=12).number_format = "0%"
    c = ws.cell(row=rr, column=16)
    c.value = (f'=IF(J{rr}="CREDITO_INTEGRAL",F{rr}*$D$1*(1-K{rr}),'
               f'IF(OR(J{rr}="SEM_CREDITO_DIFERIDO",J{rr}="VEDADO",J{rr}="NEUTRO"),0,"parâmetro/revisão"))')
    c.number_format = '#,##0;(#,##0);"-"'
    c.font = BODY
pintar_efeito(ws, 10, L0 + 1, ult)

# Resumo com fórmulas
R = ult + 2
resumo = [
    ("Valor total das compras (R$)", f"=SUM(F{L0+1}:F{ult})"),
    ("Crédito estimado — itens com crédito integral (R$)", f"=SUM(P{L0+1}:P{ult})"),
    ("Valor de insumos do Anexo IX com diferimento — sem crédito (R$)", f'=SUMIFS(F{L0+1}:F{ult},J{L0+1}:J{ult},"SEM_CREDITO_DIFERIDO")'),
    ("Valor que depende de parâmetro ou revisão (R$)", f'=SUMIFS(F{L0+1}:F{ult},J{L0+1}:J{ult},"REVISAR")+SUMIFS(F{L0+1}:F{ult},J{L0+1}:J{ult},"CREDITO_AD_REM")+SUMIFS(F{L0+1}:F{ult},J{L0+1}:J{ult},"CREDITO_SIMPLES")+SUMIFS(F{L0+1}:F{ult},J{L0+1}:J{ult},"CREDITO_PRESUMIDO")'),
    ("Crédito que seria estimado se o diferimento fosse ignorado (R$)", f"=P{R+2}+P{R+3}*$D$1*0.4"),
    ("Itens marcados para revisão", f'=COUNTIF(N{L0+1}:N{ult},"Sim")'),
    ("Casos de teste aprovados", f'=COUNTIF(O{L0+1}:O{ult},"OK")&" de "&COUNTA(A{L0+1}:A{ult})'),
]
ws.cell(row=R, column=2, value="Resumo").font = BOLD
for i, (rot, form) in enumerate(resumo, R + 1):
    ws.cell(row=i, column=2, value=rot).font = BODY
    c = ws.cell(row=i, column=16, value=form)
    c.font = BOLD
    c.number_format = '#,##0;(#,##0);"-"'
ws.cell(row=R + 5, column=2).comment = Comment(
    "Mostra o erro que o simulador cometeria ao dar crédito sobre insumos do Anexo IX: valor diferido x alíquota x 40% (redução de 60%).", "IAG 5.0")

SAIDA.parent.mkdir(parents=True, exist_ok=True)
wb.save(SAIDA)
print("Salvo em", SAIDA)
