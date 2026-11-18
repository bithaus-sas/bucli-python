"""API objects: plain dicts that also read by attribute (``sub.status``).

``BucliObject`` IS the dict the API answered (``json.dumps(obj)`` works,
``obj["status"]`` works); nested objects and lists are converted on the way
in. A key that clashes with a dict method must be read with brackets — the
one that matters is a subscription's ``sub["items"]`` (``sub.items`` is
``dict.items``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit

if TYPE_CHECKING:
    from bucli.client import Bucli


class LastResponse:
    """What the HTTP answer said besides its body."""

    __slots__ = ("headers", "http_status", "idempotency_key", "request_id")

    def __init__(self, http_status: int, headers: dict[str, str], idempotency_key: str | None = None) -> None:
        self.http_status = http_status
        self.headers = headers
        self.request_id = headers.get("Request-Id")
        self.idempotency_key = idempotency_key

    @property
    def replayed(self) -> bool:
        """``True`` when Bucli answered a stored response for the same ``Idempotency-Key``."""
        return self.headers.get("Idempotent-Replayed", "").lower() == "true"

    def __repr__(self) -> str:
        return f"LastResponse(http_status={self.http_status}, request_id={self.request_id!r})"


class BucliObject(dict):
    """An object of the API (``customer``, ``subscription``…)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_client", None)
        object.__setattr__(self, "last_response", None)

    # -- attribute access -----------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"{type(self).__name__} no tiene el campo {name!r}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name == "last_response":
            object.__setattr__(self, name, value)
        else:
            self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        ident = self.get("id")
        kind = self.get("object", type(self).__name__)
        head = f"<{kind} {ident}>" if ident else f"<{kind}>"
        return f"{head} {dict.__repr__(self)}"

    def __reduce__(self) -> Any:
        return (type(self), (dict(self),))

    # -- conversion -------------------------------------------------------------

    @classmethod
    def construct_from(cls, value: Any, client: Bucli | None = None) -> Any:
        """Converts the JSON the API answered (dicts become objects, lists stay lists)."""
        return convert(value, client)

    def to_dict(self) -> dict[str, Any]:
        """A deep copy made only of plain ``dict``/``list`` (for storing or serializing)."""
        return _plain(self)


class ListObject(BucliObject):
    """``{"object": "list", "data": [...], "has_more": bool, "url": "/v1/..."}``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_path", None)
        object.__setattr__(self, "_params", {})

    @property
    def data(self) -> list[Any]:
        return self.get("data") or []

    @property
    def has_more(self) -> bool:
        return bool(self.get("has_more"))

    def is_empty(self) -> bool:
        return not self.data

    def _source(self) -> tuple[str, dict[str, Any]]:
        if self._path:
            return self._path, dict(self._params)
        parts = urlsplit(self.get("url") or "")
        path = parts.path
        if path.startswith("/v1/"):
            path = path[len("/v1") :]
        return path, dict(parse_qsl(parts.query))

    def next_page(self) -> ListObject:
        """The page after this one (``starting_after`` = the last id)."""
        path, params = self._source()
        params.pop("ending_before", None)
        params["starting_after"] = self.data[-1]["id"]
        return self._client._get_list(path, params)

    def previous_page(self) -> ListObject:
        """The page before this one (``ending_before`` = the first id)."""
        path, params = self._source()
        params.pop("starting_after", None)
        params["ending_before"] = self.data[0]["id"]
        return self._client._get_list(path, params)

    def auto_paging_iter(self) -> Iterator[Any]:
        """Every object from this page on, fetching the next pages as needed.

        A page fetched with ``ending_before`` walks backwards (towards newer
        objects), as Stripe's does."""
        if self._client is None:
            yield from self.data
            return
        _, params = self._source()
        backwards = "ending_before" in params and "starting_after" not in params
        page: ListObject = self
        while True:
            items = list(reversed(page.data)) if backwards else page.data
            yield from items
            if not page.has_more or not page.data:
                return
            page = page.previous_page() if backwards else page.next_page()


def convert(value: Any, client: Bucli | None = None) -> Any:
    if isinstance(value, dict) and not isinstance(value, BucliObject):
        cls = ListObject if value.get("object") == "list" else BucliObject
        obj = cls({key: convert(item, client) for key, item in value.items()})
        object.__setattr__(obj, "_client", client)
        return obj
    if isinstance(value, list):
        return [convert(item, client) for item in value]
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value
