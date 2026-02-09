"""robots.txt compliance checker."""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)


class RobotsChecker:
    """Check whether a URL is allowed by the site's robots.txt."""

    def __init__(self, user_agent: str, timeout: int = 5) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        self._cache: dict[str, RobotFileParser] = {}

    def is_allowed(self, url: str) -> bool:
        """Return True if the URL is allowed by robots.txt."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        if origin not in self._cache:
            self._cache[origin] = self._fetch_robots(origin)

        rp = self._cache[origin]
        return rp.can_fetch(self._user_agent, url)

    def _fetch_robots(self, origin: str) -> RobotFileParser:
        robots_url = f"{origin}/robots.txt"
        rp = RobotFileParser()
        try:
            resp = requests.get(robots_url, timeout=self._timeout)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                logger.debug("Loaded robots.txt from %s", robots_url)
            else:
                rp.allow_all = True
        except requests.RequestException:
            rp.allow_all = True
        return rp
