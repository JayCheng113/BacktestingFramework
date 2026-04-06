"""Regression: close_resources must run even if aclose() raises."""
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

import ez.api.app as app_module


@pytest.mark.asyncio
async def test_close_resources_called_even_if_aclose_raises():
    """If LLM provider.aclose() throws, close_resources() must still execute."""
    mock_provider = AsyncMock()
    mock_provider.aclose.side_effect = RuntimeError("network error")

    mock_close = MagicMock()

    with patch.object(app_module, "close_resources", mock_close), \
         patch.object(app_module, "get_tushare_provider", return_value=None), \
         patch.object(app_module, "load_all_strategies"), \
         patch.object(app_module, "load_user_factors"), \
         patch("ez.portfolio.loader.load_portfolio_strategies"), \
         patch("ez.portfolio.loader.load_cross_factors"), \
         patch("ez.portfolio.loader.load_ml_alphas"), \
         patch("ez.llm.factory.get_cached_provider", return_value=mock_provider):

        try:
            async with app_module.lifespan(app_module.app):
                pass
        except RuntimeError:
            pass

        mock_close.assert_called_once()
