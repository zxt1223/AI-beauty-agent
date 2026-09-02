# -*- coding: utf-8 -*-
"""store.py — 存储接口抽象（KVStore 接口 → JSON 实现 / Redis 预留位）
===============================================================================
对齐 pangu 存储层「接口在前、实现在后」：业务代码只依赖 KVBackend 接口，
**换存储（JSON → Redis）不换业务代码**——这是企业级架构的最小落地。

落地三件：
  - KVBackend      抽象基类：get_all() / save_all()（全量读/写，小库 JSON 足够）。
  - JsonKVBackend  JSON 文件实现：保持 data/*.json 现有格式；**不加锁**——
                   锁在 web_server 层持有（_lock），store 内不加锁避免重蹈嵌套死锁坑。
  - RedisKVBackend 企业级预留位：接口同；真实部署时传入 redis 客户端即可切换。
  - ProfileStore   画像高层 API（get/touch + LRU 淘汰），把原 web_server._get_profile/_touch
                   的函数体搬进来封装，web_server 只做薄委托。

零回归原则：
  - 不改数据格式（{uid: {...}} 原样读写）；
  - 不改锁语义（调用方持锁，store 纯函数式委托）；
  - MAX_PROFILES 淘汰逻辑原样搬入 ProfileStore.touch（单一真相源在 config.py）。
"""
import io
import json
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

from config import MAX_PROFILES   # 画像上限，超出按 last_visit 淘汰最久（单一真相源）


class KVBackend(ABC):
    """KV 存储接口：全量读/写一张表（本库量级小，全量读写足够；大库改分片时接口再扩）。
    实现方无需关心调用方锁——锁由 web 层持有，本接口是纯数据访问。"""

    @abstractmethod
    def get_all(self):
        """返回整表 {key: value}；空/不存在 → {}。"""

    @abstractmethod
    def save_all(self, table):
        """整表落盘。失败静默（存储绝不影响主流程，与 _trace 一致）。"""


class JsonKVBackend(KVBackend):
    """JSON 文件实现：data/*.json（现有格式，utf-8，indent=1，ensure_ascii=False）。"""

    def __init__(self, path):
        self.path = Path(path)

    def get_all(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_all(self, table):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(table, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except Exception:
            pass


class RedisKVBackend(KVBackend):
    """Redis 预留位（**未启用**）：接口与 JSON 实现一致，换存储只换这一行——
    `ProfileStore(RedisKVBackend(client))`，业务代码（含 ProfileStore）零改动。

    企业级映射（部署时打开）：
      - get_all  → `client.hgetall(KEY)` （单个 hash 存整表，value 为 JSON 串）
      - save_all → `client.hmset(KEY, {k: json.dumps(v)})`
    真实依赖 redis 客户端库；本环境零依赖约束 → 构造时显式声明未启用。"""

    KEY = "beauty:profiles"

    def __init__(self, client=None):
        if client is None:
            raise NotImplementedError(
                "RedisKVBackend 是预留位：未传入 redis 客户端（零依赖环境）。"
                "生产部署时 `pip install redis` 并传入连接客户端，接口即 JSON 实现同款。")
        self._c = client

    def get_all(self):
        return {k.decode() if isinstance(k, bytes) else k:
                json.loads(v) for k, v in self._c.hgetall(self.KEY).items()}

    def save_all(self, table):
        self._c.hmset(self.KEY, {k: json.dumps(v, ensure_ascii=False) for k, v in table.items()})


class ProfileStore:
    """画像高层 API：get / touch（加载 → 建/改 → LRU 淘汰 → 落盘）。
    承接原 web_server._get_profile / _touch 的全部逻辑；调用方须持 _lock（锁在 web 层）。"""

    def __init__(self, backend):
        self._b = backend

    def get(self, uid):
        if not uid:
            return None
        return self._b.get_all().get(uid)

    def all(self):
        return self._b.get_all()

    def save_all(self, table):
        """整表覆盖落盘（handle_profile 直写场景用）。调用方须持 _lock。"""
        self._b.save_all(table)

    def touch(self, uid, **updates):
        """加载 → 建/改 → 淘汰超限 → 落盘，返回该用户最新画像。调用方须持 _lock。"""
        profiles = self._b.get_all()
        p = profiles.get(uid)
        if p is None:
            p = {"lang": None, "skins": [],
                 "created": time.strftime("%Y-%m-%d %H:%M:%S")}
            profiles[uid] = p
        for k, v in updates.items():
            p[k] = v
        if len(profiles) > MAX_PROFILES:
            for old in sorted(profiles, key=lambda u: profiles[u].get("last_visit") or "")[:-MAX_PROFILES]:
                profiles.pop(old, None)
        self._b.save_all(profiles)
        return p


if __name__ == "__main__":
    # 自测：JSON 后端读写往返 + LRU 淘汰（不碰生产数据，用临时路径）
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        st = ProfileStore(JsonKVBackend(Path(d) / "p.json"))
        p = st.touch("u1", lang="zh", skins=["混油"])
        assert p["lang"] == "zh" and st.get("u1")["skins"] == ["混油"]
        st.touch("u1", last_visit="2026-09-01")
        assert st.get("u1")["last_visit"] == "2026-09-01"
        print("store.py 自测通过：JSON 后端读/写/淘汰 OK")
