"""Tests for ListProjectsTool / ResolveProjectTool."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from live2d_server.master_agent.contracts.tool_port import ToolContext
from live2d_server.master_agent.project_registry import Project
from live2d_server.master_agent.tools.project_tools import (
    ListProjectsTool,
    RegisterProjectTool,
    ResolveProjectTool,
)


class _FakeRegistry:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects
        self.upsert_calls: list[dict] = []
        self.upsert_exception: Exception | None = None

    def list_projects(self) -> list[Project]:
        return list(self._projects)

    def resolve(self, name: str):
        for project in self._projects:
            if name.lower() in project.all_names():
                return project
        for project in self._projects:
            if any(name.lower() in candidate for candidate in project.all_names()):
                return project
        return None

    def upsert_override(self, *, name, cwd, aliases=None, description=""):
        if self.upsert_exception:
            raise self.upsert_exception
        self.upsert_calls.append({
            "name": name, "cwd": cwd,
            "aliases": list(aliases or []), "description": description,
        })
        project = Project(
            name=name, cwd=cwd, cwds=[cwd],
            aliases=list(aliases or []), description=description,
        )
        self._projects.append(project)
        return project


def _ctx(args: dict, *, registry) -> ToolContext:
    services = SimpleNamespace(
        agent_provider=None,
        bridge_service=None,
        task_tracker=None,
        loop=None,
        permit_full_access=False,
        project_registry=registry,
    )
    return ToolContext(
        conversation_id="c1",
        arguments=args,
        services=services,
    )


class ListProjectsToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_all_projects(self) -> None:
        registry = _FakeRegistry([
            Project(name="kokoro-link", cwd="/k", cwds=["/k"]),
            Project(name="agents-stage", cwd="/a", cwds=["/a"]),
        ])
        result = await ListProjectsTool().invoke(_ctx({}, registry=registry))
        self.assertTrue(result.ok)
        names = {p["name"] for p in result.data["projects"]}
        self.assertEqual(names, {"kokoro-link", "agents-stage"})
        self.assertIn("2 project(s)", result.output_text)

    async def test_no_registry_returns_empty_success(self) -> None:
        result = await ListProjectsTool().invoke(_ctx({}, registry=None))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["projects"], [])


class ResolveProjectToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_by_name(self) -> None:
        registry = _FakeRegistry([
            Project(name="kokoro-link", cwd="/k", cwds=["/k"], aliases=["kokoro"]),
        ])
        result = await ResolveProjectTool().invoke(_ctx({"name": "kokoro"}, registry=registry))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["project"]["cwd"], "/k")

    async def test_returns_failure_when_name_missing(self) -> None:
        result = await ResolveProjectTool().invoke(_ctx({}, registry=_FakeRegistry([])))
        self.assertFalse(result.ok)

    async def test_returns_failure_when_no_match(self) -> None:
        registry = _FakeRegistry([
            Project(name="foo", cwd="/f", cwds=["/f"]),
        ])
        result = await ResolveProjectTool().invoke(_ctx({"name": "absent"}, registry=registry))
        self.assertFalse(result.ok)
        self.assertIn("list_projects", result.error)

    async def test_returns_failure_when_registry_missing(self) -> None:
        result = await ResolveProjectTool().invoke(_ctx({"name": "x"}, registry=None))
        self.assertFalse(result.ok)


class RegisterProjectToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_persists_to_registry(self) -> None:
        registry = _FakeRegistry([])
        result = await RegisterProjectTool().invoke(_ctx(
            {
                "name": "scratch",
                "cwd": "C:\\scratch",
                "aliases": ["s", "scratchpad"],
                "description": "experiments",
            },
            registry=registry,
        ))
        self.assertTrue(result.ok)
        self.assertEqual(len(registry.upsert_calls), 1)
        call = registry.upsert_calls[0]
        self.assertEqual(call["name"], "scratch")
        self.assertEqual(call["cwd"], "C:\\scratch")
        self.assertEqual(call["aliases"], ["s", "scratchpad"])
        self.assertEqual(call["description"], "experiments")

    async def test_optional_fields_default_correctly(self) -> None:
        registry = _FakeRegistry([])
        result = await RegisterProjectTool().invoke(_ctx(
            {"name": "foo", "cwd": "C:\\foo"},
            registry=registry,
        ))
        self.assertTrue(result.ok)
        call = registry.upsert_calls[0]
        self.assertEqual(call["aliases"], [])
        self.assertEqual(call["description"], "")

    async def test_returns_failure_without_name(self) -> None:
        registry = _FakeRegistry([])
        result = await RegisterProjectTool().invoke(
            _ctx({"cwd": "C:\\x"}, registry=registry),
        )
        self.assertFalse(result.ok)
        self.assertEqual(registry.upsert_calls, [])

    async def test_returns_failure_without_cwd(self) -> None:
        registry = _FakeRegistry([])
        result = await RegisterProjectTool().invoke(
            _ctx({"name": "x"}, registry=registry),
        )
        self.assertFalse(result.ok)

    async def test_returns_failure_when_registry_missing(self) -> None:
        result = await RegisterProjectTool().invoke(
            _ctx({"name": "x", "cwd": "C:\\x"}, registry=None),
        )
        self.assertFalse(result.ok)

    async def test_rejects_non_list_aliases(self) -> None:
        result = await RegisterProjectTool().invoke(_ctx(
            {"name": "x", "cwd": "C:\\x", "aliases": "not-a-list"},
            registry=_FakeRegistry([]),
        ))
        self.assertFalse(result.ok)

    async def test_surfaces_registry_errors(self) -> None:
        registry = _FakeRegistry([])
        registry.upsert_exception = RuntimeError("disk full")
        result = await RegisterProjectTool().invoke(_ctx(
            {"name": "x", "cwd": "C:\\x"},
            registry=registry,
        ))
        self.assertFalse(result.ok)
        self.assertIn("disk full", result.error)


if __name__ == "__main__":
    unittest.main()
