"""
backend/core/agent_config.py — Pydantic-Modelle für agents.yaml.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class EchoAgentConfig(BaseModel):
    type: Literal["echo"]
    id: str
    name: str


class LLMAgentConfig(BaseModel):
    type: Literal["llm"]
    id: str
    name: str
    model: str = ""
    provider: str = "ollama"
    soul: str = ""
    enabled: bool = True


class QCSettings(BaseModel):
    enabled: bool = True
    min_score: int = 7
    max_retries: int = 2
    auditor_id: str = ""


class MartinAgentConfig(BaseModel):
    type: Literal["martin"]
    model: str = ""
    qc: QCSettings = Field(default_factory=QCSettings)


class SkillAgentConfig(BaseModel):
    type: Literal["skill"]
    skill_id: str
    enabled: bool = True
    config: dict = Field(default_factory=dict)


class A2AGatewayConfig(BaseModel):
    type: Literal["a2a_gateway"]
    default_recipient: str = "agent:alice"


AgentConfig = Annotated[
    Union[
        EchoAgentConfig,
        LLMAgentConfig,
        MartinAgentConfig,
        SkillAgentConfig,
        A2AGatewayConfig,
    ],
    Field(discriminator="type"),
]


class AgentsFile(BaseModel):
    agents: list[AgentConfig]
