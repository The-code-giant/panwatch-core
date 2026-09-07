import unittest

from src.core.cn_symbol import get_cn_exchange, get_cn_prefix, is_cn_sh
from src.models.market import MarketCode


class TestCnSymbolMapping(unittest.TestCase):
    def test_cn_exchange_core(self):
        """交易所识别 — SZ/SH/BJ 判断"""
        self.assertEqual(get_cn_exchange("000738"), "SZ")
        self.assertEqual(get_cn_exchange("600519"), "SH")
        self.assertEqual(get_cn_exchange("300750"), "SZ")
        self.assertEqual(get_cn_exchange("510300"), "SH")
        self.assertEqual(get_cn_exchange("900901"), "SH")
        self.assertEqual(get_cn_exchange("920001"), "BJ")

    def test_cn_prefix_core(self):
        """代码前缀 — 小写/大写前缀"""
        self.assertEqual(get_cn_prefix("000738"), "sz")
        self.assertEqual(get_cn_prefix("600519"), "sh")
        self.assertEqual(get_cn_prefix("920001"), "bj")
        self.assertEqual(get_cn_prefix("000738", upper=True), "SZ")
        self.assertTrue(is_cn_sh("600519"))
        self.assertFalse(is_cn_sh("000738"))

    # 雪球新闻 symbol 前缀映射测试已随 XueqiuNewsCollector 收口进 marketdata 包，
    # 对应用例见 packages/marketdata/tests/test_news.py::test_xueqiu_symbol_id_prefix_rules。


if __name__ == "__main__":
    unittest.main()
