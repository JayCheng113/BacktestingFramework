from datetime import datetime
from ez.types import Bar
from ez.data.validator import DataValidator, ValidationResult


def _bar(**overrides) -> Bar:
    defaults = dict(
        time=datetime(2024, 1, 2), symbol="TEST", market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000,
    )
    defaults.update(overrides)
    return Bar(**defaults)


def test_valid_bar_passes():
    result = DataValidator.validate_bars([_bar()])
    assert result.valid_count == 1
    assert result.invalid_count == 0


def test_ohlc_consistency_fails():
    result = DataValidator.validate_bars([_bar(low=11.0, high=9.0)])
    assert result.invalid_count == 1
    assert "ohlc" in result.errors[0].lower()


def test_negative_volume_fails():
    result = DataValidator.validate_bars([_bar(volume=-100)])
    assert result.invalid_count == 1


def test_mixed_valid_invalid():
    bars = [_bar(), _bar(low=999.0, high=1.0), _bar()]
    result = DataValidator.validate_bars(bars)
    assert result.valid_count == 2
    assert result.invalid_count == 1


def test_negative_price_fails():
    """Negative prices must be rejected by validator."""
    result = DataValidator.validate_bars([_bar(close=-5.0)])
    assert result.invalid_count == 1
    assert any("negative" in e.lower() for e in result.errors)

    result2 = DataValidator.validate_bars([_bar(open=-1.0)])
    assert result2.invalid_count == 1

    result3 = DataValidator.validate_bars([_bar(high=-0.5)])
    assert result3.invalid_count == 1

    result4 = DataValidator.validate_bars([_bar(low=-2.0)])
    assert result4.invalid_count == 1
