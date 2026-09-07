def test_import_package():
    """The package imports and reports its version."""
    import marketdata
    assert marketdata.__version__ == "0.2.0"


def test_public_api_exports():
    """The public names (client, symbol, types, ports, defaults) are importable from the package root."""
    from marketdata import (
        MarketData, Symbol, Market, Quote,
        SourceConfig, StaticConfigProvider, InMemoryMetricsSink,
        ConfigProvider, MetricsSink,
    )
    assert MarketData is not None and Symbol is not None and Quote is not None
    assert SourceConfig is not None and StaticConfigProvider is not None
