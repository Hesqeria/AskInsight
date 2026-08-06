"""Operator hierarchy: MapOp, JoinOp, BranchOp, InputOp, StreamOp, ReduceOp."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, Dict, Generic, List, Optional, TypeVar
from app.workflow.dag import DagNode, DagContext

IN = TypeVar("IN")
OUT = TypeVar("OUT")
T = TypeVar("T")


class BaseOperator(DagNode, ABC):
    """Executable node: _run(ctx) -> result stored in ctx.node_outputs."""

    def __init__(self, name: str, max_retries: int = 1):
        super().__init__(name)
        self.max_retries = max_retries
        self._config: Dict[str, Any] = {}

    @abstractmethod
    async def _run(self, ctx: DagContext) -> Any: ...

    async def execute(self, ctx: DagContext) -> Any:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                result = await self._run(ctx)
                ctx.set_output(self.name, result)
                return result
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        raise last_error

    def configure(self, **kwargs) -> "BaseOperator":
        self._config.update(kwargs)
        return self


class MapOp(BaseOperator, Generic[IN, OUT]):
    """Single input -> single output. Implement map(input)->output or pass fn."""

    def __init__(self, name: str, map_fn: Optional[Callable[[Any], Any]] = None, **kwargs):
        super().__init__(name, **kwargs)
        self._map_fn = map_fn

    async def _run(self, ctx: DagContext) -> OUT:
        inp = ctx.share_data.get("_pipeline_input") if not self.upstream else ctx.get_output(self.upstream[0].name)
        if self._map_fn:
            return await self._map_fn(inp) if asyncio.iscoroutinefunction(self._map_fn) else self._map_fn(inp)
        return await self.map(inp)

    async def map(self, input_value: Any) -> OUT:
        raise NotImplementedError(f"Override map() in {self.name}")


class JoinOp(BaseOperator, Generic[OUT]):
    """Multiple inputs -> single output. Implement combine(dict)->output."""

    async def _run(self, ctx: DagContext) -> OUT:
        inputs = {p.name: ctx.get_output(p.name) for p in self.upstream}
        return await self.combine(inputs)

    async def combine(self, inputs: Dict[str, Any]) -> OUT:
        raise NotImplementedError(f"Override combine() in {self.name}")


class BranchOp(BaseOperator):
    """Conditional routing. branches = {label: predicate_fn}."""

    def __init__(self, name: str, branches: Optional[Dict[str, Callable[[Any], bool]]] = None, **kwargs):
        super().__init__(name, **kwargs)
        self.branches = branches or {}

    async def _run(self, ctx: DagContext) -> List[str]:
        inp = ctx.share_data.get("_pipeline_input") if not self.upstream else ctx.get_output(self.upstream[0].name)
        active = [label for label, pred in self.branches.items() if pred(inp)]
        ctx.share_data[f"__branch_{self.name}"] = active
        return active


class InputOp(BaseOperator, Generic[OUT]):
    """Read from external source. Implement read(ctx)->output."""

    async def _run(self, ctx: DagContext) -> OUT:
        return await self.read(ctx)

    async def read(self, ctx: DagContext) -> OUT:
        raise NotImplementedError(f"Override read() in {self.name}")


class StreamOp(BaseOperator, Generic[IN, OUT]):
    """Async streaming. Implement stream(input)->AsyncIterator[output]."""

    async def _run(self, ctx: DagContext) -> List[OUT]:
        inp = ctx.share_data.get("_pipeline_input") if not self.upstream else ctx.get_output(self.upstream[0].name)
        results = []
        async for item in self.stream(inp):
            results.append(item)
        return results

    async def stream(self, input_value: Any) -> AsyncIterator[OUT]:
        raise NotImplementedError(f"Override stream() in {self.name}")


class ReduceOp(BaseOperator, Generic[T]):
    """Multiple upstream -> single output. Implement reduce(list)->output."""

    async def _run(self, ctx: DagContext) -> T:
        inputs = [ctx.get_output(p.name) for p in self.upstream]
        return await self.reduce(inputs)

    async def reduce(self, inputs: List[Any]) -> T:
        raise NotImplementedError(f"Override reduce() in {self.name}")
