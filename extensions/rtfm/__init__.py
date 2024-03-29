from __future__ import annotations

import logging
import pathlib
from typing import TYPE_CHECKING

from utilities.shared.async_config import Config

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.shared._types.config import RTFMConfig

from ._redis_cache import DocRedisCache

LOGGER = logging.getLogger(__name__)
RTFM_CONFIG_PATH = pathlib.Path("configs/rtfm.json")

MAX_SIGNATURE_AMOUNT = 3
PRIORITY_PACKAGES = ("python",)
NAMESPACE = "doc"

doc_cache = DocRedisCache(namespace=NAMESPACE)


async def setup(bot: Mipha) -> None:
    """Load the Doc cog."""
    from .docs import DocCog

    redis_key = bot.config.get("redis")
    rtfm_config = RTFM_CONFIG_PATH.exists() and Config["RTFMConfig"](RTFM_CONFIG_PATH)
    if not bot.redis or not redis_key or not rtfm_config:
        LOGGER.warning("Not running the docs extension due to lack of redis or configuration.")
        return

    await bot.add_cog(DocCog(bot, config=rtfm_config))
