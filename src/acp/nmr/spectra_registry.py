# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Per-nucleus spectrum-processor registry (todo 45 handoff, gap G10).

Maps an element / nucleus label to the processor descriptor published by the
todo-43/44 processor modules (``carbon_processor``, ``proton_processor``).
The processor modules are imported **lazily** by :func:`lookup_processor` and
:func:`processor_registry`: merely importing this registry never imports the
processor modules, so their dependencies and import cost stay out of the
spectrum-selection path until a nucleus is actually requested.

Descriptor sources (single source of truth): the module's ``NUCLEUS`` /
``PROCESSOR_ID`` constants plus its callable entry point — either a
``get_processor()`` singleton exposing ``.process`` (todo-43 carbon style) or
a module-level ``process_*`` function (todo-44 proton style). Lookups are
explicit and typed: an unknown nucleus yields ``None`` from
:func:`lookup_processor` and raises :class:`NucleusProcessorError` (reason
``unknown_nucleus``) from :func:`processor_for`; an importable module whose
descriptor is incomplete raises the same typed error with the matching
reason.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from acp.nmr.models import element_of_nucleus, normalize_symbol, nucleus_label

logger = logging.getLogger(__name__)

#: Element -> processor module (the registry is the explicit whitelist).
_PROCESSOR_MODULES: dict[str, str] = {
    "C": "acp.nmr.carbon_processor",
    "H": "acp.nmr.proton_processor",
}

#: Closed vocabulary of typed registry failures.
PROCESSOR_ERROR_REASONS: tuple[str, ...] = (
    "unknown_nucleus",
    "processor_import_failed",
    "processor_descriptor_missing",
    "trace_not_supported",
)


class NucleusProcessorError(ValueError):
    """Typed per-nucleus processor registry failure.

    Attributes:
        reason: Closed-vocabulary code (:data:`PROCESSOR_ERROR_REASONS`).
        detail: Human-readable context (module, nucleus, available choices).
    """

    def __init__(self, reason: str, detail: str) -> None:
        if reason not in PROCESSOR_ERROR_REASONS:
            raise ValueError(
                f"unknown processor error reason {reason!r}; expected {PROCESSOR_ERROR_REASONS}"
            )
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class ProcessorDescriptor:
    """One registered per-nucleus spectrum processor.

    Attributes:
        element: Normalized element symbol (``"C"`` / ``"H"``).
        nucleus_label: Canonical nucleus label (``"13C"`` / ``"1H"``).
        processor_id: Processor version id published by the module.
        module: Dotted module path the descriptor was read from.
        entry: Module attribute resolving the entry point — a
            ``get_processor`` singleton factory or a ``process_*`` callable.
        uses_singleton: ``True`` when :attr:`entry` names a factory whose
            result exposes ``.process`` (todo-43 carbon style).
    """

    element: str
    nucleus_label: str
    processor_id: str
    module: str
    entry: str
    uses_singleton: bool

    def to_dict(self) -> dict[str, object]:
        """JSON-safe registration descriptor."""
        return {
            "element": self.element,
            "nucleus_label": self.nucleus_label,
            "processor_id": self.processor_id,
            "module": self.module,
            "entry": self.entry,
            "uses_singleton": self.uses_singleton,
        }

    def import_module(self):
        """Import (or return the cached) processor module; typed on failure."""
        try:
            return importlib.import_module(self.module)
        except ImportError as exc:
            raise NucleusProcessorError(
                "processor_import_failed",
                f"cannot import {self.module}: {exc}",
            ) from exc

    def entry_point(self) -> Callable[..., Any]:
        """Return the callable processing entry point."""
        module = self.import_module()
        entry = getattr(module, self.entry)
        if not self.uses_singleton:
            return entry
        processor = entry()
        process = getattr(processor, "process", None)
        if not callable(process):
            raise NucleusProcessorError(
                "processor_descriptor_missing",
                f"{self.module}.{self.entry}() exposes no callable .process",
            )
        return process

    def load_processor(self) -> Any:
        """Return the singleton processor when published, else the module."""
        module = self.import_module()
        getter = getattr(module, "get_processor", None)
        return getter() if callable(getter) else module

    def process(
        self,
        spectrum: Any,
        *,
        trace: tuple[Any, Any] | None = None,
        options: Any = None,
    ) -> Any:
        """Call the registered entry point for one processed spectrum.

        ``trace`` (dense ``(ppm, intensity)`` pair, todo 45 handoff) is
        forwarded only to processors whose entry accepts it (carbon line
        fitting); processors that work from ``ProcessedSpectrum.lines``
        (proton) reject it with the typed ``trace_not_supported`` reason
        instead of raising a bare ``TypeError``.
        """
        entry = self.entry_point()
        kwargs: dict[str, Any] = {}
        if options is not None:
            kwargs["options"] = options
        if trace is not None:
            try:
                parameters = inspect.signature(entry).parameters
            except (TypeError, ValueError):  # builtin / C callables — no trace
                parameters = {}
            if "trace" not in parameters:
                raise NucleusProcessorError(
                    "trace_not_supported",
                    f"{self.module}.{self.entry} does not accept a dense trace; "
                    "pass trace only to processors that consume one",
                )
            kwargs["trace"] = trace
        return entry(spectrum, **kwargs)


def registered_elements() -> tuple[str, ...]:
    """Elements with a registered processor (sorted)."""
    return tuple(sorted(_PROCESSOR_MODULES))


def registered_nuclei() -> tuple[str, ...]:
    """Canonical nucleus labels with a registered processor (sorted)."""
    return tuple(nucleus_label(element) for element in registered_elements())


def _build_descriptor(element: str, module_name: str) -> ProcessorDescriptor:
    """Read a module's registration descriptor (imports it lazily)."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise NucleusProcessorError(
            "processor_import_failed",
            f"cannot import {module_name}: {exc}",
        ) from exc

    declared = getattr(module, "NUCLEUS", None)
    processor_id = getattr(module, "PROCESSOR_ID", None)
    if normalize_symbol(str(declared or "")) != element or not isinstance(processor_id, str):
        raise NucleusProcessorError(
            "processor_descriptor_missing",
            f"{module_name} must publish NUCLEUS={element!r} and a PROCESSOR_ID string "
            f"(got NUCLEUS={declared!r}, PROCESSOR_ID={processor_id!r})",
        )
    if not processor_id.strip():
        raise NucleusProcessorError(
            "processor_descriptor_missing",
            f"{module_name}.PROCESSOR_ID is blank",
        )
    label = getattr(module, "NUCLEUS_LABEL", None)
    nucleus_label_text = label if isinstance(label, str) and label else nucleus_label(element)

    entry, uses_singleton = _resolve_entry(module, module_name)
    return ProcessorDescriptor(
        element=element,
        nucleus_label=nucleus_label_text,
        processor_id=processor_id,
        module=module_name,
        entry=entry,
        uses_singleton=uses_singleton,
    )


def _resolve_entry(module: Any, module_name: str) -> tuple[str, bool]:
    """Resolve the entry attribute name + singleton style of a processor module."""
    getter = getattr(module, "get_processor", None)
    if callable(getter):
        process = getattr(getter(), "process", None)
        if not callable(process):
            raise NucleusProcessorError(
                "processor_descriptor_missing",
                f"{module_name}.get_processor() exposes no callable .process",
            )
        return "get_processor", True
    candidates = [
        name
        for name in dir(module)
        if name.startswith("process_") and callable(getattr(module, name))
    ]
    if len(candidates) != 1:
        raise NucleusProcessorError(
            "processor_descriptor_missing",
            f"{module_name} must publish exactly one process_* entry point "
            f"(found {sorted(candidates)})",
        )
    return candidates[0], False


def lookup_processor(nucleus_or_element: str) -> ProcessorDescriptor | None:
    """Resolve a nucleus/element (``"13C"``/``"C"``/``"1H"``/``"H"``) descriptor.

    Returns ``None`` for a nucleus with no registered processor (the caller
    decides whether that is acceptable); raises :class:`NucleusProcessorError`
    when a registered processor module cannot be imported or publishes an
    incomplete descriptor.
    """
    element = element_of_nucleus(str(nucleus_or_element))
    module_name = _PROCESSOR_MODULES.get(element)
    if module_name is None:
        return None
    return _build_descriptor(element, module_name)


def processor_for(nucleus_or_element: str) -> ProcessorDescriptor:
    """Resolve a descriptor or raise ``unknown_nucleus`` for unregistered nuclei."""
    descriptor = lookup_processor(nucleus_or_element)
    if descriptor is None:
        raise NucleusProcessorError(
            "unknown_nucleus",
            f"no spectrum processor registered for {nucleus_or_element!r}; "
            f"registered nuclei: {registered_nuclei()}",
        )
    return descriptor


def processor_registry() -> dict[str, ProcessorDescriptor]:
    """All registered descriptors keyed by element (insertion order C, H)."""
    registry: dict[str, ProcessorDescriptor] = {}
    for element in registered_elements():
        descriptor = lookup_processor(element)
        if descriptor is not None:
            registry[element] = descriptor
    return registry


__all__ = [
    "PROCESSOR_ERROR_REASONS",
    "NucleusProcessorError",
    "ProcessorDescriptor",
    "lookup_processor",
    "processor_for",
    "processor_registry",
    "registered_elements",
    "registered_nuclei",
]
