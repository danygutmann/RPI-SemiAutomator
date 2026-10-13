"""Typed TCU requests, call records, and D-Bus JSON conversion."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from dbus_next import Variant
from dbus_next.signature import SignatureType


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dbus_value_to_json(value: Any, signature: SignatureType) -> Any:
    """Encode D-Bus-only types explicitly instead of flattening them to strings."""
    token = signature.token
    if token == "v":
        return {
            "$type": "variant",
            "signature": value.signature,
            "value": dbus_value_to_json(value.value, value.type),
        }
    if token == "o":
        return {"$type": "object_path", "value": value}
    if token == "g":
        return {"$type": "signature", "value": value}
    if token == "a":
        child = signature.children[0]
        if child.token == "y":
            return {
                "$type": "bytes",
                "base64": base64.b64encode(bytes(value)).decode("ascii"),
            }
        if child.token == "{":
            key_type, value_type = child.children
            return {
                "$type": "dict",
                "signature": signature.signature,
                "entries": [
                    [
                        dbus_value_to_json(key, key_type),
                        dbus_value_to_json(item, value_type),
                    ]
                    for key, item in value.items()
                ],
            }
        return [dbus_value_to_json(item, child) for item in value]
    if token == "(":
        return {
            "$type": "struct",
            "signature": signature.signature,
            "value": [
                dbus_value_to_json(item, child)
                for item, child in zip(value, signature.children, strict=True)
            ],
        }
    return value


def json_to_dbus_value(value: Any, signature: SignatureType) -> Any:
    """Validate and convert one JSON value according to its introspected type."""
    token = signature.token
    if token == "v":
        if not isinstance(value, dict) or value.get("$type") != "variant":
            raise ValueError("Variant arguments must use the typed variant JSON form")
        nested_signature = value.get("signature")
        if not isinstance(nested_signature, str):
            raise ValueError("Variant arguments require a D-Bus signature")
        from dbus_next.signature import SignatureTree

        tree = SignatureTree._get(nested_signature)
        if len(tree.types) != 1:
            raise ValueError("A variant signature must describe exactly one value")
        nested_type = tree.types[0]
        nested_value = json_to_dbus_value(value.get("value"), nested_type)
        return Variant(nested_signature, nested_value)
    if token == "o":
        if not isinstance(value, dict) or value.get("$type") != "object_path":
            raise ValueError("Object paths must use the typed object_path JSON form")
        value = value.get("value")
        if not isinstance(value, str):
            raise ValueError("Object path value must be a string")
        return value
    if token == "g":
        if not isinstance(value, dict) or value.get("$type") != "signature":
            raise ValueError("Signatures must use the typed signature JSON form")
        value = value.get("value")
        if not isinstance(value, str):
            raise ValueError("Signature value must be a string")
        return value
    if token == "a":
        child = signature.children[0]
        if child.token == "y":
            if isinstance(value, dict) and value.get("$type") == "bytes":
                try:
                    return base64.b64decode(value.get("base64", ""), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("Byte-array base64 value is invalid") from exc
            if isinstance(value, list) and all(
                isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 255
                for item in value
            ):
                return bytes(value)
            raise ValueError("Byte arrays must be a byte list or typed base64 object")
        if child.token == "{":
            if not isinstance(value, dict) or value.get("$type") != "dict":
                raise ValueError("Dictionaries must use the typed dict JSON form")
            if value.get("signature") != signature.signature:
                raise ValueError(f"Dictionary signature must be '{signature.signature}'")
            entries = value.get("entries")
            if not isinstance(entries, list):
                raise ValueError("Dictionary entries must be a list of key/value pairs")
            key_type, value_type = child.children
            converted = {}
            for entry in entries:
                if not isinstance(entry, list) or len(entry) != 2:
                    raise ValueError("Each dictionary entry must contain a key and value")
                key = json_to_dbus_value(entry[0], key_type)
                try:
                    converted[key] = json_to_dbus_value(entry[1], value_type)
                except TypeError as exc:
                    raise ValueError("Dictionary keys must be hashable") from exc
            return converted
        if not isinstance(value, list):
            raise ValueError("Array arguments must be JSON arrays")
        return [json_to_dbus_value(item, child) for item in value]
    if token == "(":
        if isinstance(value, dict) and value.get("$type") == "struct":
            if value.get("signature") != signature.signature:
                raise ValueError(f"Struct signature must be '{signature.signature}'")
            value = value.get("value")
        if not isinstance(value, list) or len(value) != len(signature.children):
            raise ValueError("Struct arguments must contain one value per struct field")
        return tuple(
            json_to_dbus_value(item, child)
            for item, child in zip(value, signature.children, strict=True)
        )
    if token == "b":
        if not isinstance(value, bool):
            raise ValueError("Boolean D-Bus arguments require a JSON boolean")
        return value
    if token in {"y", "n", "q", "i", "u", "x", "t", "h"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"D-Bus '{token}' arguments require a JSON integer")
        return value
    if token == "d":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("D-Bus double arguments require a JSON number")
        return float(value)
    if token in {"s"}:
        if not isinstance(value, str):
            raise ValueError("D-Bus string arguments require a JSON string")
        return value
    raise ValueError(f"Unsupported D-Bus argument type: {token}")


@dataclass
class CallRecord:
    request_id: str
    status: str
    service: str
    object_path: str
    interface: str
    method: str
    submitted_at: str
    timeout_seconds: float
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: float | None = None
    result: Any = None
    result_signature: str | None = None
    error: dict[str, str] | None = None
    _arguments: list[Any] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("_arguments", None)
        return result

    @classmethod
    def create(
        cls,
        service: str,
        object_path: str,
        interface: str,
        method: str,
        arguments: list[Any],
        timeout_seconds: float,
    ) -> "CallRecord":
        return cls(
            request_id=str(uuid4()),
            status="accepted",
            service=service,
            object_path=object_path,
            interface=interface,
            method=method,
            submitted_at=utc_now(),
            timeout_seconds=timeout_seconds,
            _arguments=arguments,
        )
