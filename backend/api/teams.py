"""
backend/api/teams.py — Team REST-API.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.team_protocol import Team

router = APIRouter()

# In-Memory Team-Registry (für Phase 5: in DB persistieren)
_teams: dict[str, Team] = {}


class CreateTeamRequest(BaseModel):
    name: str


class AddMemberRequest(BaseModel):
    agent_id: str


@router.get("/teams")
async def list_teams():
    return [t.to_dict() for t in _teams.values()]


@router.post("/teams")
async def create_team(body: CreateTeamRequest):
    from backend.core.protocol import new_team_id
    team_id = new_team_id()
    team = Team(team_id=team_id, name=body.name)
    _teams[team_id] = team
    return team.to_dict()


@router.get("/teams/{team_id}")
async def get_team(team_id: str):
    team = _teams.get(team_id)
    if not team:
        raise HTTPException(404, f"Team not found: {team_id}")
    return team.to_dict()


@router.post("/teams/{team_id}/members")
async def add_member(team_id: str, body: AddMemberRequest):
    team = _teams.get(team_id)
    if not team:
        raise HTTPException(404, f"Team not found: {team_id}")
    team.add_member(body.agent_id)
    return team.to_dict()


@router.delete("/teams/{team_id}/members/{agent_id}")
async def remove_member(team_id: str, agent_id: str):
    team = _teams.get(team_id)
    if not team:
        raise HTTPException(404, f"Team not found: {team_id}")
    team.remove_member(agent_id)
    return team.to_dict()


@router.get("/teams/{team_id}/recommend")
async def recommend_next(team_id: str):
    team = _teams.get(team_id)
    if not team:
        raise HTTPException(404, f"Team not found: {team_id}")
    candidates = list(team._members.keys())
    recommendation = team.recommend_next(candidates)
    details = team.recommend_details(candidates)
    return {
        "team_id": team_id,
        "recommended": recommendation,
        "details": details,
        "gamma_matrix": team.compute_gamma_matrix(),
    }
