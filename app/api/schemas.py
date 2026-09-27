"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CreateKBRequest(BaseModel):
    kb_id: str = Field(min_length=1, max_length=128, description="知识库标识")
    name: str = Field(min_length=1, max_length=256, description="可读名称")


class LoadTheoryRequest(BaseModel):
    theory: str = Field(min_length=1, description="RDRL 理论文本")


class AddFactsRequest(BaseModel):
    facts: list[str] = Field(
        min_length=1, description="接地事实文本列表, 例如 ['Bird(tweety).']"
    )


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, description="查询目标, 例如 Flies(X)")


class ErrorBody(BaseModel):
    category: str
    error: str
    message: str
    details: dict
    run_id: Optional[str] = None
