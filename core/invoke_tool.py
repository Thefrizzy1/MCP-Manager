"""Invoke FastMCP tool callables that may expect Pydantic models (including nested register_* locals)."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, get_args, get_origin, get_type_hints

from pydantic import BaseModel


class ToolInvocationError(ValueError):
    """Raised when a tool payload cannot be matched to the tool signature."""


def type_namespace_for_callable(fn: Callable[..., Any]) -> dict[str, Any]:
    """Merge module globals + closure cell types so get_type_hints works for nested tools."""
    u = inspect.unwrap(fn)
    g: dict[str, Any] = {}
    mod = inspect.getmodule(u)
    if mod:
        g.update(vars(mod))
    clos = getattr(u, "__closure__", None) or ()
    for cell in clos:
        try:
            o = cell.cell_contents
        except ValueError:
            continue
        if isinstance(o, type):
            g.setdefault(o.__name__, o)
    return g


def _type_hints_for_callable(fn: Callable[..., Any]) -> dict[str, Any]:
    u = inspect.unwrap(fn)
    ns = type_namespace_for_callable(fn)
    try:
        return get_type_hints(u, globalns=ns, localns=ns, include_extras=True)
    except Exception:
        return getattr(u, "__annotations__", {}) or {}


def _pydantic_model_type(t: Any) -> type[BaseModel] | None:
    origin = get_origin(t)
    if origin is not None:
        return None
    try:
        if isinstance(t, type) and issubclass(t, BaseModel):
            return t
    except TypeError:
        return None
    return None


def pydantic_param_model(fn: Callable[..., Any]) -> tuple[str, type[BaseModel]] | None:
    """Return the single Pydantic parameter model used by a tool, if present."""
    u = inspect.unwrap(fn)
    hints = _type_hints_for_callable(fn)
    for pname in inspect.signature(u).parameters:
        model = _pydantic_model_type(hints.get(pname))
        if model is not None:
            return pname, model
    return None


def schema_audit_for_payload(fn: Callable[..., Any], payload: dict | None) -> dict[str, Any]:
    """Compare a smoke payload with the tool's actual Pydantic model."""
    raw = payload if payload is not None else {}
    param_model = pydantic_param_model(fn)
    if param_model is None:
        return {
            "status": "pass",
            "model": None,
            "required": [],
            "optional": [],
            "unexpected": [],
            "missing": [],
            "canonical_payload": {},
        }
    _, model = param_model
    fields = model.model_fields
    accepted: set[str] = set()
    required: list[str] = []
    optional: list[str] = []
    canonical: dict[str, Any] = {}
    for name, field in fields.items():
        alias = field.alias if isinstance(field.alias, str) else None
        validation_alias = getattr(field, "validation_alias", None)
        accepted.add(name)
        if alias:
            accepted.add(alias)
        if validation_alias:
            choices = getattr(validation_alias, "choices", None)
            accepted.update(str(c) for c in choices or [validation_alias])
        if field.is_required():
            required.append(name)
            canonical[name] = _example_value_for_field(field.annotation)
        else:
            optional.append(name)
            canonical[name] = field.default
    missing = [name for name in required if name not in raw and (fields[name].alias or name) not in raw]
    unexpected = [name for name in raw if name not in accepted]
    return {
        "status": "fail" if missing or unexpected else "pass",
        "model": model.__name__,
        "required": required,
        "optional": optional,
        "unexpected": unexpected,
        "missing": missing,
        "canonical_payload": canonical,
    }


def _example_value_for_field(annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is not None and args:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _example_value_for_field(non_none[0])
    if annotation is str:
        return "TEST_SMOKE"
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is bool:
        return False
    if annotation is list:
        return []
    if annotation is dict:
        return {}
    return None


def build_tool_call_kwargs(fn: Callable[..., Any], payload: dict | None = None) -> dict[str, Any]:
    """Validate payload and build strict kwargs; never retries with empty args."""
    raw: dict = payload if payload is not None else {}
    u = inspect.unwrap(fn)
    sig = inspect.signature(u)
    param_model = pydantic_param_model(fn)
    if param_model is not None:
        pname, model = param_model
        return {pname: model.model_validate(raw)}

    if not sig.parameters:
        if raw:
            raise ToolInvocationError(f"Tool takes no parameters but payload was provided: {sorted(raw)}")
        return {}

    try:
        sig.bind(**raw)
    except TypeError as exc:
        raise ToolInvocationError(str(exc)) from exc
    return dict(raw)


async def invoke_mcp_tool_fn(
    fn: Callable[..., Awaitable[Any]],
    *,
    payload: dict | None = None,
) -> Any:
    u = inspect.unwrap(fn)
    kw = build_tool_call_kwargs(fn, payload)
    return await u(**kw)
