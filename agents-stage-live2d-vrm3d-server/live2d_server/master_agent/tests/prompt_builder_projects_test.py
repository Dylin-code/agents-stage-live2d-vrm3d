"""Tests for project list injection into the system prompt."""

from __future__ import annotations

import unittest

from live2d_server.master_agent.project_registry import Project
from live2d_server.master_agent.prompt_builder import build_system_prompt


class ProjectsInjectionTest(unittest.TestCase):
    def test_no_projects_shows_learn_hint(self) -> None:
        """Even without dev-registry, the prompt tells the LLM that
        ``register_project`` exists so the user's first project name
        gets remembered for next time."""
        prompt = build_system_prompt()
        self.assertNotIn("Known projects", prompt)
        self.assertIn("register_project", prompt)

    def test_empty_projects_shows_learn_hint(self) -> None:
        prompt = build_system_prompt(projects=())
        self.assertNotIn("Known projects", prompt)
        self.assertIn("register_project", prompt)

    def test_one_project_renders_a_line(self) -> None:
        prompt = build_system_prompt(projects=[
            Project(name="kokoro-link", cwd="C:\\path\\Kokoro-Link", cwds=["C:\\path\\Kokoro-Link"]),
        ])
        self.assertIn("Known projects", prompt)
        self.assertIn("kokoro-link", prompt)
        self.assertIn("C:\\path\\Kokoro-Link", prompt)

    def test_aliases_surface_in_brackets(self) -> None:
        prompt = build_system_prompt(projects=[
            Project(
                name="agents-stage",
                cwd="C:\\repos\\agents-stage",
                cwds=["C:\\repos\\agents-stage"],
                aliases=["stage", "ASLV"],
            ),
        ])
        self.assertIn("aliases:", prompt)
        self.assertIn("stage", prompt)
        self.assertIn("ASLV", prompt)

    def test_block_appears_after_base_instructions(self) -> None:
        """The project list sits below the mechanical instructions so
        the LLM reads the rules first, then the lookup table."""
        prompt = build_system_prompt(projects=[
            Project(name="foo", cwd="/x", cwds=["/x"]),
        ])
        base_idx = prompt.index("EXECUTION MODEL")
        projects_idx = prompt.index("Known projects")
        self.assertLess(base_idx, projects_idx)


if __name__ == "__main__":
    unittest.main()
