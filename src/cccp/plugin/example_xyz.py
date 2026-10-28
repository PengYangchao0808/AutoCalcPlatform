"""Minimal read-only XYZ parser plugin — the SDK example (plan T45).

This is a real, deterministic parser for the plain XYZ structure format.  It
reads a file and returns a built-in
:class:`~cccp.calculation.results.TaskResult`; it launches no program, imports
no platform code and never creates a second task chain.  The plugin is
disabled by default, like every plugin.

The example exists to demonstrate the whole SDK surface in one small,
auditable file:

* the built-in ``TaskRequest`` / ``TaskResult`` / ``TaskContext`` envelopes;
* explicit coordinate units and input atom-order mapping;
* partial output: a truncated structure keeps the valid atom prefix and sets
  ``complete=False`` with a typed ``parse_failure``;
* cooperative cancellation and timeout checks between atom lines.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import JsonObject, Provenance
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import TaskRequest
from cccp.calculation.results import ErrorKind, TaskResult
from cccp.plugin.discovery import PluginCandidate
from cccp.plugin.errors import PluginUnavailableError
from cccp.plugin.metadata import (
    ApiCompatibility,
    ErrorSemantics,
    PluginCapability,
    PluginMetadata,
    ResourceRequirements,
)
from cccp.plugin.sdk import (
    UNIT_ANGSTROM,
    PluginOutputSpec,
    check_cancelled,
    check_deadline,
)
from cccp.version import __version__

EXAMPLE_PLUGIN_NAME = "xyz_readonly_parser"
EXAMPLE_CAPABILITY_NAME = "xyz_readonly_parser"
EXAMPLE_SCHEMA = "xyz_structure"
EXAMPLE_SCHEMA_VERSION = 1

EXAMPLE_OUTPUT_SPEC = PluginOutputSpec(
    units=(("coordinates", UNIT_ANGSTROM),),
    atom_mapping="input_order",
    allows_partial=True,
)


def example_metadata() -> PluginMetadata:
    """Return the frozen metadata record for the example plugin."""
    return PluginMetadata(
        name=EXAMPLE_PLUGIN_NAME,
        distribution="cccp",
        version=__version__,
        api=ApiCompatibility(),
        capabilities=(
            PluginCapability(
                name=EXAMPLE_CAPABILITY_NAME,
                schema=EXAMPLE_SCHEMA,
                schema_version=EXAMPLE_SCHEMA_VERSION,
            ),
        ),
        science_software=(),
        resources=ResourceRequirements(requires_subprocess=False, requires_network=False),
        license="MIT",
        dependencies=(),
        error_semantics=ErrorSemantics(
            pre_launch_typed=True,
            partial_output_tolerant=True,
            never_overrides_builtin=True,
        ),
        default_enabled=False,
    )


class XyzReadonlyParserPlugin:
    """Read-only XYZ parser served through the plugin SDK."""

    output_spec = EXAMPLE_OUTPUT_SPEC

    def capability_names(self) -> tuple[str, ...]:
        """Return the capability this handler serves."""
        return (EXAMPLE_CAPABILITY_NAME,)

    def output_spec_for(self, capability: str) -> PluginOutputSpec:
        """Return the output contract for *capability*."""
        if capability != EXAMPLE_CAPABILITY_NAME:
            raise PluginUnavailableError(
                EXAMPLE_PLUGIN_NAME, reason=f"capability {capability!r} is not served"
            )
        return self.output_spec

    def invoke(
        self,
        request: TaskRequest,
        *,
        context: TaskContext | None = None,
    ) -> TaskResult:
        """Parse the XYZ file referenced by ``request.structure.path``."""
        started_at = time.monotonic()
        check_deadline(EXAMPLE_PLUGIN_NAME, started_at, context)
        check_cancelled(context)
        structure = request.structure
        if structure is None or structure.path is None:
            raise TaskInputError("xyz_readonly_parser requires structure.path")
        try:
            text = Path(structure.path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise TaskInputError(
                f"xyz_readonly_parser could not read {structure.path}: {exc}"
            ) from exc
        check_deadline(EXAMPLE_PLUGIN_NAME, started_at, context)
        return _parse_xyz_text(
            text,
            request=request,
            context=context,
            started_at=started_at,
        )


def _parse_xyz_text(
    text: str,
    *,
    request: TaskRequest,
    context: TaskContext | None,
    started_at: float,
) -> TaskResult:
    """Parse plain XYZ *text* and return a built-in result envelope."""
    lines = text.splitlines()
    index = _next_non_empty(lines, 0)
    if index is None:
        return _result(
            request,
            text=text,
            declared_count=0,
            atoms=(),
            comment="",
            errors=("empty XYZ input",),
        )
    try:
        declared_count = int(lines[index].strip())
    except ValueError:
        return _result(
            request,
            text=text,
            declared_count=0,
            atoms=(),
            comment="",
            errors=(f"XYZ atom count must be an integer, got {lines[index].strip()!r}",),
        )
    if declared_count < 0:
        return _result(
            request,
            text=text,
            declared_count=declared_count,
            atoms=(),
            comment="",
            errors=(f"XYZ atom count must be >= 0, got {declared_count}",),
        )
    index += 1
    comment = lines[index].strip() if index < len(lines) else ""
    index += 1

    atoms: list[tuple[str, tuple[float, float, float]]] = []
    errors: list[str] = []
    while index < len(lines) and len(atoms) < declared_count:
        check_cancelled(context)
        check_deadline(EXAMPLE_PLUGIN_NAME, started_at, context)
        line = lines[index]
        index += 1
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 4:
            errors.append(f"atom line {len(atoms) + 1} has {len(fields)} field(s), expected 4")
            continue
        try:
            coordinates = (
                float(fields[1]),
                float(fields[2]),
                float(fields[3]),
            )
        except ValueError:
            errors.append(f"atom line {len(atoms) + 1} has non-numeric coordinates")
            continue
        atoms.append((fields[0], coordinates))

    if len(atoms) < declared_count:
        errors.append(f"truncated: expected {declared_count} atom(s), parsed {len(atoms)}")
    trailing = [line for line in lines[index:] if line.strip()]
    if trailing:
        errors.append(f"unexpected {len(trailing)} trailing line(s) after {declared_count} atom(s)")
    return _result(
        request,
        text=text,
        declared_count=declared_count,
        atoms=tuple(atoms),
        comment=comment,
        errors=tuple(errors),
    )


def _next_non_empty(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        if lines[index].strip():
            return index
    return None


def _result(
    request: TaskRequest,
    *,
    text: str,
    declared_count: int,
    atoms: tuple[tuple[str, tuple[float, float, float]], ...],
    comment: str,
    errors: tuple[str, ...],
) -> TaskResult:
    """Build the complete/partial result from the parsed atom prefix."""
    complete = not errors and len(atoms) == declared_count
    symbols = tuple(symbol for symbol, _coordinates in atoms)
    coordinates = tuple(coordinates for _symbol, coordinates in atoms)
    metadata: JsonObject = {
        "capability": EXAMPLE_CAPABILITY_NAME,
        "units": {"coordinates": UNIT_ANGSTROM},
        "atom_order": list(symbols),
        "source_format": "xyz",
        "declared_atom_count": declared_count,
        "comment": comment,
        "parser_version": __version__,
    }
    return TaskResult(
        task=request.task,
        status="completed" if complete else "failed",
        complete=complete,
        error_kind=None if complete else ErrorKind.PARSE_FAILURE,
        errors=() if complete else errors,
        coordinates=coordinates,
        symbols=symbols,
        metadata=metadata,
        provenance=Provenance(
            backend="readonly_parser",
            method="xyz",
            version=__version__,
            input_signature=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ),
    )


def _build_example() -> XyzReadonlyParserPlugin:
    return XyzReadonlyParserPlugin()


#: The example candidate.  Its loader runs only after an explicit
#: ``PluginHost.enable(EXAMPLE_PLUGIN_NAME)`` followed by ``load``.
EXAMPLE_CANDIDATE = PluginCandidate(metadata=example_metadata(), loader=_build_example)


__all__ = [
    "EXAMPLE_CANDIDATE",
    "EXAMPLE_CAPABILITY_NAME",
    "EXAMPLE_OUTPUT_SPEC",
    "EXAMPLE_PLUGIN_NAME",
    "EXAMPLE_SCHEMA",
    "EXAMPLE_SCHEMA_VERSION",
    "XyzReadonlyParserPlugin",
    "example_metadata",
]
