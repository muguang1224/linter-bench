from app.logging_util import log_token

def test_log_token_exists():
    assert callable(log_token)
