# -*- coding: utf-8 -*-
"""
db_config.py — 数据库连接配置（凭据统一从 scripts/.env 读取，仓库零明文密码）
============================================================================
各脚本不再硬编码 MySQL 账号密码。scripts/.env 已被 .gitignore，不会进入仓库：
  - 本地开发：在 scripts/.env 配置 BEAUTY_DB_URL（SQLAlchemy 场景）或
    BEAUTY_DB_USER / BEAUTY_DB_PWD / BEAUTY_DB_HOST / BEAUTY_DB_PORT / BEAUTY_DB_NAME（pymysql 场景）。
  - 未配置时返回占位符（连接会失败并提示配置），绝不回落明文密码。
"""
import os
from pathlib import Path

_ENV = Path(__file__).resolve().parent / ".env"


def load_env():
    """把 scripts/.env 的 KEY=VALUE 读进环境变量（已存在则不改写）。"""
    if _ENV.exists():
        for line in _ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def db_url():
    """SQLAlchemy 连接 URL（create_engine(db_url())）。"""
    load_env()
    return os.environ.get(
        "BEAUTY_DB_URL",
        "mysql+pymysql://root:CHANGE_ME@127.0.0.1:3306/beauty_agent?charset=utf8mb4",
    )


def db_params():
    """pymysql.connect(**db_params()) 参数 dict。"""
    load_env()
    return {
        "user": os.environ.get("BEAUTY_DB_USER", "root"),
        "password": os.environ.get("BEAUTY_DB_PWD", "CHANGE_ME"),
        "host": os.environ.get("BEAUTY_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("BEAUTY_DB_PORT", "3306")),
        "database": os.environ.get("BEAUTY_DB_NAME", "beauty_agent"),
        "charset": os.environ.get("BEAUTY_DB_CHARSET", "utf8mb4"),
    }
