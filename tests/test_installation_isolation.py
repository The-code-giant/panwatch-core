"""Regression coverage for import-time isolation from installation paths."""
import os
from pathlib import Path

from src.config import Settings
from src.web.database import DB_PATH


# Deliberately checked during collection, before any autouse fixture can repair
# a configuration or database that was imported with installation paths.
_data_dir = Path(os.environ["DATA_DIR"])
assert _data_dir.name.startswith("panwatch-pytest-")
assert Path(DB_PATH).resolve().parent == _data_dir.resolve()
assert Path(Settings.model_config["env_file"]).parent == _data_dir
assert not Path(Settings.model_config["env_file"]).exists()
assert not any(name.upper().startswith(("AUTH_", "AI_", "NOTIFY_", "OPENAI_"))
               or name.upper() in {"JWT_SECRET", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
               for name in os.environ)


def test_configuration_ignores_cwd_dotenv(tmp_path, monkeypatch):
    """测试配置不读取当前目录的模拟环境文件。"""
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("AI_MODEL=synthetic-dotenv-sentinel\n")
    assert Settings().ai_model != "synthetic-dotenv-sentinel"


def test_database_is_disposable_at_import():
    """测试导入时数据库已经指向独立临时目录。"""
    assert Path(DB_PATH).parent == _data_dir
    assert os.environ["DISABLE_SCHEDULERS"] == "1"
    assert os.environ["UPDATE_CHECK_DISABLE"] == "1"


def test_inherited_credentials_and_proxy_are_not_configuration():
    """测试继承的安装凭据和代理不会进入配置。"""
    settings = Settings()
    assert settings.ai_api_key == ""
    assert settings.notify_telegram_bot_token == ""
    assert settings.http_proxy == ""


def test_unconfigured_install_has_no_default_ai_endpoint():
    """测试未配置的安装没有默认 AI 端点，不会把提示词发往任何第三方。"""
    settings = Settings()
    assert settings.ai_base_url == ""
    assert settings.ai_model == ""
