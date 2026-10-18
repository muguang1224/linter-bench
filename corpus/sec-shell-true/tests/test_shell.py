from app.shell import run

def test_runner_exists():
    assert callable(run)
