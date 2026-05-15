"""Snapshot-style guards for the master-agent system prompt.

We don't pin the full prompt text (it evolves), but we DO pin the
critical rules so a future edit can't accidentally drop them. Each rule
maps to a behavior the runtime relies on:

- confirmation gate before ``*_new_session``
- ``#full`` keyword gating ``permission_mode=full``
- always pair ``wait_for_subtask`` after ``*_send_prompt``
- relay worker's actual ``final_text`` in ``report_to_user``
"""

from __future__ import annotations

import unittest

from live2d_server.master_agent.prompt_builder import build_system_prompt


class SystemPromptGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.prompt = build_system_prompt()

    def test_confirmation_gate_before_new_session(self) -> None:
        self.assertIn("CONFIRMATION GATE", self.prompt)
        self.assertIn("codex_new_session", self.prompt)
        self.assertIn("claude_new_session", self.prompt)
        # Lists out the params that must be shown to the user.
        for keyword in (
            "agent_brand", "cwd", "model", "reasoning_effort",
            "permission_mode", "plan_mode",
        ):
            self.assertIn(keyword, self.prompt, f"missing {keyword!r} in gate")

    def test_gate_does_not_apply_to_existing_session_resume(self) -> None:
        """Resume / send_prompt to an existing session must be explicitly
        exempt so the LLM doesn't ask for confirmation on every turn."""
        self.assertIn("Resume / send_prompt to an EXISTING session_id", self.prompt)
        self.assertIn("NOT gated", self.prompt)

    def test_full_permission_mode_gated_by_user_keyword(self) -> None:
        self.assertIn("#full", self.prompt)
        # Loose check that the prompt explains downgrade behavior.
        self.assertTrue(
            "downgraded" in self.prompt or "downgrade" in self.prompt,
            "prompt should mention the full→auto downgrade",
        )

    def test_wait_then_report_flow_documented(self) -> None:
        self.assertIn("wait_for_subtask", self.prompt)
        self.assertIn("final_text", self.prompt)


if __name__ == "__main__":
    unittest.main()
