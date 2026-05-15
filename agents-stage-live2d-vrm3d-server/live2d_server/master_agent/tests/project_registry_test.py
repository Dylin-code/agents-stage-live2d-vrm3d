"""Tests for the ProjectRegistry that reads dev-registry/services.yaml."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from live2d_server.master_agent.project_registry import Project, ProjectRegistry


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")


class ProjectRegistryServicesYamlTest(unittest.TestCase):
    def test_groups_services_by_cwd(self) -> None:
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: kokoro-api
                    group: kokoro-link
                    cwd: C:\\path\\Kokoro-Link
                  - name: kokoro
                    group: kokoro-link
                    cwd: C:\\path\\Kokoro-Link
                  - name: kokoro-tts
                    group: kokoro-link
                    cwd: C:\\path\\Kokoro-Link
            """)
            registry = ProjectRegistry(services_path=services, override_path=Path(tmp) / "nope.yaml")
            projects = registry.list_projects()
            self.assertEqual(len(projects), 1)
            project = projects[0]
            self.assertEqual(project.name, "kokoro-link")
            self.assertEqual(project.cwd, "C:\\path\\Kokoro-Link")
            self.assertEqual(set(project.services), {"kokoro-api", "kokoro", "kokoro-tts"})

    def test_splits_shared_group_with_different_cwds(self) -> None:
        """A 'group' that maps to multiple cwds becomes multiple projects.

        Real example: ``Gentleman`` group spans MovieGentleman2.1 and
        PixGentleman — collapsing them into one entry would force the
        director to ask "which Gentleman?" every time.
        """
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: movie-gentleman
                    group: Gentleman
                    cwd: C:\\path\\MovieGentleman
                  - name: pix-gentleman
                    group: Gentleman
                    cwd: C:\\path\\PixGentleman
            """)
            registry = ProjectRegistry(services_path=services, override_path=Path(tmp) / "nope.yaml")
            projects = registry.list_projects()
            names = {p.name for p in projects}
            self.assertEqual(names, {"movie-gentleman", "pix-gentleman"})
            for project in projects:
                # The shared group survives as an alias on each split.
                self.assertIn("Gentleman", project.aliases)

    def test_skips_services_with_no_cwd(self) -> None:
        """Pure-infra services like ``ollama serve`` have no cwd → can't
        be placed on a filesystem, so they shouldn't appear as projects."""
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: ollama
                    group: ai
                  - name: real-project
                    group: real-project
                    cwd: C:\\path\\real
            """)
            registry = ProjectRegistry(services_path=services, override_path=Path(tmp) / "nope.yaml")
            names = {p.name for p in registry.list_projects()}
            self.assertEqual(names, {"real-project"})

    def test_trims_leading_whitespace_in_cwd(self) -> None:
        """Regression: a stray leading space in the YAML cwd value
        (seen in the real services.yaml) must be stripped, otherwise
        the path won't actually exist on disk."""
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: kokoro
                    group: kokoro-link
                    cwd: " C:\\\\path\\\\Kokoro-Link"
            """)
            registry = ProjectRegistry(services_path=services, override_path=Path(tmp) / "nope.yaml")
            projects = registry.list_projects()
            self.assertEqual(projects[0].cwd, "C:\\path\\Kokoro-Link")

    def test_resolve_exact_name_alias_and_substring(self) -> None:
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: kokoro-api
                    group: kokoro-link
                    cwd: C:\\path\\Kokoro-Link
            """)
            registry = ProjectRegistry(services_path=services, override_path=Path(tmp) / "nope.yaml")

            exact = registry.resolve("kokoro-link")
            assert exact is not None
            self.assertEqual(exact.name, "kokoro-link")

            via_alias = registry.resolve("kokoro-api")
            assert via_alias is not None
            self.assertEqual(via_alias.name, "kokoro-link")

            via_basename = registry.resolve("Kokoro-Link")
            assert via_basename is not None
            self.assertEqual(via_basename.name, "kokoro-link")

            via_substring = registry.resolve("kokoro")
            assert via_substring is not None
            self.assertEqual(via_substring.name, "kokoro-link")

            self.assertIsNone(registry.resolve("definitely-not-here"))
            self.assertIsNone(registry.resolve(""))

    def test_missing_services_file_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = ProjectRegistry(
                services_path=Path(tmp) / "nope.yaml",
                override_path=Path(tmp) / "also-nope.yaml",
            )
            self.assertEqual(registry.list_projects(), [])

    def test_malformed_yaml_does_not_crash(self) -> None:
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            services.write_text("this: is: not: valid: yaml: [", encoding="utf-8")
            registry = ProjectRegistry(services_path=services, override_path=Path(tmp) / "nope.yaml")
            # Should not raise; just returns empty.
            self.assertEqual(registry.list_projects(), [])


class ProjectRegistryOverrideTest(unittest.TestCase):
    def test_override_adds_new_project(self) -> None:
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: a
                    group: foo
                    cwd: C:\\path\\foo
            """)
            override = Path(tmp) / "projects.yaml"
            _write(override, """
                projects:
                  - name: scratch
                    cwd: C:\\scratch
                    aliases: [scratch-pad]
                    description: One-off experiments
            """)
            registry = ProjectRegistry(services_path=services, override_path=override)
            names = {p.name for p in registry.list_projects()}
            self.assertEqual(names, {"foo", "scratch"})

    def test_override_replaces_same_name_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: a
                    group: foo
                    cwd: C:\\path\\old
            """)
            override = Path(tmp) / "projects.yaml"
            _write(override, """
                projects:
                  - name: foo
                    cwd: C:\\path\\new
                    aliases: [override-alias]
                    description: redirected
            """)
            registry = ProjectRegistry(services_path=services, override_path=override)
            projects = registry.list_projects()
            foo = next(p for p in projects if p.name == "foo")
            self.assertEqual(foo.cwd, "C:\\path\\new")
            self.assertIn("override-alias", foo.aliases)
            self.assertEqual(foo.description, "redirected")

    def test_override_aliases_union_with_base(self) -> None:
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "services.yaml"
            _write(services, """
                services:
                  - name: srv-a
                    group: foo
                    cwd: C:\\path\\foo
                  - name: srv-b
                    group: foo
                    cwd: C:\\path\\foo
            """)
            override = Path(tmp) / "projects.yaml"
            _write(override, """
                projects:
                  - name: foo
                    cwd: C:\\path\\foo
                    aliases: [extra]
            """)
            registry = ProjectRegistry(services_path=services, override_path=override)
            foo = next(p for p in registry.list_projects() if p.name == "foo")
            self.assertIn("extra", foo.aliases)
            # Aliases derived from base (service names) survive too.
            self.assertTrue(any(a in {"srv-a", "srv-b", "foo"} for a in foo.aliases))


class ProjectRegistryUpsertTest(unittest.TestCase):
    def test_upsert_creates_file_when_missing(self) -> None:
        """A fresh deployment without ``config/master-agent/projects.yaml``
        should still let the director save its first lookup."""
        with TemporaryDirectory() as tmp:
            services = Path(tmp) / "absent.yaml"  # no dev-registry
            override = Path(tmp) / "config" / "master-agent" / "projects.yaml"
            registry = ProjectRegistry(services_path=services, override_path=override)
            project = registry.upsert_override(
                name="scratch", cwd="C:\\scratch", aliases=["s"], description="exp",
            )
            self.assertEqual(project.name, "scratch")
            self.assertTrue(override.exists())
            # Reload from disk and verify round-trip.
            reloaded = ProjectRegistry(services_path=services, override_path=override)
            projects = reloaded.list_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0].cwd, "C:\\scratch")
            self.assertIn("s", projects[0].aliases)

    def test_upsert_replaces_same_name(self) -> None:
        with TemporaryDirectory() as tmp:
            override = Path(tmp) / "projects.yaml"
            registry = ProjectRegistry(
                services_path=Path(tmp) / "nope.yaml", override_path=override,
            )
            registry.upsert_override(name="foo", cwd="C:\\old")
            registry.upsert_override(name="foo", cwd="C:\\new", description="moved")
            projects = ProjectRegistry(
                services_path=Path(tmp) / "nope.yaml", override_path=override,
            ).list_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0].cwd, "C:\\new")
            self.assertEqual(projects[0].description, "moved")

    def test_upsert_preserves_other_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            override = Path(tmp) / "projects.yaml"
            registry = ProjectRegistry(
                services_path=Path(tmp) / "nope.yaml", override_path=override,
            )
            registry.upsert_override(name="alpha", cwd="C:\\a")
            registry.upsert_override(name="beta", cwd="C:\\b")
            registry.upsert_override(name="alpha", cwd="C:\\a2")
            names_to_cwd = {
                p.name: p.cwd
                for p in ProjectRegistry(
                    services_path=Path(tmp) / "nope.yaml",
                    override_path=override,
                ).list_projects()
            }
            self.assertEqual(names_to_cwd, {"alpha": "C:\\a2", "beta": "C:\\b"})

    def test_upsert_rejects_empty_name_or_cwd(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = ProjectRegistry(
                services_path=Path(tmp) / "nope.yaml",
                override_path=Path(tmp) / "projects.yaml",
            )
            with self.assertRaises(ValueError):
                registry.upsert_override(name="", cwd="C:\\x")
            with self.assertRaises(ValueError):
                registry.upsert_override(name="x", cwd="   ")


class ProjectRegistryGracefulDegradeTest(unittest.TestCase):
    """Regression: this feature must not break deployments that don't
    use the user's private dev-registry convention."""

    def test_zero_files_yields_empty_list_no_exception(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = ProjectRegistry(
                services_path=Path(tmp) / "missing-services.yaml",
                override_path=Path(tmp) / "missing-projects.yaml",
            )
            # Two calls in a row — second-call cache / re-read mustn't
            # accidentally raise.
            self.assertEqual(registry.list_projects(), [])
            self.assertEqual(registry.list_projects(), [])
            self.assertIsNone(registry.resolve("anything"))


class ProjectAllNamesTest(unittest.TestCase):
    def test_all_names_includes_cwd_basename(self) -> None:
        project = Project(
            name="foo",
            cwd="C:\\path\\to\\MyRepo",
            cwds=["C:\\path\\to\\MyRepo"],
            aliases=["bar"],
        )
        names = project.all_names()
        self.assertIn("foo", names)
        self.assertIn("bar", names)
        self.assertIn("myrepo", names)


if __name__ == "__main__":
    unittest.main()
