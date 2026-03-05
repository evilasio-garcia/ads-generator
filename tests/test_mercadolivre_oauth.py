import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import Settings


def test_ml_client_id_and_secret_are_configurable():
    s = Settings(ml_client_id="test_id", ml_client_secret="test_secret")
    assert s.ml_client_id == "test_id"
    assert s.ml_client_secret == "test_secret"


def test_ml_settings_default_to_empty_string():
    s = Settings()
    assert isinstance(s.ml_client_id, str)
    assert isinstance(s.ml_client_secret, str)
