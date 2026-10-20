"""Teste de fumaça do app: abre a página, carrega os exemplos e confere os indicadores."""
from streamlit.testing.v1 import AppTest  # executa o app sem navegador

from conftest import RAIZ  # pasta principal


def test_app_com_exemplos():
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=60).run()  # abre o app
    assert not at.exception  # nenhuma exceção na abertura
    at.button[0].click().run()  # clica em "Usar notas de exemplo"
    assert not at.exception  # nenhuma exceção após carregar
    rotulos = [m.label for m in at.metric]  # nomes dos indicadores exibidos
    assert "Crédito estimado" in rotulos  # o painel foi desenhado
    assert any("Para revisar" in t.label for t in at.tabs)  # as abas foram criadas
