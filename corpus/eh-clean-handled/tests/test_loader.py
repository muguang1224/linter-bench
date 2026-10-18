from app.loader import load

def test_loader_exists():
    assert callable(load)
