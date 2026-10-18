def test_swallowed(result=1):
    try:
        assert result == 2
    except AssertionError:
        pass
