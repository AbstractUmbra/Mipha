from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Mipha

from ._redis_cache import DocRedisCache

LOGGER = logging.getLogger(__name__)

MAX_SIGNATURE_AMOUNT = 3
PRIORITY_PACKAGES = ("python",)
NAMESPACE = "doc"

doc_cache = DocRedisCache(namespace=NAMESPACE)


async def setup(bot: Mipha) -> None:
    """Load the Doc cog."""
    from .docs import DocCog

    redis_key = bot.config.get("redis")
    if not bot.redis or not redis_key:
        LOGGER.warning("Not running the docs extension due to lack of redis.")
        return

    await bot.add_cog(DocCog(bot))
