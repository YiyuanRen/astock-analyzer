import pytest

from app.commands.parser import parse
from app.data.fetcher import _secid


@pytest.mark.parametrize("text,kind,code", [
    ("Q 000001", "Q", "000001"),
    ("b 600000", "B", "600000"),
    ("C 000001", "C", "000001"),
    ("  Q   000001  ", "Q", "000001"),
])
def test_simple_commands(text, kind, code):
    c = parse(text)
    assert (c.kind, c.code) == (kind, code)


def test_s_variants():
    assert (parse("S 000001").buy_price, parse("S 000001").quantity) == (None, None)
    c = parse("S 000001 @10.45")
    assert (c.buy_price, c.quantity) == (10.45, None)
    c = parse("S 000001 @10.45 100")
    assert (c.buy_price, c.quantity) == (10.45, 100)
    c = parse("S 000001 ＠10.45\u3000100")  # 全角@ 与全角空格
    assert (c.buy_price, c.quantity) == (10.45, 100)


def test_l_and_m():
    assert parse("L").kind == "L"
    assert parse("M").kind == "M" and parse("M").provider is None
    c = parse("M deepseek deepseek-chat")
    assert (c.provider, c.model) == ("deepseek", "deepseek-chat")


@pytest.mark.parametrize("text", ["你好", "Q 12345", "Q000001", "", None, "X 000001"])
def test_invalid_goes_help(text):
    assert parse(text).kind == "HELP"


@pytest.mark.parametrize("code,secid", [
    ("000001", "0.000001"), ("300750", "0.300750"),
    ("600519", "1.600519"), ("688981", "1.688981"),
    ("510300", "1.510300"),  # 沪市ETF
    ("159915", "0.159915"),  # 深市ETF
])
def test_secid(code, secid):
    assert _secid(code) == secid
