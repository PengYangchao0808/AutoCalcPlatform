"""ACP2 scientific recipe contracts and deterministic validation (A-07a).

This initializer exports only.  Implementation lives in the sibling modules:

* :mod:`~cccp.calculation.recipes.contracts` — frozen recipe types mirroring
  ``recipe.schema.json`` and the typed contract errors;
* :mod:`~cccp.calculation.recipes.validation` — the pure DAG validator, the
  typed validation errors and the failure-propagation report.

Importing this package pulls in no task-execution module, no QC interface and
never starts a calculation.  Recipe compilation and science identity belong to
todo 16 (``recipes/compile.py``) and are deliberately absent here.
"""

from __future__ import annotations

from cccp.calculation.recipes.contracts import (
    RECIPE_SCHEMA as RECIPE_SCHEMA,
)
from cccp.calculation.recipes.contracts import (
    RECIPE_SCHEMA_VERSION as RECIPE_SCHEMA_VERSION,
)
from cccp.calculation.recipes.contracts import (
    SAFE_FIELD_PATTERN as SAFE_FIELD_PATTERN,
)
from cccp.calculation.recipes.contracts import (
    SELECTOR_KINDS as SELECTOR_KINDS,
)
from cccp.calculation.recipes.contracts import (
    STEP_ID_PATTERN as STEP_ID_PATTERN,
)
from cccp.calculation.recipes.contracts import (
    Alternative as Alternative,
)
from cccp.calculation.recipes.contracts import (
    Applicability as Applicability,
)
from cccp.calculation.recipes.contracts import (
    Edge as Edge,
)
from cccp.calculation.recipes.contracts import (
    Limits as Limits,
)
from cccp.calculation.recipes.contracts import (
    OutputRef as OutputRef,
)
from cccp.calculation.recipes.contracts import (
    Recipe as Recipe,
)
from cccp.calculation.recipes.contracts import (
    RecipeContractError as RecipeContractError,
)
from cccp.calculation.recipes.contracts import (
    RecipeForbiddenFieldError as RecipeForbiddenFieldError,
)
from cccp.calculation.recipes.contracts import (
    RecipeSchemaError as RecipeSchemaError,
)
from cccp.calculation.recipes.contracts import (
    RecipeSchemaVersionError as RecipeSchemaVersionError,
)
from cccp.calculation.recipes.contracts import (
    Selector as Selector,
)
from cccp.calculation.recipes.contracts import (
    Step as Step,
)
from cccp.calculation.recipes.contracts import (
    StepFlagConflictError as StepFlagConflictError,
)
from cccp.calculation.recipes.contracts import (
    UnknownRecipeTaskKindError as UnknownRecipeTaskKindError,
)
from cccp.calculation.recipes.contracts import (
    ValidatorRef as ValidatorRef,
)
from cccp.calculation.recipes.contracts import (
    existing_task_kinds as existing_task_kinds,
)
from cccp.calculation.recipes.validation import (
    DanglingReference as DanglingReference,
)
from cccp.calculation.recipes.validation import (
    DanglingReferenceError as DanglingReferenceError,
)
from cccp.calculation.recipes.validation import (
    DependencyRule as DependencyRule,
)
from cccp.calculation.recipes.validation import (
    DiagnosticDependencyError as DiagnosticDependencyError,
)
from cccp.calculation.recipes.validation import (
    DuplicateAlternativeError as DuplicateAlternativeError,
)
from cccp.calculation.recipes.validation import (
    DuplicateEdgeError as DuplicateEdgeError,
)
from cccp.calculation.recipes.validation import (
    DuplicateStepError as DuplicateStepError,
)
from cccp.calculation.recipes.validation import (
    MissingRequiredStepError as MissingRequiredStepError,
)
from cccp.calculation.recipes.validation import (
    RecipeCycleError as RecipeCycleError,
)
from cccp.calculation.recipes.validation import (
    RecipeLimitError as RecipeLimitError,
)
from cccp.calculation.recipes.validation import (
    RecipeValidationError as RecipeValidationError,
)
from cccp.calculation.recipes.validation import (
    RecipeValidationReport as RecipeValidationReport,
)
from cccp.calculation.recipes.validation import (
    UnknownRecipeCapabilityError as UnknownRecipeCapabilityError,
)
from cccp.calculation.recipes.validation import (
    blocked_steps as blocked_steps,
)
from cccp.calculation.recipes.validation import (
    validate_recipe as validate_recipe,
)

__all__ = [
    "RECIPE_SCHEMA",
    "RECIPE_SCHEMA_VERSION",
    "SAFE_FIELD_PATTERN",
    "SELECTOR_KINDS",
    "STEP_ID_PATTERN",
    "Alternative",
    "Applicability",
    "DanglingReference",
    "DanglingReferenceError",
    "DependencyRule",
    "DiagnosticDependencyError",
    "DuplicateAlternativeError",
    "DuplicateEdgeError",
    "DuplicateStepError",
    "Edge",
    "Limits",
    "MissingRequiredStepError",
    "OutputRef",
    "Recipe",
    "RecipeContractError",
    "RecipeCycleError",
    "RecipeForbiddenFieldError",
    "RecipeLimitError",
    "RecipeSchemaError",
    "RecipeSchemaVersionError",
    "RecipeValidationError",
    "RecipeValidationReport",
    "Selector",
    "Step",
    "StepFlagConflictError",
    "UnknownRecipeCapabilityError",
    "UnknownRecipeTaskKindError",
    "ValidatorRef",
    "blocked_steps",
    "existing_task_kinds",
    "validate_recipe",
]
