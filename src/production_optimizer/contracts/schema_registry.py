from __future__ import annotations

import inspect
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from . import (
    a1,
    a2,
    a3,
    artifacts,
    b1,
    b2,
    c0,
    commands,
    envelope,
    errors,
    events,
    interrupts,
    platform,
    registries,
    state,
)

_MODULES = (
    a1,
    a2,
    a3,
    b1,
    b2,
    c0,
    artifacts,
    commands,
    envelope,
    errors,
    events,
    interrupts,
    platform,
    registries,
    state,
)


def _contract_models() -> dict[str, type[BaseModel]]:
    models: dict[str, type[BaseModel]] = {}
    for module in _MODULES:
        for name, candidate in inspect.getmembers(module, inspect.isclass):
            if candidate.__module__ != module.__name__ or not issubclass(candidate, BaseModel):
                continue
            if name in models:
                raise RuntimeError(f"duplicate contract model name: {name}")
            models[name] = candidate
    return dict(sorted(models.items()))


CONTRACT_MODELS = MappingProxyType(_contract_models())


def generated_json_schemas() -> dict[str, dict[str, Any]]:
    return {name: model.model_json_schema() for name, model in CONTRACT_MODELS.items()}
