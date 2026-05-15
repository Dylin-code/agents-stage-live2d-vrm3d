"""Tests for persona injection into the master-agent system prompt."""

from __future__ import annotations

import unittest

from live2d_server.master_agent.persona import PersonaConfig, default_persona
from live2d_server.master_agent.prompt_builder import build_system_prompt


class PersonaInjectionTest(unittest.TestCase):
    def test_no_persona_argument_keeps_legacy_prompt(self) -> None:
        """Backward compat: existing callers don't have to pass a persona."""
        prompt = build_system_prompt()
        self.assertNotIn("=== Persona ===", prompt)
        # Must still mention the canonical role line.
        self.assertIn("master controller", prompt)

    def test_disabled_persona_is_dropped_entirely(self) -> None:
        marker = "ZZ_PERSONA_MARKER_ZZ"
        prompt = build_system_prompt(persona=PersonaConfig(
            enabled=False,
            display_name=marker,
            summary=marker,
        ))
        self.assertNotIn("=== Persona ===", prompt)
        self.assertNotIn(marker, prompt)

    def test_enabled_persona_renders_display_name_at_top(self) -> None:
        prompt = build_system_prompt(persona=PersonaConfig(
            enabled=True,
            display_name="導演",
            summary="統籌舞台",
            personality=["沉穩", "鏡頭感"],
            speaking_style="導演視角",
            catchphrase="場記開始──",
            boundaries=["不假裝寫程式碼"],
        ))
        # Persona block must come before the mechanical instructions.
        persona_idx = prompt.index("=== Persona ===")
        mechanical_idx = prompt.index("master controller")
        self.assertLess(persona_idx, mechanical_idx)
        # All fields surface in the prompt text.
        self.assertIn("導演", prompt)
        self.assertIn("統籌舞台", prompt)
        self.assertIn("沉穩", prompt)
        self.assertIn("導演視角", prompt)
        self.assertIn("場記開始", prompt)
        self.assertIn("不假裝寫程式碼", prompt)

    def test_persona_block_walls_off_tool_calling_format(self) -> None:
        """The persona block must explicitly tell the LLM that tool JSON
        is NOT styled by the persona — otherwise a chatty persona could
        break tool-call parsing."""
        prompt = build_system_prompt(persona=default_persona())
        # Look for the wording that draws the line.
        self.assertIn("工具呼叫", prompt)
        self.assertIn("schema", prompt)

    def test_empty_optional_fields_are_skipped(self) -> None:
        prompt = build_system_prompt(persona=PersonaConfig(
            enabled=True,
            display_name="X",
            # Everything else blank.
        ))
        self.assertIn("=== Persona ===", prompt)
        self.assertIn("X", prompt)
        self.assertNotIn("角色簡介:", prompt)
        self.assertNotIn("性格:", prompt)
        self.assertNotIn("說話風格:", prompt)
        self.assertNotIn("口頭禪", prompt)
        self.assertNotIn("界線", prompt)


if __name__ == "__main__":
    unittest.main()
