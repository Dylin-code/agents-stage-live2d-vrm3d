"""Project registry — what cwd each project lives at.

The director shouldn't have to ask "where is Kokoro-Link?" every time.
This module reads the user's existing dev-registry (``services.yaml``)
and exposes a flat list of projects keyed by name → cwd, plus aliases
for fuzzy lookup.

Data sources
------------

1. **Primary** — ``~/.config/dev-registry/services.yaml`` (the user's
   own convention, see CLAUDE.md). Services are grouped by their
   ``group`` field, and each group becomes one :class:`Project` with
   one or more cwds. Services without a cwd (pure infra like
   ``ollama serve``) are skipped.

2. **Override** — ``config/master-agent/projects.yaml`` (optional).
   Schema is the same as :class:`Project.to_dict`. Override entries
   merged on top of dev-registry results — same ``name`` replaces;
   new ``name`` adds. Use this for one-off projects that don't have
   a dev-registry service yet.

Both files are read on each call to :meth:`ProjectRegistry.list_projects`
so YAML edits take effect without restarting the master agent.

The registry is *read-only* from the master agent's point of view —
adding/editing entries happens out-of-band (user edits YAML). A
write-back path is intentionally not provided to avoid duelling sources
of truth.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Project:
    """A development project the director can dispatch work into."""

    name: str
    cwd: str                       # canonical cwd to use for new sessions
    cwds: list[str] = field(default_factory=list)  # all known cwds (rare: >1)
    aliases: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cwd": self.cwd,
            "cwds": list(self.cwds),
            "aliases": list(self.aliases),
            "services": list(self.services),
            "description": self.description,
        }

    def all_names(self) -> list[str]:
        """Return ``name`` + ``aliases`` lowercased, for matching."""
        out = [self.name.lower()]
        out.extend(a.lower() for a in self.aliases if a)
        # The cwd's last component is often what the user calls the
        # project ("the Kokoro-Link folder"), so accept that too.
        if self.cwd:
            basename = Path(self.cwd).name.lower()
            if basename and basename not in out:
                out.append(basename)
        return out


class ProjectRegistry:
    """Read-on-demand registry that merges dev-registry + override file."""

    def __init__(
        self,
        *,
        services_path: Optional[Path] = None,
        override_path: Optional[Path] = None,
    ) -> None:
        self._services_path = services_path or _resolve_services_path()
        self._override_path = override_path or _resolve_override_path()

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def services_path(self) -> Path:
        return self._services_path

    @property
    def override_path(self) -> Path:
        return self._override_path

    def list_projects(self) -> list[Project]:
        """Return all known projects, dev-registry first then overrides.

        Sorted by name (case-insensitive) for stable prompt output.
        Empty list if neither source exists or yields nothing usable.
        """
        by_name: dict[str, Project] = {}
        for project in self._load_from_services_yaml():
            by_name[project.name.lower()] = project
        for project in self._load_from_override():
            key = project.name.lower()
            existing = by_name.get(key)
            if existing is None:
                by_name[key] = project
            else:
                by_name[key] = _merge(existing, project)
        return sorted(by_name.values(), key=lambda p: p.name.lower())

    def upsert_override(
        self,
        *,
        name: str,
        cwd: str,
        aliases: Optional[Iterable[str]] = None,
        description: str = "",
    ) -> Project:
        """Persist a project entry into the override yaml.

        Used by ``register_project`` so the director can teach itself a
        new project once and have it survive across conversations and
        server restarts. The override file is private to this checkout
        (under ``config/master-agent/``) — clones get a clean slate, so
        a co-worker pulling this repo won't inherit your local map.

        Same ``name`` → update in place. New ``name`` → append. Other
        existing entries are preserved verbatim.
        """
        clean_name = (name or "").strip()
        clean_cwd = (cwd or "").strip()
        if not clean_name:
            raise ValueError("name is required")
        if not clean_cwd:
            raise ValueError("cwd is required")
        clean_aliases = [a.strip() for a in (aliases or []) if a and a.strip()]
        clean_description = (description or "").strip()

        # Re-read the override file each call so concurrent edits (user
        # hand-editing while the director adds entries) don't trample
        # each other beyond last-write-wins on a per-name basis.
        data = _read_yaml(self._override_path)
        if not isinstance(data, dict):
            data = {}
        raw_projects = data.get("projects")
        if not isinstance(raw_projects, list):
            raw_projects = []

        entries: list[dict[str, Any]] = []
        replaced = False
        for item in raw_projects:
            if not isinstance(item, dict):
                continue
            existing_name = str(item.get("name") or "").strip()
            if existing_name.lower() == clean_name.lower():
                replaced = True
                entries.append(_compose_override_entry(
                    name=clean_name,
                    cwd=clean_cwd,
                    aliases=clean_aliases,
                    description=clean_description,
                ))
                continue
            entries.append(item)
        if not replaced:
            entries.append(_compose_override_entry(
                name=clean_name,
                cwd=clean_cwd,
                aliases=clean_aliases,
                description=clean_description,
            ))

        payload = {**data, "projects": entries}
        _write_yaml_atomic(self._override_path, payload)
        return Project(
            name=clean_name,
            cwd=clean_cwd,
            cwds=[clean_cwd],
            aliases=clean_aliases,
            description=clean_description,
        )

    def resolve(self, name_or_alias: str) -> Optional[Project]:
        """Fuzzy lookup: exact name / alias / cwd basename, case-insensitive.

        Falls back to substring match when no exact hit. Returns the
        first match by sort order so behavior is deterministic.
        """
        needle = (name_or_alias or "").strip().lower()
        if not needle:
            return None
        projects = self.list_projects()
        # Exact match across all_names() first.
        for project in projects:
            if needle in project.all_names():
                return project
        # Substring fallback.
        for project in projects:
            if any(needle in candidate for candidate in project.all_names()):
                return project
        return None

    # ------------------------------------------------------------------
    # Source-specific loaders
    # ------------------------------------------------------------------

    def _load_from_services_yaml(self) -> list[Project]:
        """Project identity = unique cwd. Group is treated as an alias
        when it cleanly maps to one cwd, otherwise demoted so multiple
        projects sharing a category name don't collide.

        Example: ``Gentleman`` group spans two cwds (MovieGentleman2.1
        and PixGentleman); both surface as separate projects named
        after their service (``movie-gentleman``, ``pix-gentleman``)
        with ``Gentleman`` kept as an alias on each.
        """
        data = _read_yaml(self._services_path)
        if not isinstance(data, dict):
            return []
        raw_services = data.get("services")
        if not isinstance(raw_services, list):
            return []

        # First pass: collect per-cwd service descriptors.
        per_cwd: dict[str, list[dict[str, Any]]] = {}
        # Second pass needs to know which groups span multiple cwds.
        group_cwds: dict[str, set[str]] = {}
        for item in raw_services:
            if not isinstance(item, dict):
                continue
            cwd = _clean_cwd(item.get("cwd"))
            if not cwd:
                # No cwd → not a placeable project (ollama, lmstudio, ...).
                continue
            group = str(item.get("group") or "").strip()
            service = str(item.get("name") or "").strip()
            per_cwd.setdefault(cwd, []).append({
                "service": service,
                "group": group,
            })
            if group:
                group_cwds.setdefault(group, set()).add(cwd)

        projects: list[Project] = []
        for cwd, entries in per_cwd.items():
            # The group(s) seen at this cwd — usually one.
            local_groups = [e["group"] for e in entries if e["group"]]
            unique_groups = list(dict.fromkeys(local_groups))  # preserve order, dedupe
            service_names = [e["service"] for e in entries if e["service"]]

            # Pick the friendliest name:
            # - if a single group lives only at THIS cwd, use it
            # - else fall back to the first service name (more specific)
            # - else cwd basename as last resort
            name = ""
            if len(unique_groups) == 1:
                only_group = unique_groups[0]
                if len(group_cwds.get(only_group, set())) == 1:
                    name = only_group
            if not name and service_names:
                name = service_names[0]
            if not name:
                name = Path(cwd).name

            # Build alias set: everything users might call this project
            # by, minus whatever we picked as the canonical name.
            alias_candidates: list[str] = []
            alias_candidates.extend(unique_groups)
            alias_candidates.extend(service_names)
            basename = Path(cwd).name
            if basename:
                alias_candidates.append(basename)
            aliases: list[str] = []
            seen_alias = {name.lower()}
            for candidate in alias_candidates:
                key = candidate.lower()
                if key in seen_alias:
                    continue
                seen_alias.add(key)
                aliases.append(candidate)

            projects.append(Project(
                name=name,
                cwd=cwd,
                cwds=[cwd],
                aliases=aliases,
                services=service_names,
            ))
        return projects

    def _load_from_override(self) -> list[Project]:
        data = _read_yaml(self._override_path)
        if not isinstance(data, dict):
            return []
        raw_projects = data.get("projects")
        if not isinstance(raw_projects, list):
            return []
        out: list[Project] = []
        for item in raw_projects:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            cwd = _clean_cwd(item.get("cwd"))
            if not name or not cwd:
                continue
            out.append(Project(
                name=name,
                cwd=cwd,
                cwds=[cwd],
                aliases=[str(a).strip() for a in (item.get("aliases") or []) if a],
                services=[str(s).strip() for s in (item.get("services") or []) if s],
                description=str(item.get("description") or "").strip(),
            ))
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_services_path() -> Path:
    env = (os.getenv("DEV_REGISTRY_SERVICES_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "dev-registry" / "services.yaml"


def _resolve_override_path() -> Path:
    env = (os.getenv("MASTER_AGENT_PROJECTS_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "config" / "master-agent" / "projects.yaml"


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        _LOGGER.warning("project registry: failed to read %s — %s", path, exc)
        return None


def _clean_cwd(raw: Any) -> str:
    """Trim whitespace and normalize separators.

    The user's services.yaml occasionally has stray leading spaces
    inside the quoted value (e.g. ``cwd: " C:\\Users..."``); strip
    those so resolved paths actually exist.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    return text


def _compose_override_entry(
    *,
    name: str,
    cwd: str,
    aliases: list[str],
    description: str,
) -> dict[str, Any]:
    """Build the YAML-friendly dict for one project entry.

    Drops empty optional fields so the override file stays tidy when a
    user inspects it by hand.
    """
    entry: dict[str, Any] = {"name": name, "cwd": cwd}
    if aliases:
        entry["aliases"] = list(aliases)
    if description:
        entry["description"] = description
    return entry


def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write YAML via tempfile + replace so a crash mid-write can't
    leave a half-written override file. Mirrors the persona / conversation
    store's atomicity pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _merge(base: Project, overlay: Project) -> Project:
    """Layer ``overlay`` on top of ``base`` for same-name projects.

    Override file gets the last word on ``cwd`` and ``description``;
    ``aliases`` and ``services`` are unioned (override wins ordering).
    """
    merged_aliases = list(overlay.aliases)
    for alias in base.aliases:
        if alias not in merged_aliases:
            merged_aliases.append(alias)
    merged_services = list(overlay.services or base.services)
    cwds = list(overlay.cwds or base.cwds)
    if not cwds:
        cwds = [base.cwd or overlay.cwd]
    return Project(
        name=overlay.name or base.name,
        cwd=overlay.cwd or base.cwd,
        cwds=cwds,
        aliases=merged_aliases,
        services=merged_services,
        description=overlay.description or base.description,
    )


__all__ = ["Project", "ProjectRegistry"]
