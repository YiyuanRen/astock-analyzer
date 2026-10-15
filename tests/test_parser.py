import pytest

from app.commands.parser import parse
from app.data.fetcher import _secid
from app.data.market import decimals_of, display_code, market_of, parse_code


@pytest.mark.parametrize("text,kind,code", [
    ("Q 000001", "Q", "000001"),
    ("b 600000", "B", "600000"),
    ("C 000001", "C", "000001"),
    ("  Q   000001  ", "Q", "000001"),
    # 港股与带市场前后缀写法
    ("Q 00700", "Q", "00700"),
    ("Q 00700.HK", "Q", "00700"),
    ("B [01810.HK](http://01810.hk/)", "B", "01810"),
    ("S [01810.HK](http://01810.hk/) @25.5 200", "S", "01810"),
    ("Q http://01810.hk/", "Q", "01810"),
    ("Q hk00700", "Q", "00700"),
    ("B HK700", "B", "00700"),
    ("C 700.hk", "C", "00700"),
    ("Q SH600519", "Q", "600519"),
    ("Q 600519.SH", "Q", "600519"),
    ("B SZ000001", "B", "000001"),
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
    c_hk = parse("S 00700.HK @415.5 200")
    assert (c_hk.code, c_hk.buy_price, c_hk.quantity) == ("00700", 415.5, 200)


def test_l_and_m():
    assert parse("L").kind == "L"
    assert parse("M").kind == "M" and parse("M").provider is None
    c = parse("M deepseek deepseek-chat")
    assert (c.provider, c.model) == ("deepseek", "deepseek-chat")


@pytest.mark.parametrize("text", ["你好", "Q 700", "Q 1234", "Q 1234567", "Q000001", "", None, "X 000001"])
def test_invalid_goes_help(text):
    assert parse(text).kind == "HELP"


@pytest.mark.parametrize("code,secid", [
    ("000001", "0.000001"), ("300750", "0.300750"),
    ("600519", "1.600519"), ("688981", "1.688981"),
    ("510300", "1.510300"),  # 沪市ETF
    ("159915", "0.159915"),  # 深市ETF
    ("00700", "116.00700"),  # 港股
    ("09988", "116.09988"),
])
def test_secid(code, secid):
    assert _secid(code) == secid


def test_market_helpers():
    assert parse_code("HK5") == ("00005", "HK")
    assert market_of("00700") == "HK" and market_of("000001") == "CN"
    assert display_code("00700") == "00700.HK" and display_code("000001") == "000001"
    assert decimals_of("00700") == 3 and decimals_of("000001") == 2
