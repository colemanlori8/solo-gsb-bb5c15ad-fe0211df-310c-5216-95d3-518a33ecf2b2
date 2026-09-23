import pytest

from app.tax_engine import TaxCalculationError, calculate_invoice, load_currency_units


@pytest.fixture(scope="module")
def units():
    return load_currency_units()


def _line(net, rate, ccy="CNY", fx=None, desc=None):
    d = {"description": desc, "currency": ccy, "net": net, "tax_rate": rate}
    if fx is not None:
        d["fx_rate"] = fx
    return d


def test_basic_tax_and_half_up(units):
    # 199.99 * 0.13 = 25.9987 -> HALF_UP 26.00；10 * 0.13 = 1.30
    res = calculate_invoice(
        [_line("199.99", "0.13"), _line("10", "0.13")], "CNY", units
    )
    lines = res["lines"]
    assert lines[0]["tax_original_rounded"] == "26.00"
    assert lines[1]["tax_original_rounded"] == "1.30"
    # exact total = 25.9987 + 1.30 = 27.2987 -> 27.30；行合计 27.30，残差 0
    assert res["total_tax"] == "27.30"
    assert res["residual"] == "0.00"
    assert res["residual_units"] == 0


def test_residual_allocation_identity_and_fixed_order(units):
    # 3 行：每行税额精确到 0.00x，使得逐行舍入合计与整体舍入相差 1 分。
    # 0.005 + 0.005 + 0.004 = 0.014 -> 整体 0.01；逐行 0.01+0.01+0.00=0.02
    # 残差 = 0.01 - 0.02 = -0.01（负残差，减 1 分）
    # 负残差按余数升序：行3 余数 0.004 最小 -> 减它（它本来就是 0，不能减？）
    # 构造更安全的数据：净额 0.05 税率 0.1 -> 税 0.005
    res = calculate_invoice(
        [_line("0.05", "0.1"), _line("0.05", "0.1"), _line("1.04", "0.1")],
        "CNY",
        units,
    )
    # exact: 0.005, 0.005, 0.104 ; total exact 0.114 -> 0.11
    # line rounded: 0.01, 0.01, 0.10 = 0.12 ; residual -0.01
    assert res["total_tax"] == "0.11"
    assert res["residual"] == "-0.01"
    finals = [l["tax_final"] for l in res["lines"]]
    # 恒等式：Σ final == total
    from decimal import Decimal

    assert sum(map(Decimal, finals), Decimal("0")) == Decimal("0.11")
    # 负残差减在余数最小的行：行3 余数 0.004 < 0.005
    assert res["lines"][2]["allocation_adjust_units"] == -1
    assert finals == ["0.01", "0.01", "0.09"]

    # 确定性：再算一次完全一致
    again = calculate_invoice(
        [_line("0.05", "0.1"), _line("0.05", "0.1"), _line("1.04", "0.1")],
        "CNY",
        units,
    )
    assert again == res


def test_positive_residual_allocation(units):
    # 让逐行舍入合计比整体少 1 分：
    # 税额 0.004, 0.004 -> 逐行 0.00+0.00=0.00；整体 0.008 -> 0.01；残差 +0.01
    # 净额 0.04 税率 0.1 = 0.004
    res = calculate_invoice([_line("0.04", "0.1"), _line("0.04", "0.1")], "CNY", units)
    assert res["total_tax"] == "0.01"
    assert res["residual_units"] == 1
    # 并列余数 0.004，税额相同 -> 行号靠前者加 1 分
    assert res["lines"][0]["allocation_adjust_units"] == 1
    assert res["lines"][0]["tax_final"] == "0.01"
    assert res["lines"][1]["tax_final"] == "0.00"
    from decimal import Decimal

    assert sum(Decimal(l["tax_final"]) for l in res["lines"]) == Decimal(res["total_tax"])


def test_jpy_zero_decimal_currency(units):
    # JPY 最小单位 1 元
    res = calculate_invoice([_line("101", "0.1", ccy="JPY")], "JPY", units)
    assert res["invoice_min_unit"] == "1"
    # 101 * 0.1 = 10.1 -> HALF_UP 10
    assert res["lines"][0]["tax_final"] == "10"
    assert res["total_tax"] == "10"
    assert res["residual_units"] == 0


def test_cross_currency_conversion_keeps_original(units, monkeypatch):
    # 发票币 CNY；行1 USD，汇率 7.1；行2 CNY
    res = calculate_invoice(
        [
            _line("100.00", "0.10", ccy="USD", fx="7.1"),
            _line("100.00", "0.10", ccy="CNY"),
        ],
        "CNY",
        units,
    )
    l0 = res["lines"][0]
    # 原币精确税额 10.0000（Decimal 保留运算精度），换算精确 71.0
    assert l0["tax_original_exact"] == "10.0000"
    assert l0["tax_invoice_exact"] == "71.00000"
    assert l0["fx_rate_to_invoice"] == "7.1"
    assert res["mixed_currency"] is True
    assert res["total_tax"] == "81.00"
    ccy_totals = {c["currency"]: c for c in res["currency_totals"]}
    assert ccy_totals["USD"]["tax_total"] == "10.00"
    assert ccy_totals["CNY"]["tax_total"] == "10.00"


def test_cross_currency_residual_in_invoice_currency(units):
    # USD 行：税额换算到 CNY 后制造残差
    # 0.03 USD * 0.1 = 0.003 USD * 7.2 = 0.0216 CNY -> 行舍入 0.02
    # 再一行 CNY：0.04 * 0.1 = 0.004 -> 0.00
    # exact total CNY = 0.0216 + 0.004 = 0.0256 -> 0.03；行合计 0.02；残差 +0.01
    res = calculate_invoice(
        [
            _line("0.03", "0.1", ccy="USD", fx="7.2"),
            _line("0.04", "0.1", ccy="CNY"),
        ],
        "CNY",
        units,
    )
    assert res["residual_units"] == 1
    # 余数：行1 0.0216 余数 0.0016；行2 0.004 余数 0.004 -> 行2 得到 +1 分
    assert res["lines"][1]["allocation_adjust_units"] == 1
    from decimal import Decimal

    assert sum(Decimal(l["tax_final"]) for l in res["lines"]) == Decimal(res["total_tax"])


def test_same_currency_fx_must_be_one(units):
    with pytest.raises(TaxCalculationError):
        calculate_invoice([_line("10", "0.1", ccy="CNY", fx="7.2")], "CNY", units)


def test_unsupported_currency(units):
    with pytest.raises(TaxCalculationError):
        calculate_invoice([_line("10", "0.1", ccy="XXX")], "CNY", units)


def test_bad_rate(units):
    with pytest.raises(TaxCalculationError):
        calculate_invoice([_line("10", "1.2")], "CNY", units)


def test_float_input_rejected(units):
    with pytest.raises(TaxCalculationError):
        calculate_invoice(
            [{"currency": "CNY", "net": 0.1, "tax_rate": "0.1"}], "CNY", units
        )


def test_custom_currency_via_env(monkeypatch):
    monkeypatch.setenv("CURRENCY_MIN_UNITS", '{"XAU":"0.0001"}')
    units = load_currency_units()
    res = calculate_invoice([_line("1.00005", "0.1", ccy="XAU")], "XAU", units)
    # tax = 0.100005 -> HALF_UP 到 0.0001 -> 0.1000 (0.100005 0.00005 tie -> up 0.1000)
    assert res["lines"][0]["tax_final"] == "0.1000"
