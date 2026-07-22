"""
tools/personal.py — Personal productivity tools.
Covers: Habitica, Nextcloud (calendar/tasks/notes), Home Assistant
"""

import json
import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import fmt_json, TIMEOUT, _handle_error


def register_personal_tools(mcp: FastMCP):

    # ─── HABITICA ─────────────────────────────────────────────────────────────

    def _habitica_headers() -> dict:
        return {
            "x-api-user": cfg.habitica_user_id,
            "x-api-key": cfg.habitica_api_token,
            "Content-Type": "application/json"
        }

    @mcp.tool(
        name="habitica_get_tasks",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def habitica_get_tasks() -> str:
        """Get all Habitica tasks: dailies, todos, habits with completion status."""
        if not cfg.is_configured("habitica_url", "habitica_user_id", "habitica_api_token"):
            return "Error: Habitica not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.habitica_url}/api/v4/tasks/user",
                    headers=_habitica_headers()
                )
                r.raise_for_status()
                tasks = r.json().get("data", [])

            dailies = [t for t in tasks if t["type"] == "daily"]
            todos = [t for t in tasks if t["type"] == "todo" and not t.get("completed")]
            habits = [t for t in tasks if t["type"] == "habit"]

            result = "## Habitica Tasks\n\n"

            result += f"### Dailies ({len(dailies)})\n"
            for t in dailies:
                due = "✓" if t.get("completed") else ("📅" if t.get("isDue") else "⏭")
                result += f"{due} {t.get('text')}\n"

            result += f"\n### To-Dos ({len(todos)} active)\n"
            for t in todos[:10]:
                result += f"☐ {t.get('text')}"
                if t.get("date"):
                    result += f" (due: {t['date'][:10]})"
                result += "\n"
            if len(todos) > 10:
                result += f"  ...and {len(todos) - 10} more\n"

            result += f"\n### Habits ({len(habits)})\n"
            for t in habits[:10]:
                result += f"⚡ {t.get('text')} (streak: {t.get('streak', 0)})\n"

            return result
        except Exception as e:
            return _handle_error(e, "Habitica")

    @mcp.tool(
        name="habitica_get_stats",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def habitica_get_stats() -> str:
        """Get Habitica character stats: HP, MP, XP, level, gold, class."""
        if not cfg.is_configured("habitica_url", "habitica_user_id", "habitica_api_token"):
            return "Error: Habitica not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.habitica_url}/api/v4/user",
                    headers=_habitica_headers()
                )
                r.raise_for_status()
                user = r.json().get("data", {})

            stats = user.get("stats", {})
            result = "## Habitica Stats\n\n"
            result += f"**Level {stats.get('lvl')}** {user.get('profile', {}).get('name', 'Adventurer')}\n"
            result += f"Class: {stats.get('class', 'warrior').title()}\n\n"
            result += f"❤️ HP: {stats.get('hp', 0):.1f} / {stats.get('maxHealth', 50)}\n"
            result += f"✨ MP: {stats.get('mp', 0):.1f} / {stats.get('maxMP', 0):.0f}\n"
            result += f"⭐ XP: {stats.get('exp', 0):.0f} / {stats.get('toNextLevel', 0)}\n"
            result += f"🪙 Gold: {stats.get('gp', 0):.1f}\n"
            result += f"💎 Gems: {user.get('balance', 0) * 4:.0f}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Habitica")

    class HabiticaScoreInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        task_id: str = Field(..., description="Task ID from habitica_get_tasks", min_length=1)
        direction: str = Field(default="up", description="'up' to complete/positive, 'down' to undo/negative")

    @mcp.tool(
        name="habitica_score_task",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def habitica_score_task(params: HabiticaScoreInput) -> str:
        """Score (tick off) a Habitica task. Use 'up' to complete, 'down' to undo."""
        if not cfg.is_configured("habitica_url", "habitica_user_id", "habitica_api_token"):
            return "Error: Habitica not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.habitica_url}/api/v4/tasks/{params.task_id}/score/{params.direction}",
                    headers=_habitica_headers()
                )
                r.raise_for_status()
                data = r.json().get("data", {})
                delta = data.get("delta", 0)
                return f"✓ Task scored {params.direction}. XP delta: {delta:.2f}"
        except Exception as e:
            return _handle_error(e, "Habitica")

    class HabiticaAddTodoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        text: str = Field(..., description="Task name/description", min_length=1, max_length=500)
        notes: Optional[str] = Field(default=None, description="Additional notes for the task")
        due_date: Optional[str] = Field(default=None, description="Due date in YYYY-MM-DD format")
        priority: float = Field(default=1.0, description="Priority: 0.1=trivial, 1=easy, 1.5=medium, 2=hard")

    @mcp.tool(
        name="habitica_add_todo",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def habitica_add_todo(params: HabiticaAddTodoInput) -> str:
        """Add a new To-Do item to Habitica."""
        if not cfg.is_configured("habitica_url", "habitica_user_id", "habitica_api_token"):
            return "Error: Habitica not configured."
        try:
            body = {
                "type": "todo",
                "text": params.text,
                "priority": params.priority,
            }
            if params.notes:
                body["notes"] = params.notes
            if params.due_date:
                body["date"] = params.due_date
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.habitica_url}/api/v4/tasks/user",
                    headers=_habitica_headers(),
                    json=body
                )
                r.raise_for_status()
                task = r.json().get("data", {})
                return f"✓ Added todo: '{task.get('text')}' (ID: {task.get('id')})"
        except Exception as e:
            return _handle_error(e, "Habitica")

    class HabiticaAddHabitInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        text: str = Field(..., description="Habit name", min_length=1, max_length=500)
        notes: Optional[str] = Field(default=None, description="Additional notes")
        up: bool = Field(default=True, description="Allow positive scoring")
        down: bool = Field(default=False, description="Allow negative scoring")
        priority: float = Field(default=1.0, description="Priority: 0.1=trivial, 1=easy, 1.5=medium, 2=hard")

    @mcp.tool(
        name="habitica_add_habit",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def habitica_add_habit(params: HabiticaAddHabitInput) -> str:
        """Add a new Habit to Habitica."""
        if not cfg.is_configured("habitica_url", "habitica_user_id", "habitica_api_token"):
            return "Error: Habitica not configured."
        try:
            body = {
                "type": "habit",
                "text": params.text,
                "priority": params.priority,
                "up": params.up,
                "down": params.down,
            }
            if params.notes:
                body["notes"] = params.notes
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.habitica_url}/api/v4/tasks/user",
                    headers=_habitica_headers(),
                    json=body
                )
                r.raise_for_status()
                task = r.json().get("data", {})
                return f"✓ Added habit: '{task.get('text')}' (ID: {task.get('id')})"
        except Exception as e:
            return _handle_error(e, "Habitica")

    class HabiticaDeleteInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        task_id: str = Field(..., description="Task ID (from habitica_get_tasks, or returned when the task was added)", min_length=1, max_length=64)

    @mcp.tool(
        name="habitica_delete_task",
        annotations={"readOnlyHint": False, "destructiveHint": True}
    )
    async def habitica_delete_task(params: HabiticaDeleteInput) -> str:
        """Delete a Habitica task (todo, habit, or daily) by its ID."""
        if not cfg.is_configured("habitica_url", "habitica_user_id", "habitica_api_token"):
            return "Error: Habitica not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.delete(
                    f"{cfg.habitica_url}/api/v4/tasks/{params.task_id}",
                    headers=_habitica_headers()
                )
                r.raise_for_status()
                return f"✓ Deleted Habitica task {params.task_id}"
        except Exception as e:
            return _handle_error(e, "Habitica")

    # ─── HOME ASSISTANT ───────────────────────────────────────────────────────

    def _ha_headers() -> dict:
        return {
            "Authorization": f"Bearer {cfg.ha_token}",
            "Content-Type": "application/json"
        }

    def _ha_base() -> str:
        """Return HA URL without trailing slash so f"{base}/api/..." never produces '//api/...'."""
        return (cfg.ha_url or "").rstrip("/")

    @mcp.tool(
        name="ha_get_states",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def ha_get_states() -> str:
        """Get all Home Assistant entity states grouped by domain (lights, switches, sensors, etc.)"""
        if not cfg.is_configured("ha_url", "ha_token"):
            return "Error: Home Assistant not configured. Set HA_URL and HA_TOKEN in .env"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(f"{_ha_base()}/api/states", headers=_ha_headers())
                r.raise_for_status()
                states = r.json()

            domains: dict = {}
            for entity in states:
                entity_id = entity.get("entity_id", "")
                domain = entity_id.split(".")[0]
                if domain not in domains:
                    domains[domain] = []
                domains[domain].append(entity)

            # Focus on actionable domains
            actionable = ["light", "switch", "cover", "climate", "media_player", "fan", "lock", "alarm_control_panel"]
            result = "## Home Assistant States\n\n"

            for domain in actionable:
                if domain not in domains:
                    continue
                result += f"### {domain.replace('_', ' ').title()}\n"
                for entity in domains[domain]:
                    entity_id = entity.get("entity_id")
                    state = entity.get("state")
                    friendly = entity.get("attributes", {}).get("friendly_name", entity_id)
                    result += f"  - **{friendly}** (`{entity_id}`): {state}\n"
                result += "\n"

            return result
        except Exception as e:
            return _handle_error(e, "Home Assistant")

    class HAEntityInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        entity_id: str = Field(..., description="Entity ID e.g. 'light.living_room', 'switch.desk_lamp'", min_length=1)

    @mcp.tool(
        name="ha_get_entity",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def ha_get_entity(params: HAEntityInput) -> str:
        """Get detailed state and attributes of a specific Home Assistant entity."""
        if not cfg.is_configured("ha_url", "ha_token"):
            return "Error: Home Assistant not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{_ha_base()}/api/states/{params.entity_id}",
                    headers=_ha_headers()
                )
                r.raise_for_status()
                entity = r.json()
            result = f"## {entity.get('entity_id')}\n\n"
            result += f"**State:** {entity.get('state')}\n"
            result += f"**Last changed:** {entity.get('last_changed', '')[:19]}\n\n"
            attrs = entity.get("attributes", {})
            if attrs:
                result += "**Attributes:**\n"
                for k, v in attrs.items():
                    result += f"  {k}: {v}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Home Assistant")

    class HASearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search term for entity name or ID", min_length=1, max_length=100)
        domain: Optional[str] = Field(default=None, description="Filter by domain: 'light', 'switch', 'sensor', etc.")

    @mcp.tool(
        name="ha_search_entities",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def ha_search_entities(params: HASearchInput) -> str:
        """Search for Home Assistant entities by name or domain."""
        if not cfg.is_configured("ha_url", "ha_token"):
            return "Error: Home Assistant not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(f"{_ha_base()}/api/states", headers=_ha_headers())
                r.raise_for_status()
                states = r.json()

            query = params.query.lower()
            matches = []
            for entity in states:
                entity_id = entity.get("entity_id", "")
                friendly = entity.get("attributes", {}).get("friendly_name", "").lower()
                domain = entity_id.split(".")[0]
                if params.domain and domain != params.domain:
                    continue
                if query in entity_id.lower() or query in friendly:
                    matches.append(entity)

            if not matches:
                return f"No entities found matching '{params.query}'"
            result = f"## Search: '{params.query}' ({len(matches)} results)\n\n"
            for entity in matches[:20]:
                entity_id = entity.get("entity_id")
                friendly = entity.get("attributes", {}).get("friendly_name", entity_id)
                state = entity.get("state")
                result += f"- **{friendly}** (`{entity_id}`): {state}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Home Assistant")

    class HAServiceInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        domain: str = Field(..., description="Service domain e.g. 'light', 'switch', 'homeassistant'", min_length=1)
        service: str = Field(..., description="Service name e.g. 'turn_on', 'turn_off', 'toggle'", min_length=1)
        entity_id: Optional[str] = Field(default=None, description="Target entity ID")
        service_data: Optional[dict] = Field(default=None, description="Additional service data e.g. {brightness: 255, color_temp: 4000}")

    @mcp.tool(
        name="ha_call_service",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def ha_call_service(params: HAServiceInput) -> str:
        """Call a Home Assistant service to control devices.

        Examples:
        - Turn on light: domain='light', service='turn_on', entity_id='light.living_room'
        - Turn off all lights: domain='light', service='turn_off', entity_id='all'
        - Set brightness: domain='light', service='turn_on', entity_id='light.desk', service_data={brightness_pct: 50}
        - Toggle switch: domain='switch', service='toggle', entity_id='switch.desk_lamp'
        """
        if not cfg.is_configured("ha_url", "ha_token"):
            return "Error: Home Assistant not configured."
        try:
            body = {}
            if params.entity_id:
                body["entity_id"] = params.entity_id
            if params.service_data:
                body.update(params.service_data)
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{_ha_base()}/api/services/{params.domain}/{params.service}",
                    headers=_ha_headers(),
                    json=body
                )
                r.raise_for_status()
            target = params.entity_id or "all"
            return f"✓ Called {params.domain}.{params.service} on {target}"
        except Exception as e:
            return _handle_error(e, "Home Assistant")

    @mcp.tool(
        name="ha_turn_on",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def ha_turn_on(params: HAEntityInput) -> str:
        """Turn on a Home Assistant entity (light, switch, etc.)"""
        if not cfg.is_configured("ha_url", "ha_token"):
            return "Error: Home Assistant not configured."
        try:
            domain = params.entity_id.split(".")[0]
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{_ha_base()}/api/services/{domain}/turn_on",
                    headers=_ha_headers(),
                    json={"entity_id": params.entity_id}
                )
                r.raise_for_status()
            return f"✓ Turned on {params.entity_id}"
        except Exception as e:
            return _handle_error(e, "Home Assistant")

    @mcp.tool(
        name="ha_turn_off",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def ha_turn_off(params: HAEntityInput) -> str:
        """Turn off a Home Assistant entity (light, switch, etc.)"""
        if not cfg.is_configured("ha_url", "ha_token"):
            return "Error: Home Assistant not configured."
        try:
            domain = params.entity_id.split(".")[0]
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{_ha_base()}/api/services/{domain}/turn_off",
                    headers=_ha_headers(),
                    json={"entity_id": params.entity_id}
                )
                r.raise_for_status()
            return f"✓ Turned off {params.entity_id}"
        except Exception as e:
            return _handle_error(e, "Home Assistant")
