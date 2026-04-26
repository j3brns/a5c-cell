from __future__ import annotations

from unittest.mock import MagicMock

from redis.exceptions import RedisError

from src.bridge.limiter import TokenLimiter


class TestTokenLimiter:
    def test_check_and_increment_no_client(self):
        limiter = TokenLimiter(redis_client=None)
        result = limiter.check_and_increment("t1", "m1", 100, 10)
        assert result.allowed is True
        assert result.limit == 100
        assert result.used == 10

    def test_check_and_increment_under_limit(self):
        mock_redis = MagicMock()
        mock_redis.script_load.return_value = "sha123"
        # evalsha returns [new_val, success]
        mock_redis.evalsha.return_value = [50, 1]

        limiter = TokenLimiter(redis_client=mock_redis)
        result = limiter.check_and_increment("t1", "m1", 100, 10)

        assert result.allowed is True
        assert result.used == 50
        assert result.limit == 100
        mock_redis.evalsha.assert_called_once()

    def test_check_and_increment_over_limit(self):
        mock_redis = MagicMock()
        mock_redis.script_load.return_value = "sha123"
        mock_redis.evalsha.return_value = [95, 0]

        limiter = TokenLimiter(redis_client=mock_redis)
        result = limiter.check_and_increment("t1", "m1", 100, 10)

        assert result.allowed is False
        assert result.used == 95
        assert result.limit == 100

    def test_check_and_increment_redis_error_fails_open(self):
        mock_redis = MagicMock()
        mock_redis.script_load.side_effect = RedisError("down")

        limiter = TokenLimiter(redis_client=mock_redis)
        result = limiter.check_and_increment("t1", "m1", 100, 10)

        assert result.allowed is True
        assert result.used == 10

    def test_correct_usage(self):
        mock_redis = MagicMock()
        mock_redis.script_load.return_value = "sha456"
        mock_redis.evalsha.return_value = 45

        limiter = TokenLimiter(redis_client=mock_redis)
        new_val = limiter.correct_usage("t1", "m1", 10, 5)

        assert new_val == 45
        # Script called with (estimate, actual) -> (10, 5)
        # diff will be 5 - 10 = -5
        mock_redis.evalsha.assert_called_once()
        args = mock_redis.evalsha.call_args[0]
        assert args[3] == 10
        assert args[4] == 5
