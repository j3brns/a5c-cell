from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.bridge.tpm_limiter import TokenLimiter


class TestTokenLimiter:
    def test_check_and_increment_no_client(self):
        # Point _default_counter_client to return None
        with patch("src.bridge.tpm_limiter._default_counter_client", return_value=None):
            limiter = TokenLimiter(counter_client=None)
            result = limiter.check_and_increment("t1", "m1", 100, 10)
            assert result.allowed is True
            assert result.limit == 100
            assert result.used == 10

    def test_check_and_increment_under_limit(self):
        mock_client = MagicMock()
        # check_and_increment returns (used, success)
        mock_client.check_and_increment.return_value = (50, True)

        limiter = TokenLimiter(counter_client=mock_client)
        result = limiter.check_and_increment("t1", "m1", 100, 10)

        assert result.allowed is True
        assert result.used == 50
        assert result.limit == 100
        mock_client.check_and_increment.assert_called_once()

    def test_check_and_increment_over_limit(self):
        mock_client = MagicMock()
        mock_client.check_and_increment.return_value = (95, False)

        limiter = TokenLimiter(counter_client=mock_client)
        result = limiter.check_and_increment("t1", "m1", 100, 10)

        assert result.allowed is False
        assert result.used == 95
        assert result.limit == 100

    def test_check_and_increment_error_fails_open(self):
        mock_client = MagicMock()
        mock_client.check_and_increment.side_effect = Exception("down")

        limiter = TokenLimiter(counter_client=mock_client)
        result = limiter.check_and_increment("t1", "m1", 100, 10)

        assert result.allowed is True
        assert result.used == 10

    def test_correct_usage(self):
        mock_client = MagicMock()
        mock_client.correct_usage.return_value = 45

        limiter = TokenLimiter(counter_client=mock_client)
        new_val = limiter.correct_usage("t1", "m1", 10, 5)

        assert new_val == 45
        mock_client.correct_usage.assert_called_once()
