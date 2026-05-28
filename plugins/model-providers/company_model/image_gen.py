"""公司 Model 平台的 image generation provider.

原本作为独立 ``image_gen/company/`` plugin 存在, 现在合并进
``model-providers/company_model/`` plugin 目录, 作为一个子模块由 ``__init__.py``
在 plugin 加载时通过 ``agent.image_gen_registry.register_provider`` 注册.

行为跟原版 (基于上游 ``openai`` plugin 的 thin clone) 完全一致:
  - API model: ``gpt-image-2`` (公司平台 OpenAI-compatible endpoint)
  - 三档 quality: low (~15s) / medium (~40s, 默认) / high (~2min)
  - base_url + auth 都跟 chat provider 复用同一份配置
  - 输出 b64 → 落盘 cache, URL → 下载缓存
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model catalog (mirrors upstream openai plugin)
# ---------------------------------------------------------------------------

API_MODEL = "gpt-image-2"
BASE_URL = "https://model.zhenguanyu.com/v1"

_MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-image-2-low": {
        "display": "GPT Image 2 (Low) — company",
        "speed": "~15s",
        "strengths": "Fast iteration, lowest cost",
        "quality": "low",
    },
    "gpt-image-2-medium": {
        "display": "GPT Image 2 (Medium) — company",
        "speed": "~40s",
        "strengths": "Balanced — default",
        "quality": "medium",
    },
    "gpt-image-2-high": {
        "display": "GPT Image 2 (High) — company",
        "speed": "~2min",
        "strengths": "Highest fidelity, strongest prompt adherence",
        "quality": "high",
    },
}

DEFAULT_MODEL = "gpt-image-2-medium"

_SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}


def _load_company_config() -> Dict[str, Any]:
    """Read ``image_gen`` from config.yaml (returns {} on any failure)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _resolve_model() -> Tuple[str, Dict[str, Any]]:
    """Decide which tier to use and return ``(model_id, meta)``."""
    env_override = os.environ.get("COMPANY_IMAGE_MODEL")
    if env_override and env_override in _MODELS:
        return env_override, _MODELS[env_override]

    cfg = _load_company_config()
    company_cfg = cfg.get("company") if isinstance(cfg.get("company"), dict) else {}
    candidate: Optional[str] = None
    if isinstance(company_cfg, dict):
        value = company_cfg.get("model")
        if isinstance(value, str) and value in _MODELS:
            candidate = value
    if candidate is None:
        top = cfg.get("model")
        if isinstance(top, str) and top in _MODELS:
            candidate = top

    if candidate is not None:
        return candidate, _MODELS[candidate]

    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class CompanyImageGenProvider(ImageGenProvider):
    """Company Model platform ``images.generate`` backend (OpenAI-compatible)."""

    @property
    def name(self) -> str:
        return "company"

    @property
    def display_name(self) -> str:
        return "Company Model (zhenguanyu)"

    def is_available(self) -> bool:
        if not os.environ.get("COMPANY_MODEL_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": "company",
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Company Model",
            "badge": "internal",
            "tag": "gpt-image-2 via model.zhenguanyu.com",
            "env_vars": [
                {
                    "key": "COMPANY_MODEL_API_KEY",
                    "prompt": "Company Model API key",
                    "url": "https://model.zhenguanyu.com",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="company",
                aspect_ratio=aspect,
            )

        api_key = os.environ.get("COMPANY_MODEL_API_KEY")
        if not api_key:
            return error_response(
                error=(
                    "COMPANY_MODEL_API_KEY not set. Add it to ~/.hermes/.env "
                    "or run `hermes setup` to configure."
                ),
                error_type="auth_required",
                provider="company",
                aspect_ratio=aspect,
            )

        try:
            import openai
        except ImportError:
            return error_response(
                error="openai Python package not installed (pip install openai)",
                error_type="missing_dependency",
                provider="company",
                aspect_ratio=aspect,
            )

        tier_id, meta = _resolve_model()
        size = _SIZES.get(aspect, _SIZES["square"])

        payload: Dict[str, Any] = {
            "model": API_MODEL,
            "prompt": prompt,
            "size": size,
            "n": 1,
            "quality": meta["quality"],
        }

        try:
            client = openai.OpenAI(api_key=api_key, base_url=BASE_URL)
            response = client.images.generate(**payload)
        except Exception as exc:
            logger.debug("Company image generation failed", exc_info=True)
            return error_response(
                error=f"Company image generation failed: {exc}",
                error_type="api_error",
                provider="company",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        data = getattr(response, "data", None) or []
        if not data:
            return error_response(
                error="Company Model returned no image data",
                error_type="empty_response",
                provider="company",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = data[0]
        b64 = getattr(first, "b64_json", None)
        url = getattr(first, "url", None)
        revised_prompt = getattr(first, "revised_prompt", None)

        if b64:
            try:
                saved_path = save_b64_image(b64, prefix=f"company_{tier_id}")
            except Exception as exc:
                return error_response(
                    error=f"Could not save image to cache: {exc}",
                    error_type="io_error",
                    provider="company",
                    model=tier_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            image_ref = str(saved_path)
        elif url:
            try:
                saved_path = save_url_image(url, prefix=f"company_{tier_id}")
            except Exception as exc:
                logger.warning(
                    "Company image URL %s could not be cached (%s); falling back to bare URL.",
                    url,
                    exc,
                )
                image_ref = url
            else:
                image_ref = str(saved_path)
        else:
            return error_response(
                error="Company response contained neither b64_json nor URL",
                error_type="empty_response",
                provider="company",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        extra: Dict[str, Any] = {"size": size, "quality": meta["quality"]}
        if revised_prompt:
            extra["revised_prompt"] = revised_prompt

        return success_response(
            image=image_ref,
            model=tier_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="company",
            extra=extra,
        )
