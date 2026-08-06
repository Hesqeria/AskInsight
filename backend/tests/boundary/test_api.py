"""B6.1-B6.2: API interface tests (schema validation + concurrency isolation)"""
import asyncio
import uuid

import pytest
from pydantic import ValidationError

from app.api.schemas.query_schema import QuerySchema


def test_b61_missing_query_field_raises():
    """B6.1: missing query field makes Pydantic raise ValidationError (corresponds to FastAPI 422)"""
    with pytest.raises(ValidationError):
        QuerySchema()  # do not pass query


def test_b61b_query_none_raises():
    """B6.1b: query=None should raise"""
    with pytest.raises(ValidationError):
        QuerySchema(query=None)


def test_b61c_empty_string_passes():
    """B6.1c: query="" is valid under pydantic (str), needs business-layer validation"""
    obj = QuerySchema(query="")
    assert obj.query == ""


def test_b62_concurrent_requests_isolate_request_id():
    """B6.2: concurrent request_id values must not pollute each other (contextvars isolation)"""
    from app.core.context import request_id_ctx_var

    async def task(rid):
        request_id_ctx_var.set(rid)
        await asyncio.sleep(0.01)
        return request_id_ctx_var.get()

    async def main():
        rids = [str(uuid.uuid4()) for _ in range(10)]
        results = await asyncio.gather(*[task(r) for r in rids])
        assert results == rids

    asyncio.run(main())
