from typing import Any, Callable, Iterable, Optional, Protocol, Tuple, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

class MonkeyPatch(Protocol):
    def setattr(self, target: str, value: Any) -> None: ...

class CaptureFixture(Protocol):
    def readouterr(self) -> Tuple[str, str]: ...

class _Mark(Protocol):
    def parametrize(
        self,
        argnames: Any,
        argvalues: Iterable[Any],
        *,
        ids: Optional[Iterable[str]] = ...,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...

mark: _Mark

def fixture(function: _F, *, autouse: bool = ...) -> _F: ...
def raises(exception: Any) -> Any: ...
