from __future__ import annotations

import re
from typing import Any, Iterable, cast

_TEXT_PREFIX = r"(^|[\s,{(\[?&])"
_CONTROL_ESCAPES = {
    "\n": r"\n",
    "\r": r"\r",
    "\t": r"\t",
}


def _compile_patterns(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue
    return tuple(compiled)


def _inline_key_variants(key: str) -> set[str]:
    key = key.strip().lower()
    if not key:
        return set()
    variants = {key}
    for current in tuple(variants):
        variants.add(current.replace("_", "-"))
        variants.add(current.replace("-", "_"))
        variants.add(current.replace("_", ""))
        variants.add(current.replace("-", ""))
    return {variant for variant in variants if variant}


class Redactor:
    def __init__(self, keys: Iterable[str], patterns: Iterable[str]):
        self.keys = {str(key).strip().lower() for key in keys if str(key).strip()}
        self.patterns = _compile_patterns(patterns)
        inline_keys = sorted(
            {variant for key in self.keys for variant in _inline_key_variants(key)}
        )
        self._inline_key_pattern = (
            re.compile(
                rf"(?i)(?P<prefix>{_TEXT_PREFIX})(?P<key>['\"]?(?:{'|'.join(map(re.escape, inline_keys))})['\"]?)(?P<sep>\s*[:=]\s*)(?P<auth>Bearer\s+)?(?P<value>['\"]?[^\s,;]+['\"]?)"
            )
            if inline_keys
            else None
        )
        self._bearer_pattern = (
            re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+\b")
            if {"authorization", "auth", "token"} & self.keys
            else None
        )

    def scrub(self, value: Any) -> Any:
        if isinstance(value, dict):
            mapping = cast(dict[Any, Any], value)
            return {
                key: "***" if str(key).lower() in self.keys else self.scrub(item)
                for key, item in mapping.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.scrub(item) for item in cast(Iterable[Any], value)]
        if isinstance(value, str):
            return self.scrub_text(value)
        return value

    def scrub_text(self, value: str) -> str:
        if self._inline_key_pattern is not None:
            value = self._inline_key_pattern.sub(
                lambda match: (
                    f"{match.group('prefix')}{match.group('key')}{match.group('sep')}"
                    f"{match.group('auth') or ''}***"
                ),
                value,
            )
        if self._bearer_pattern is not None:
            value = self._bearer_pattern.sub("Bearer ***", value)
        for pattern in self.patterns:
            value = pattern.sub("***", value)
        return value


def escape_control_chars(value: str) -> str:
    escaped: list[str] = []
    for char in value:
        if char in _CONTROL_ESCAPES:
            escaped.append(_CONTROL_ESCAPES[char])
            continue
        codepoint = ord(char)
        escaped.append(f"\\x{codepoint:02x}" if codepoint < 32 or codepoint == 127 else char)
    return "".join(escaped)
