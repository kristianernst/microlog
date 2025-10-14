from typing import Any, Callable, Dict, Optional, TypeVar

T = TypeVar("T")

def chz(cls: T) -> T: ...
def field(
    *,
    default: Any = ...,
    default_factory: Optional[Callable[[], Any]] = ...,
    doc: Optional[str] = ...,
    metadata: Optional[Dict[str, Any]] = ...,
) -> Any: ...
