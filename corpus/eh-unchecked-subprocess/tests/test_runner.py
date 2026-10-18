from app.runner import run

def test_runner_exists():
    assert callable(run)
