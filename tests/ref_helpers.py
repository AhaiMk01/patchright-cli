"""Fakes for handler tests that act on snapshot refs, with no browser."""

from unittest.mock import AsyncMock, MagicMock

# A tab's last full snapshot, as Playwright's AI mode prints it.
DEFAULT_RAW = "\n".join(
    [
        "- generic [active] [ref=e1]:",
        *(f'  - button "Button {n}" [ref=e{n}]' for n in range(2, 10)),
    ]
)


def use_refs(session, locator=None, raw: str = DEFAULT_RAW, count: int = 1):
    """Make `session` resolve refs in `raw` to `locator` through page.get_by_ref.

    Returns the locator, whose count() reports `count` matches.
    """
    locator = locator if locator is not None else MagicMock()
    if not isinstance(getattr(locator, "count", None), AsyncMock):
        locator.count = AsyncMock(return_value=count)
    session.last_raw = raw
    session.page.get_by_ref = MagicMock(return_value=locator)
    return locator
