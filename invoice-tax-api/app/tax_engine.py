"""税额计算核心：全部使用 Decimal，禁用 float，保证结果可复算。

规则概述
========
1. 每行净额先按**行币种**的最小货币单位做 HALF_UP 量化。
2. 行税额精确值 = 净额 × 税率（不提前舍入）。
3. 行税额按行币种最小单位 HALF_UP 舍入（满足“逐行舍入”的发票惯例，
   结果作为 ``tax_rounded`` 保留在原币金额中）。
4. 跨币种时：用**未舍入的精确行税额 × 行汇率**换算到发票币种（避免二次舍入），
   换算后的精确税额再按发票币种单位 HALF_UP。
5. 发票总税额 = Σ 换算后精确税额，再按发票币种单位 HALF_UP。
6. 残差 = 总税额(整体舍入) − Σ 逐行舍入税额，以发票币种“最小单位整数倍”表示。
   残差按**最大余数法（Hamilton 法）固定规则**分摊到行：
   - 残差 > 0：需要加 k 个最小单位。按「精确换算税额相对其向下取整的余数」
     从大到小排序，取前 k 行各 +1 单位；
   - 残差 < 0：需要减 |k| 个最小单位。按余数从小到大排序，取前 |k| 行各 -1 单位；
   - 余数相同的并列情况：税额绝对值大者优先，再相同则行号（提交顺序）靠前者优先。
   该规则完全确定，相同输入永远得到相同结果，可随时复算。
7. 恒等式：分摊后 Σ 各行最终税额 == 整体舍入总税额，残差为 0。
"""
from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal, localcontext, getcontext
from typing import Any

getcontext().prec = 50

ZERO = Decimal("0")
ONE = Decimal("1")


class TaxCalculationError(ValueError):
    """可预期的入参/配置错误，message 可直接返回给客户端。"""


def load_currency_units() -> dict[str, Decimal]:
    """读取币种最小单位配置（默认表 + CURRENCY_MIN_UNITS JSON 覆盖）。"""
    from .config import DEFAULT_CURRENCY_MIN_UNITS

    units: dict[str, Decimal] = {}
    for code, unit in DEFAULT_CURRENCY_MIN_UNITS.items():
        units[code.upper()] = Decimal(unit)

    raw = os.getenv("CURRENCY_MIN_UNITS", "").strip()
    if raw:
        try:
            overrides = json.loads(raw)
            if not isinstance(overrides, dict):
                raise ValueError
            for code, unit in overrides.items():
                units[str(code).upper()] = Decimal(str(unit))
        except Exception as exc:  # noqa: BLE001
            raise TaxCalculationError(
                "环境变量 CURRENCY_MIN_UNITS 必须是 JSON 对象，"
                '例如 {"USD":"0.01","JPY":"1"}'
            ) from exc

    for code, unit in units.items():
        if not unit.is_finite() or unit <= 0:
            raise TaxCalculationError(f"币种 {code} 的最小单位必须为正数")
    return units


def quantize(value: Decimal, unit: Decimal) -> Decimal:
    """按最小单位 HALF_UP 量化。"""
    return value.quantize(unit, rounding=ROUND_HALF_UP)


def parse_decimal(raw: Any, field: str) -> Decimal:
    """把字符串/int 安全解析为有限正 Decimal（禁止 float、NaN、Infinity）。"""
    if isinstance(raw, bool):  # bool 是 int 的子类，显式拒绝
        raise TaxCalculationError(f"{field} 必须是十进制数字字符串")
    if isinstance(raw, float):
        raise TaxCalculationError(f"{field} 不允许使用 float，请改为字符串")
    try:
        value = Decimal(str(raw))
    except Exception as exc:  # noqa: BLE001
        raise TaxCalculationError(f"{field} 必须是十进制数字字符串") from exc
    if not value.is_finite():
        raise TaxCalculationError(f"{field} 必须是有限数")
    return value


def _unit_remainder(value: Decimal, unit: Decimal) -> Decimal:
    """value 相对其 floor（单位整数倍）的余数，范围 [0, unit)。"""
    units = value / unit
    floored = units.to_integral_value(rounding="ROUND_FLOOR")
    return (units - floored) * unit


def calculate_invoice(
    lines: list[dict[str, Any]],
    invoice_currency: str,
    currency_units: dict[str, Decimal],
) -> dict[str, Any]:
    """执行完整计算。

    每行输入字段：``description``、``currency``、``net``、``tax_rate``、
    ``fx_rate``（行币种 -> 发票币种，发票同币种时为 1）。
    返回可 JSON 序列化（金额全部为字符串）的结构化结果。
    """
    inv_ccy = invoice_currency.upper()
    if inv_ccy not in currency_units:
        raise TaxCalculationError(f"不支持的发票币种: {inv_ccy}")
    inv_unit = currency_units[inv_ccy]

    if not lines:
        raise TaxCalculationError("至少需要一行发票行")

    computed: list[dict[str, Any]] = []

    with localcontext() as ctx:
        ctx.prec = 50

        for idx, line in enumerate(lines):
            ccy = str(line["currency"]).upper()
            if ccy not in currency_units:
                raise TaxCalculationError(f"第 {idx + 1} 行使用了不支持的币种: {ccy}")
            unit = currency_units[ccy]

            net = parse_decimal(line["net"], f"第 {idx + 1} 行净额")
            if net < 0:
                raise TaxCalculationError(f"第 {idx + 1} 行净额不能为负")

            rate = parse_decimal(line["tax_rate"], f"第 {idx + 1} 行税率")
            if rate < ZERO or rate > ONE:
                raise TaxCalculationError(f"第 {idx + 1} 行税率必须在 0 ~ 1 之间")

            fx = parse_decimal(line.get("fx_rate", "1"), f"第 {idx + 1} 行汇率")
            if fx <= ZERO:
                raise TaxCalculationError(f"第 {idx + 1} 行汇率必须为正数")
            if ccy == inv_ccy and fx != ONE:
                raise TaxCalculationError(
                    f"第 {idx + 1} 行币种与发票币种相同，汇率必须为 1"
                )

            net_q = quantize(net, unit)
            tax_exact = net_q * rate
            tax_round = quantize(tax_exact, unit)

            tax_inv_exact = tax_exact * fx
            tax_inv_round = quantize(tax_inv_exact, inv_unit)

            computed.append(
                {
                    "index": idx,
                    "description": str(line.get("description") or f"行 {idx + 1}"),
                    "currency": ccy,
                    "net": net_q,
                    "tax_rate": rate,
                    "fx_rate": fx,
                    "min_unit": unit,
                    # 原币
                    "tax_original_exact": tax_exact,
                    "tax_original_rounded": tax_round,
                    # 发票币
                    "tax_invoice_exact": tax_inv_exact,
                    "tax_invoice_rounded": tax_inv_round,
                    "tax_final": tax_inv_round,  # 分摊前初值，稍后调整
                    "allocation_adjust_units": 0,
                }
            )

        total_exact = sum((r["tax_invoice_exact"] for r in computed), ZERO)
        total_rounded = quantize(total_exact, inv_unit)
        sum_line_rounded = sum((r["tax_invoice_rounded"] for r in computed), ZERO)

        residual = total_rounded - sum_line_rounded
        # 残差以“最小单位个数”表示，残差必然是单位的整数倍
        residual_units = int((residual / inv_unit).to_integral_value(rounding="ROUND_HALF_UP"))

        if abs(residual_units) > len(computed):
            # 理论上不会发生（整体舍入与逐行舍入之差不超过行数），防御性检查
            raise TaxCalculationError(
                f"残差 {residual_units} 个单位超出行数 {len(computed)}，无法分摊"
            )

        if residual_units > 0:
            order = sorted(
                computed,
                key=lambda r: (
                    -_unit_remainder(r["tax_invoice_exact"], inv_unit),
                    -r["tax_invoice_exact"],
                    r["index"],
                ),
            )
            for r in order[:residual_units]:
                r["allocation_adjust_units"] += 1
                r["tax_final"] += inv_unit
        elif residual_units < 0:
            order = sorted(
                computed,
                key=lambda r: (
                    _unit_remainder(r["tax_invoice_exact"], inv_unit),
                    -r["tax_invoice_exact"],
                    r["index"],
                ),
            )
            for r in order[: abs(residual_units)]:
                r["allocation_adjust_units"] -= 1
                r["tax_final"] -= inv_unit

        total_final = sum((r["tax_final"] for r in computed), ZERO)
        if total_final != total_rounded:
            raise TaxCalculationError("内部分摊校验失败：分摊后总额与整体舍入额不一致")

    # 按原币种汇总（保留原币金额）
    by_currency: dict[str, dict[str, Decimal]] = {}
    for r in computed:
        bucket = by_currency.setdefault(
            r["currency"], {"net": ZERO, "tax_exact": ZERO}
        )
        bucket["net"] += r["net"]
        bucket["tax_exact"] += r["tax_original_exact"]

    currency_totals = []
    for ccy in sorted(by_currency):
        bucket = by_currency[ccy]
        unit = currency_units[ccy]
        currency_totals.append(
            {
                "currency": ccy,
                "min_unit": unit,
                "net_total": quantize(bucket["net"], unit),
                "tax_total": quantize(bucket["tax_exact"], unit),
            }
        )

    mixed = any(r["currency"] != inv_ccy for r in computed)

    line_results = [
        {
            "index": r["index"],
            "description": r["description"],
            "currency": r["currency"],
            "min_unit": _s(r["min_unit"]),
            "net": _s(r["net"]),
            "tax_rate": _s(r["tax_rate"]),
            # 原币金额（保留）
            "tax_original_exact": _s(r["tax_original_exact"]),
            "tax_original_rounded": _s(r["tax_original_rounded"]),
            # 换算
            "fx_rate_to_invoice": _s(r["fx_rate"]),
            "tax_invoice_exact": _s(r["tax_invoice_exact"]),
            "tax_invoice_rounded": _s(r["tax_invoice_rounded"]),
            # 残差分摊
            "allocation_adjust_units": r["allocation_adjust_units"],
            "allocation_adjust_amount": _s(inv_unit * r["allocation_adjust_units"]),
            "tax_final": _s(r["tax_final"]),
        }
        for r in computed
    ]

    return {
        "invoice_currency": inv_ccy,
        "invoice_min_unit": _s(inv_unit),
        "mixed_currency": mixed,
        "total_tax_exact": _s(total_exact),
        "total_tax": _s(total_rounded),
        "sum_line_rounded_tax": _s(sum_line_rounded),
        "residual": _s(residual),
        "residual_units": residual_units,
        "residual_rule": (
            "最大余数法(Hamilton)：正残差按精确税额余数降序加最小单位；"
            "负残差按余数升序减最小单位；并列时税额大者、行号靠前者优先"
        ),
        "currency_totals": [
            {
                "currency": c["currency"],
                "min_unit": _s(c["min_unit"]),
                "net_total": _s(c["net_total"]),
                "tax_total": _s(c["tax_total"]),
            }
            for c in currency_totals
        ],
        "lines": line_results,
    }


def _s(value: Decimal) -> str:
    """Decimal -> 规范字符串。"""
    return format(value, "f")
