def compute():
    return 1

def test_overbroad():
    try:
        result = compute()
    except Exception:
        result = None
    assert result == 1
