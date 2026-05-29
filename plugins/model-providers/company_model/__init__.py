"""公司 Model 平台 (model.zhenguanyu.com) 的 provider profile.

公司平台是 OpenAI-wire 协议的转发网关, 承载多个上游 (Anthropic, OpenAI,
DeepSeek, Moonshot, Zhipu, Qwen, Xiaomi 等). 不同上游模型有不同的
``max_completion_tokens`` 上限. 当客户端不显式声明 ``max_tokens`` 时,
公司网关会用一个保守的默认值 (≈ 4k-8k), 导致 Claude 生成的长回复被截断
为 ``finish_reason=length``.

这个 profile 通过 OpenRouter 公开元数据 (``/api/v1/models``) 动态查询每个
模型的真实 max_output_tokens 上限并按需注入. 数据源跟公司控制台前端
(``/console/models``) 用的完全一致.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from typing import Any, Optional

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)


# ── OpenRouter 元数据查询 ──────────────────────────────────────────
#
# 公司平台 ``/v1/models`` 只返回 id/object/owned_by/created 四个字段, 不带
# context_length / max_output / modality 等关键元数据. 所以我们去公司控制台
# 前端用的同一个数据源 — OpenRouter ``/api/v1/models`` — 查询.
#
# 缓存策略: 进程内一次性拉取, 24 小时刷新; 拉不到时缓存 None 并降级到静态映射.

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_TTL_SECONDS = 24 * 3600

# 公司平台 ID → OpenRouter ID 的映射 (公司平台用 ``-`` 分隔, OpenRouter 用 ``.``).
# 没有显式映射时 fallback 到子串模糊匹配 (见 ``_lookup_openrouter_metadata``).
# 只列公司平台 /v1/models 当前实际存在的聊天模型 (snapshot: 2026-05-29).
_COMPANY_TO_OPENROUTER: dict[str, str] = {
    "claude-opus-4-8":      "anthropic/claude-opus-4.8",
    "claude-sonnet-4-6":    "anthropic/claude-sonnet-4.6",
    "gpt-5.5":              "openai/gpt-5.5",
    "deepseek-v4-pro":      "deepseek/deepseek-v4-pro",
    "kimi-k2.6":            "moonshotai/kimi-k2.6",
    "qwen3.7-max":          "qwen/qwen3.7-max",
    "xiaomi/mimo-v2.5-pro": "xiaomi/mimo-v2.5-pro",
}

# 当 OpenRouter 不可达时使用的静态兜底 (数据来自 2026-05-29 OpenRouter 快照,
# 使用 ``top_provider.max_completion_tokens`` 字段). 即使 OpenRouter 永久不可达,
# 这里也保证每个模型有合理的输出上限. 实际请求总是优先用 live 元数据.
_STATIC_FALLBACK_MAX_OUTPUT: dict[str, int] = {
    "claude-opus-4-8":      128_000,
    "claude-sonnet-4-6":    128_000,
    "gpt-5.5":              128_000,
    "deepseek-v4-pro":      384_000,
    "kimi-k2.6":            262_142,
    "qwen3.7-max":           65_536,
    "xiaomi/mimo-v2.5-pro": 131_072,
}

# 未知模型的兜底输出上限. 跟随 Claude/GPT-5 等主流模型档位 (128k), 这样新增的
# 模型不会被错误地缩到偏小的 32k.
_DEFAULT_MAX_OUTPUT = 128_000


_or_cache: Optional[dict[str, dict[str, Any]]] = None
_or_cache_at: float = 0.0
_or_cache_lock = threading.Lock()


def _fetch_openrouter_catalog(timeout: float = 8.0) -> Optional[dict[str, dict[str, Any]]]:
    """拉取 OpenRouter 全量模型目录, 按 id 索引.

    成功返回 ``{model_id: {context_length, max_completion_tokens, ...}}``,
    失败返回 ``None`` (不抛出, 调用方走静态兜底).
    """
    try:
        req = urllib.request.Request(_OPENROUTER_MODELS_URL)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "hermes-cli/company-model-plugin")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        catalog: dict[str, dict[str, Any]] = {}
        for m in items:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if not isinstance(mid, str):
                continue
            top = m.get("top_provider") or {}
            arch = m.get("architecture") or {}
            catalog[mid] = {
                "context_length": (
                    top.get("context_length") or m.get("context_length") or 0
                ),
                "max_completion_tokens": top.get("max_completion_tokens") or 0,
                "input_modalities": arch.get("input_modalities") or [],
                "output_modalities": arch.get("output_modalities") or [],
            }
        return catalog or None
    except Exception as exc:
        logger.debug("OpenRouter catalog fetch failed: %s", exc)
        return None


def _get_openrouter_catalog() -> Optional[dict[str, dict[str, Any]]]:
    """读取或刷新 OpenRouter 目录缓存. 拉取失败时返回 ``None``."""
    global _or_cache, _or_cache_at
    import time

    now = time.time()
    with _or_cache_lock:
        if _or_cache is not None and (now - _or_cache_at) < _CACHE_TTL_SECONDS:
            return _or_cache
        catalog = _fetch_openrouter_catalog()
        if catalog is not None:
            _or_cache = catalog
            _or_cache_at = now
        return _or_cache


def _lookup_openrouter_metadata(model: str) -> Optional[dict[str, Any]]:
    """在 OpenRouter 目录里查找模型元数据.

    匹配顺序:
      1. ``_COMPANY_TO_OPENROUTER`` 显式映射
      2. 公司 ID 直接在 OpenRouter 中存在 (xiaomi/mimo-v2.5-pro 类)
      3. 子串模糊匹配 (model 出现在某个 OR id 的 path 部分)
    """
    catalog = _get_openrouter_catalog()
    if not catalog:
        return None
    explicit = _COMPANY_TO_OPENROUTER.get(model)
    if explicit and explicit in catalog:
        return catalog[explicit]
    if model in catalog:
        return catalog[model]
    m_norm = model.lower().replace(".", "-")
    best: Optional[dict[str, Any]] = None
    best_len = 0
    for or_id, meta in catalog.items():
        or_path = or_id.split("/", 1)[-1].lower().replace(".", "-")
        if or_path == m_norm and len(or_path) > best_len:
            best = meta
            best_len = len(or_path)
    return best


def _resolve_max_output(model: str) -> int:
    """解析模型的 max_tokens 上限.

    优先级: OpenRouter live 元数据 > 静态兜底表 > 全局默认.
    """
    if not model:
        return _DEFAULT_MAX_OUTPUT
    meta = _lookup_openrouter_metadata(model)
    if meta:
        live = meta.get("max_completion_tokens")
        if isinstance(live, int) and live > 0:
            return live
    static = _STATIC_FALLBACK_MAX_OUTPUT.get(model)
    if static:
        return static
    return _DEFAULT_MAX_OUTPUT


def _is_chat_model(model_id: str) -> bool:
    """判断公司平台返回的模型是否是聊天模型.

    判定逻辑(基于运行时数据, 不用关键字 hardcode):
      1. 在 OpenRouter 目录里存在 → 一定是聊天模型 (OR 不收录 ASR/TTS/图像生成)
      2. 否则: 当 OR 元数据不可用时, 用 ``output_modalities`` (如果未来公司平台
         补充了元数据) 判断
      3. 都查不到时返回 False (保守: 宁可漏掉新模型也不要把语音模型混入)
    """
    meta = _lookup_openrouter_metadata(model_id)
    if meta is not None:
        out_mods = meta.get("output_modalities") or []
        if not out_mods:
            return True
        return "text" in out_mods
    return False


# ── ProviderProfile ────────────────────────────────────────────────


class CompanyModelProfile(ProviderProfile):
    """公司 Model 平台 profile, 按模型动态注入 max_tokens."""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """注入 ``max_tokens`` 到 top-level api_kwargs.

        Transport 在 ``_build_kwargs_from_profile`` 里:
          1. 先按 ephemeral / user_max / profile.default_max_tokens 解析 max_tokens
          2. 再 ``api_kwargs.update(top_level_from_profile)``
        所以这里返回的 ``max_tokens`` 会覆盖前面的默认值, 但 ``ephemeral``
        (上下文裁剪 / 重试缩减) 路径会通过 ``_ephemeral_max_output_tokens``
        提前消费, 不走这里 — 这是期望的: 上下文不够时缩减优先.
        """
        model = context.get("model") or ""
        max_out = _resolve_max_output(model)
        return {}, {"max_tokens": max_out}

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """从公司平台 ``/v1/models`` 拉取实时模型列表, 用 OpenRouter 元数据过滤.

        过滤逻辑见 ``_is_chat_model``: 保留所有在 OpenRouter 目录里能查到的模型,
        丢弃 ASR/TTS/图像生成等非聊天模型 (OR 不收录这些类型). 顺序保持公司平台
        ``/v1/models`` 返回的原始顺序, 不做偏好重排 — "默认模型"是用户配置的职责
        (config.yaml ``model.default``), 不该由 provider plugin 用副作用表达.
        """
        ids = super().fetch_models(api_key=api_key, timeout=timeout)
        if not ids:
            return None
        chat_ids = [mid for mid in ids if _is_chat_model(mid)]
        return chat_ids or list(ids)


company_model = CompanyModelProfile(
    name="company_model",
    aliases=("company-model", "zhenguanyu", "modelgate"),
    api_mode="chat_completions",
    display_name="公司 Model 平台",
    description="公司内部 Model 网关 (model.zhenguanyu.com) — 多上游 OpenAI-wire 转发",
    env_vars=("COMPANY_MODEL_API_KEY",),
    base_url="https://model.zhenguanyu.com/v1",
    hostname="model.zhenguanyu.com",
    # 离线兜底列表: 仅当公司平台 /v1/models 不可达时使用. 在线时 plugin 加载阶段
    # 会通过 live 探测把真实模型 push 到 _PROVIDER_MODELS, 覆盖这个静态列表.
    # 顺序跟公司平台 /v1/models snapshot (2026-05-29) 保持一致, 不强排序.
    fallback_models=(
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "deepseek-v4-pro",
        "gpt-5.5",
        "kimi-k2.6",
        "qwen3.7-max",
        "xiaomi/mimo-v2.5-pro",
    ),
    # auxiliary 任务 (压缩/总结/视觉等) 用 sonnet — 公司平台已下线 haiku-4-5,
    # sonnet 是 Claude 家族里目前最便宜的, 跟主模型 prompt cache 兼容.
    default_aux_model="claude-sonnet-4-6",
)

register_provider(company_model)


# ── 让 /model 选择器看见完整且实时的模型列表 ──────────────────────
#
# hermes_cli/model_switch.py 的 ``list_authenticated_providers`` 在 section 2b
# 直接通过 ``curated.get(slug, [])`` 读模型列表, 而 ``curated`` 是
# ``hermes_cli.models._PROVIDER_MODELS`` 的浅拷贝, 不会调 ``fetch_models()``.
# 因此 plugin 必须主动把模型列表 push 到 ``_PROVIDER_MODELS`` 里.
#
# 我们这里直接调用 plugin 自己的 ``fetch_models`` 走 live 探测, 这样:
#   - /model 选择器看到的就是公司平台 /v1/models 的实时返回
#   - 公司平台增删模型 (例如下线 gpt-4o, 上线新模型) 会自动反映, 无需改代码
#   - 如果探测失败 (无 API key / 网络不可达), 自动回退到 fallback_models
#
# 注意: 这只是 module-level 字典注入, 不修改 hermes-agent 源码.
def _inject_models_into_curated() -> None:
    try:
        from hermes_cli import models as _hermes_models

        api_key = os.getenv("COMPANY_MODEL_API_KEY", "").strip()
        live_ids: Optional[list[str]] = None
        if api_key:
            try:
                live_ids = company_model.fetch_models(api_key=api_key, timeout=5.0)
            except Exception as exc:
                logger.debug("company_model live model fetch failed: %s", exc)
        ids = live_ids if live_ids else list(company_model.fallback_models)
        _hermes_models._PROVIDER_MODELS["company_model"] = ids
    except Exception as exc:
        logger.debug("Failed to inject company_model into _PROVIDER_MODELS: %s", exc)


_inject_models_into_curated()


# ── 注册公司平台的 image generation provider ──────────────────────
#
# 公司平台 ``/v1/images/generations`` (gpt-image-2) 需要单独注册到
# ``agent.image_gen_registry``. 原本作为独立的 ``image_gen/company/`` plugin
# 存在, 现在合并到这里 — 一个 plugin 目录管 chat + image gen 两件事.
#
# 注意: hermes 的 plugin loader 按 kind 分流, model-provider plugin 不会执行
# 通用的 ``register(ctx)`` entry point. 但 ``agent.image_gen_registry.register_provider``
# 是 module-level 的公开函数, 我们在 plugin import 时直接调用即可绕过这个限制.
#
# 用 importlib 加载子模块, 因为 user plugin 通过 ``spec_from_file_location``
# 装载, 不是常规 package, ``from . import image_gen`` 不一定安全.
def _register_image_gen_provider() -> None:
    try:
        import importlib.util
        from pathlib import Path

        from agent.image_gen_registry import register_provider as _register_image_gen

        _here = Path(__file__).resolve().parent
        _img_file = _here / "image_gen.py"
        if not _img_file.is_file():
            logger.debug("company_model image_gen.py not found, skipping image registration")
            return
        # 用 plugin 自己的 module 名作 prefix, 避免跟其他 user plugin 的 image_gen 子模块冲突.
        _spec = importlib.util.spec_from_file_location(
            "_hermes_user_provider_company_model.image_gen", _img_file
        )
        if _spec is None or _spec.loader is None:
            logger.debug("Failed to spec image_gen.py for company_model plugin")
            return
        _img_mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_img_mod)
        _register_image_gen(_img_mod.CompanyImageGenProvider())
        logger.debug("company_model: image gen provider registered")
    except Exception as exc:
        logger.warning(
            "company_model: failed to register image gen provider: %s", exc
        )


_register_image_gen_provider()
