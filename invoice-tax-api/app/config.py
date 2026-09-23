# 支持币种的最小货币单位（以“1 个单位”表示），如 0.01 表示分。
# 可通过环境变量 CURRENCY_MIN_UNITS 覆盖 / 扩展，格式 JSON：
#   {"USD":"0.01","JPY":"1","XAU":"0.0001"}
DEFAULT_CURRENCY_MIN_UNITS = {
    "CNY": "0.01",
    "USD": "0.01",
    "EUR": "0.01",
    "HKD": "0.01",
    "JPY": "1",
    "KRW": "1",
    "AUD": "0.01",
    "GBP": "0.01",
    "SGD": "0.01",
}

# 税率允许范围（包含边界）
MIN_TAX_RATE = "0"
MAX_TAX_RATE = "1"

# 金额 / 汇率允许的最大值（防御性上限）
MAX_AMOUNT = "999999999999.999999"
