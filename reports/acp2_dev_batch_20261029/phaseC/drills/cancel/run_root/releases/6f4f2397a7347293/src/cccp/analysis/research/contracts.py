"""Read-only access to the packaged ACP2 science contract corpus.

The authoritative documents live in
``docs/reference/next-change/acp2-contracts/science/``.  A byte-identical copy
ships inside :mod:`cccp.analysis.research` so a standalone install can validate
payloads and read the documented examples without a repository checkout.  The
in-package copy is data only: this module never parses a schema, never defines
a second contract engine and never writes anything.

The accessor is deliberately small:

* :func:`contract_root` returns the packaged data directory;
* :func:`list_contracts` returns the packaged file names as sorted POSIX
  relative paths;
* :func:`read_contract` / :func:`read_contract_text` return one file's bytes or
  decoded text after rejecting absolute paths and ``..`` traversal.

Only the standard library is imported.  Importing this module performs no I/O,
starts nothing and pulls in no task, QC or platform module.

Author: QCcalc Team
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CONTRACT_FILE_NAMES",
    "CONTRACT_ROOT_NAME",
    "ContractPathError",
    "contract_root",
    "list_contracts",
    "read_contract",
    "read_contract_text",
]

#: Directory name of the in-package corpus (sibling of this module).
CONTRACT_ROOT_NAME = "contracts"

#: The fourteen frozen contract files, in the order fixed by the docs corpus.
CONTRACT_FILE_NAMES: tuple[str, ...] = (
    "capability.schema.json",
    "task-binding.schema.json",
    "errors.schema.json",
    "reaction.schema.json",
    "question.schema.json",
    "evidence.schema.json",
    "provenance.schema.json",
    "recipe.schema.json",
    "semantic-rules.md",
    "evidence-semantics.md",
    "examples/capability-energy.json",
    "examples/task-binding-singlepoint.json",
    "examples/reaction-da.json",
    "examples/question-activation.json",
)


class ContractPathError(ValueError):
    """A contract name is absolute, escapes the corpus, or is otherwise invalid."""


def contract_root() -> Path:
    """Return the packaged contract data directory.

    Returns:
        The absolute ``.../cccp/analysis/research/contracts`` directory.  The
        path is computed on each call and no file is created or modified.
    """
    return Path(__file__).resolve().parent / CONTRACT_ROOT_NAME


def list_contracts() -> tuple[str, ...]:
    """Return every packaged contract file as a sorted POSIX relative path.

    Returns:
        Relative names such as ``"capability.schema.json"`` and
        ``"examples/reaction-da.json"``.  Only regular files below
        :func:`contract_root` are listed.
    """
    root = contract_root()
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    )


def _resolve_contract_path(name: str) -> Path:
    """Validate *name* and return the existing file below the corpus root.

    Raises:
        ContractPathError: *name* is not a non-empty relative string, is
            absolute, contains ``..`` or resolves outside the corpus.
        FileNotFoundError: The name is valid but no packaged file exists.
    """
    if not isinstance(name, str) or not name.strip():
        message = "contract name must be a non-empty relative string"
        raise ContractPathError(message)
    if "\x00" in name:
        message = f"contract name {name!r} contains a null byte"
        raise ContractPathError(message)
    candidate = Path(name)
    if candidate.is_absolute():
        message = f"contract name {name!r} must be relative to the contract root"
        raise ContractPathError(message)
    if any(part == ".." for part in candidate.parts):
        message = f"contract name {name!r} must not traverse outside the contract root"
        raise ContractPathError(message)
    if any(part in ("", ".") for part in candidate.parts):
        message = f"contract name {name!r} must name a file below the contract root"
        raise ContractPathError(message)

    root = contract_root()
    resolved = (root / candidate).resolve()
    if resolved == root or root not in resolved.parents:
        message = f"contract name {name!r} resolves outside the contract root"
        raise ContractPathError(message)
    if not resolved.is_file():
        message = f"contract {name!r} is not present in the packaged corpus at {root}"
        raise FileNotFoundError(message)
    return resolved


def read_contract(name: str) -> bytes:
    """Return one packaged contract file as bytes.

    Args:
        name: POSIX relative name such as ``"reaction.schema.json"``.

    Returns:
        The exact file bytes (identical to the docs corpus copy).

    Raises:
        ContractPathError: The name is absolute, traversing or malformed.
        FileNotFoundError: The named file is not packaged.
    """
    return _resolve_contract_path(name).read_bytes()


def read_contract_text(name: str, *, encoding: str = "utf-8") -> str:
    """Return one packaged contract file decoded as text.

    Args:
        name: POSIX relative name such as ``"examples/reaction-da.json"``.
        encoding: Text encoding; UTF-8 is the corpus encoding.

    Returns:
        The decoded file content.

    Raises:
        ContractPathError: The name is absolute, traversing or malformed.
        FileNotFoundError: The named file is not packaged.
    """
    return _resolve_contract_path(name).read_text(encoding=encoding)
