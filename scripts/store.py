# -*- coding: utf-8 -*-
"""store.py — 存储接口抽象（per-key KV 接口 → JSON 实现 / Redis 预留位 + 热冷分离）
===============================================================================
对齐 pangu 存储层「接口在前、实现在后」：业务代码只依赖 KVBackend 接口 + ProfileStore
高层，**换存储（JSON → Redis）不换业务代码**——这是企业级架构的最小落地。

落地四件（2026-09-03 记忆层高并发演进第 1、3 段）：
  - KVBackend      抽象基类：per-key 三件套（get/set/delete）+ 整表两件套（get_all/save_all）。
                   业务主路径用 per-key（一人一条，不背别人）；整表仅 ProfileStore 做 LRU 淘汰
                   这类「必须全表遍历」的场景用（JSON 下即整文件读/写一次）。
  - JsonKVBackend  JSON 文件实现：data/*.json（现有格式）。诚实注：单文件即整表——per-key
                   操作底层也是整文件读+整文件写，demo 规模（≤百用户 × 单键 < 几 KB）无感；
                   Redis 切换后 per-key 变真 O(1)，业务代码零改动。**不加锁**——
                   锁在 web_server 层持有（_lock），store 内不加锁避免重蹈嵌套死锁坑。
  - RedisKVBackend 企业级预留位：接口同；真实部署时传入 redis 客户端即可切换。
  - ProfileStore   画像高层 API：**热/冷双后端**——热 backend 存画像字段（lang/skins/
                   last_visit/created，每请求读），冷 backend 存 convo 对话记忆（独立文件、
                   低频读、可丢——将来 Redis 下可带 TTL）。touch 含 LRU 淘汰，超限清最久未
                   访问用户的**热画像 + 冷 convo**（不留孤儿记忆）。

零回归原则（2026-09-03 迁移后）：
  - data/user_profiles.json 记录瘦身为 {uid: {lang, skins, last_visit, created}}（不再含
    convo）；旧文件内嵌的 convo 由 ProfileStore.migrate() 启动时一次性拆到冷表（幂等）。
  - 不改锁语义（调用方持锁，store 纯函数式委托）；
  - MAX_PROFILES 淘汰逻辑单一真相源在 config.py（`import config` 现读，可运行调参）。
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

import config  # noqa: E402  MAX_PROFILES 画像上限（import 模块现读，单一真相源可调参）


class KVBackend(ABC):
    """KV 存储接口：per-key 三件套（主路径，一人一条）+ 整表两件套（LRU/迁移专用）。
    实现方无需关心调用方锁——锁由 web 层持有，本接口是纯数据访问。"""

    # ---- per-key（业务主路径：单用户读写，不背全表）----
    @abstractmethod
    def get(self, key):
        """读单键；不存在 → None。"""

    @abstractmethod
    def set(self, key, value):
        """写单键。失败静默（存储绝不影响主流程，与 _trace 一致）。"""

    @abstractmethod
    def delete(self, key):
        """删单键（不存在则 no-op）。"""

    # ---- 整表（ProfileStore 做 LRU 淘汰 / 一次性迁移等必须遍历全表的场景才用）----
    @abstractmethod
    def get_all(self):
        """整表快照 {key: value}；空/不存在 → {}。"""

    @abstractmethod
    def save_all(self, table):
        """整表覆盖落盘。失败静默。"""


class JsonKVBackend(KVBackend):
    """JSON 文件实现：data/*.json（现有格式，utf-8，indent=1，ensure_ascii=False）。
    诚实注：单文件 = 整表，per-key 操作底层也是整读 + 整写；demo 规模（小文件）足够，
    且代码形状即生产（Redis 下 get/set 变 O(1)，业务代码零改动）。"""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _dump(self, table):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(table, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except Exception:
            pass

    def get(self, key):
        return self._load().get(key)

    def set(self, key, value):
        table = self._load()
        table[key] = value
        self._dump(table)

    def delete(self, key):
        table = self._load()
        table.pop(key, None)
        self._dump(table)

    def get_all(self):
        return self._load()

    def save_all(self, table):
        self._dump(table)


class RedisKVBackend(KVBackend):
    """Redis 预留位（**未启用**）：接口与 JSON 实现一致，换存储只换构造那一行——
    `ProfileStore(RedisKVBackend(client), RedisKVBackend(client, KEY_CONVO))`，
    业务代码（含 ProfileStore / web 委托）零改动。

    企业级映射（部署时打开）：
      - per-key  → string `beauty:profile:{key}`：GET / SET / DEL（热画像与冷 convo 各一
                   key 空间，天然冷热隔离；冷 key 可带 TTL 让 convo 自动过期——convo 可丢）。
      - get_all  → 仅供 LRU 淘汰：小库用 KEYS + MGET；大库换 SCAN（勿在生产 KEYS）。
      - save_all → pipeline SET（LRU 淘汰整批写回用）。
    真实依赖 redis 客户端库；本环境零依赖约束 → 构造时显式声明未启用。"""

    def __init__(self, client=None, key_prefix="beauty:profile:"):
        if client is None:
            raise NotImplementedError(
                "RedisKVBackend 是预留位：未传入 redis 客户端（零依赖环境）。"
                "生产部署时 `pip install redis` 并传入连接客户端，接口即 JSON 实现同款。")
        self._c = client
        self._p = key_prefix

    def _k(self, key):
        return self._p + str(key)

    def get(self, key):
        v = self._c.get(self._k(key))
        return json.loads(v) if v is not None else None

    def set(self, key, value):
        self._c.set(self._k(key), json.dumps(value, ensure_ascii=False))

    def delete(self, key):
        self._c.delete(self._k(key))

    def get_all(self):
        keys = self._c.keys(self._p + "*")          # 小库专用；大库换 SCAN
        if not keys:
            return {}
        vals = self._c.mget(keys)
        return {k[len(self._p):].decode() if isinstance(k, bytes) else k[len(self._p):]:
                json.loads(v) for k, v in zip(keys, vals) if v is not None}

    def save_all(self, table):
        with self._c.pipeline() as pipe:
            for k, v in table.items():
                pipe.set(self._k(k), json.dumps(v, ensure_ascii=False))
            pipe.execute()


class ProfileStore:
    """画像高层 API：热（画像字段）+ 冷（convo）分离双后端。
    承接原 web_server._get_profile/_touch 的全部逻辑；调用方须持 _lock（锁在 web 层）。

      - get / touch         → 热 backend（lang/skins/last_visit/created，每请求读）
      - get_convo/save_convo → 冷 backend（对话记忆独立文件，低频读）
      - touch 内 LRU 淘汰超限用户时**同步 delete 其冷 convo**（不留孤儿记忆）
      - migrate()            → 启动时一次性：把旧热表记录里内嵌的 convo 拆到冷表（幂等）
    """

    def __init__(self, hot_backend, cold_backend):
        self._h = hot_backend
        self._c = cold_backend

    # ---- 热：画像字段 ----
    def get(self, uid):
        if not uid:
            return None
        return self._h.get(uid)

    def all(self):
        return self._h.get_all()

    def touch(self, uid, **updates):
        """热画像更新：加载整表 → 建/改该用户 → 超限 LRU 淘汰（同步清冷 convo）→ 整表落盘。
        返回该用户最新热画像。调用方须持 _lock。只收热字段（lang/skins/last_visit…），
        convo 请走 save_convo（热冷分离：对话记忆不再背着画像一起写）。"""
        table = self._h.get_all()
        p = table.get(uid)
        if p is None:
            p = {"lang": None, "skins": [],
                 "created": time.strftime("%Y-%m-%d %H:%M:%S")}
            table[uid] = p
        for k, v in updates.items():
            p[k] = v
        if len(table) > config.MAX_PROFILES:
            for old in sorted(table,
                              key=lambda u: (table[u].get("last_visit") or ""))[:-config.MAX_PROFILES]:
                table.pop(old, None)
                self._c.delete(old)   # 被淘汰用户不留孤儿 convo
        self._h.save_all(table)
        return p

    # ---- 冷：对话记忆 convo（独立文件、低频读、可丢）----
    def get_convo(self, uid):
        if not uid:
            return None
        return self._c.get(uid)

    def save_convo(self, uid, convo):
        if not uid:
            return
        self._c.set(uid, convo)

    # ---- 一次性迁移：旧格式热表内嵌 convo → 拆到冷表 ----
    def migrate(self):
        """把热表旧记录里内嵌的 convo 拆到冷表（幂等，启动时调用一次）。
        旧格式：{uid: {lang, skins, convo, ...}} → 新格式热表不再含 convo 键。
        冷表已存在该 uid 不覆盖（冷表优先）。"""
        table = self._h.get_all()
        changed = False
        for uid, p in table.items():
            if isinstance(p, dict) and "convo" in p:
                convo = p.pop("convo")
                if convo and self._c.get(uid) is None:
                    self._c.set(uid, convo)
                changed = True
        if changed:
            self._h.save_all(table)


if __name__ == "__main__":
    # 自测：JSON 双后端读/写 + 热冷分离 + 旧格式迁移 + LRU 淘汰同步清冷（不碰生产数据，临时路径）
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        hot, cold = JsonKVBackend(d / "hot.json"), JsonKVBackend(d / "cold.json")
        st = ProfileStore(hot, cold)

        # 热 touch + 冷 convo 分离：touch 不带 convo，convo 走 save_convo
        st.touch("u1", lang="zh", skins=["混油"])
        st.save_convo("u1", {"orig": "想要哑光", "recent": [{"r": "u", "t": "hi"}]})
        assert st.get("u1")["lang"] == "zh" and st.get("u1")["skins"] == ["混油"]
        assert st.get("u1").get("convo") is None, "热表不应再含 convo"
        assert st.get_convo("u1")["orig"] == "想要哑光", "convo 应落在冷表"
        assert st.get("u2") is None and st.get_convo("u2") is None

        # 旧格式迁移：造一份内嵌 convo 的旧数据 → migrate → 拆到冷表且热表瘦身
        hot.save_all({"u_old": {"lang": "en", "skins": [], "created": "x",
                                "convo": {"orig": "旧对话", "recent": []}}})
        st.migrate()
        assert hot.get_all()["u_old"].get("convo") is None, "migrate 应拆走 convo"
        assert st.get_convo("u_old")["orig"] == "旧对话"
        st.migrate()   # 幂等：再跑一遍不报错、不重复
        assert hot.get_all()["u_old"].get("convo") is None

        # LRU 淘汰：MAX_PROFILES 调小后超限 → 最久未访问被清，冷 convo 同步删
        import config as _c
        _c.MAX_PROFILES = 2
        st.touch("uA", last_visit="2026-09-01 00:00:00"); st.save_convo("uA", {"a": 1})
        st.touch("uB", last_visit="2026-09-02 00:00:00"); st.save_convo("uB", {"b": 1})
        st.touch("uC", last_visit="2026-09-03 00:00:00"); st.save_convo("uC", {"c": 1})
        assert st.get("uA") is None and st.get_convo("uA") is None, "最久用户应被淘汰且冷表同清"
        assert st.get("uB") and st.get_convo("uB") and st.get("uC") and st.get_convo("uC")
        print("store.py 自测通过：热冷双后端 / per-key / 迁移幂等 / LRU 同步清冷 OK")
