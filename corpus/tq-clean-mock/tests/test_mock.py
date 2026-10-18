from unittest import mock

def test_called():
    m = mock.Mock()
    m()
    m.assert_called_once()
