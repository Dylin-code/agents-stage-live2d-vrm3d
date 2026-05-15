"""Tests for PersonaConfig / FilePersonaStore + persona presets."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from live2d_server.master_agent.persona import (
    DEFAULT_DISPLAY_NAME,
    FilePersonaStore,
    PersonaConfig,
    PersonaStore,
    default_persona,
)
from live2d_server.master_agent.persona_presets import (
    PRESET_IDS,
    get_preset,
    list_presets,
)


class PersonaConfigTest(unittest.TestCase):
    def test_normalized_strips_whitespace_and_drops_empties(self) -> None:
        p = PersonaConfig(
            enabled=True,
            display_name="  導演  ",
            summary="  hi  ",
            personality=["", "  沉穩  ", "  ", "鏡頭感"],
            speaking_style="  brief  ",
            catchphrase="  go  ",
            boundaries=["  no炫技  ", ""],
        ).normalized()
        self.assertEqual(p.display_name, "導演")
        self.assertEqual(p.summary, "hi")
        self.assertEqual(p.personality, ["沉穩", "鏡頭感"])
        self.assertEqual(p.speaking_style, "brief")
        self.assertEqual(p.catchphrase, "go")
        self.assertEqual(p.boundaries, ["no炫技"])

    def test_normalized_falls_back_to_default_name_when_blank(self) -> None:
        p = PersonaConfig(display_name="   ").normalized()
        self.assertEqual(p.display_name, DEFAULT_DISPLAY_NAME)

    def test_to_dict_round_trip(self) -> None:
        original = PersonaConfig(
            enabled=False,
            display_name="X",
            personality=["a", "b"],
            speaking_style="s",
            catchphrase="c",
            boundaries=["b1"],
        )
        round_trip = PersonaConfig.from_dict(original.to_dict())
        self.assertIsNotNone(round_trip)
        assert round_trip is not None
        self.assertEqual(round_trip, original)

    def test_from_dict_returns_none_for_garbage(self) -> None:
        self.assertIsNone(PersonaConfig.from_dict("not a dict"))
        self.assertIsNone(PersonaConfig.from_dict(None))


class PersonaPresetsTest(unittest.TestCase):
    def test_built_in_presets_cover_expected_ids(self) -> None:
        # If you add or rename a preset, update this assertion intentionally.
        self.assertEqual(
            set(PRESET_IDS),
            {"director", "calm-assistant", "fellow-coder", "tool-only"},
        )

    def test_default_persona_matches_director_preset(self) -> None:
        director = get_preset("director")
        assert director is not None
        self.assertEqual(default_persona(), director)

    def test_tool_only_preset_is_disabled(self) -> None:
        preset = get_preset("tool-only")
        assert preset is not None
        self.assertFalse(preset.enabled)

    def test_list_presets_exposes_metadata_for_each(self) -> None:
        entries = list_presets()
        self.assertEqual(len(entries), len(PRESET_IDS))
        for entry in entries:
            self.assertIn("id", entry)
            self.assertIn("display_name", entry)
            self.assertIn("summary", entry)
            self.assertIn("enabled", entry)

    def test_get_preset_returns_a_fresh_copy(self) -> None:
        """Caller mutation must not bleed into the module-level constants."""
        first = get_preset("director")
        assert first is not None
        first.personality.append("MUTATED")
        second = get_preset("director")
        assert second is not None
        self.assertNotIn("MUTATED", second.personality)


class PersonaStoreInMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_starts_with_default_persona(self) -> None:
        store = PersonaStore()
        persona = await store.get()
        self.assertTrue(persona.enabled)
        self.assertEqual(persona.display_name, DEFAULT_DISPLAY_NAME)

    async def test_set_normalizes_input(self) -> None:
        store = PersonaStore()
        result = await store.set(PersonaConfig(display_name="  X  ", personality=[" a "]))
        self.assertEqual(result.display_name, "X")
        self.assertEqual(result.personality, ["a"])

    async def test_reset_restores_default(self) -> None:
        store = PersonaStore()
        await store.set(PersonaConfig(enabled=False, display_name="X"))
        restored = await store.reset_to_default()
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.display_name, DEFAULT_DISPLAY_NAME)

    async def test_get_returns_a_copy_not_the_stored_object(self) -> None:
        store = PersonaStore()
        first = await store.get()
        first.personality.append("MUTATED")
        second = await store.get()
        self.assertNotIn("MUTATED", second.personality)


class FilePersonaStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_load_creates_default(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "persona.json"
            store = FilePersonaStore(path)
            persona = await store.get()
            self.assertEqual(persona.display_name, DEFAULT_DISPLAY_NAME)

    async def test_set_persists_to_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "persona.json"
            store_a = FilePersonaStore(path)
            await store_a.set(PersonaConfig(display_name="阿凱", personality=["熱血"]))

            # Re-open from a fresh instance to confirm persistence.
            store_b = FilePersonaStore(path)
            persona = await store_b.get()
            self.assertEqual(persona.display_name, "阿凱")
            self.assertEqual(persona.personality, ["熱血"])

    async def test_corrupted_file_falls_back_to_default(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "persona.json"
            path.write_text("not valid json", encoding="utf-8")
            store = FilePersonaStore(path)
            persona = await store.get()
            self.assertEqual(persona.display_name, DEFAULT_DISPLAY_NAME)

    async def test_disabled_persona_persists(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "persona.json"
            store = FilePersonaStore(path)
            await store.set(PersonaConfig(enabled=False, display_name="總控 Agent"))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(data["enabled"])


if __name__ == "__main__":
    unittest.main()
