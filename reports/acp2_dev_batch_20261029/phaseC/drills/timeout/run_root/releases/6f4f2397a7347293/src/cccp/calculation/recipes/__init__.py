"""ACP2 scientific recipe contracts and deterministic validation (A-07a).

This initializer exports only.  Implementation lives in the sibling modules:

* :mod:`~cccp.calculation.recipes.contracts` — frozen recipe types mirroring
  ``recipe.schema.json`` and the typed contract errors;
* :mod:`~cccp.calculation.recipes.validation` — the pure DAG validator, the
  typed validation errors and the failure-propagation report.

* :mod:`~cccp.calculation.recipes.compile` — the deterministic definition
  compiler and the two-layer science identity boundary.

Importing this package pulls in no task-execution module, no QC interface and
never starts a calculation.  Compilation produces a ``TaskRequest`` DAG
definition only; it never executes it.
"""

from __future__ import annotations

from cccp.calculation.recipes.compile import (
    COMPILED_RECIPE_SCHEMA as COMPILED_RECIPE_SCHEMA,
)
from cccp.calculation.recipes.compile import (
    COMPILED_RECIPE_SCHEMA_VERSION as COMPILED_RECIPE_SCHEMA_VERSION,
)
from cccp.calculation.recipes.compile import (
    WHOLE_STEP_PRODUCT_KIND as WHOLE_STEP_PRODUCT_KIND,
)
from cccp.calculation.recipes.compile import (
    ApprovalMetadata as ApprovalMetadata,
)
from cccp.calculation.recipes.compile import (
    CompiledRecipe as CompiledRecipe,
)
from cccp.calculation.recipes.compile import (
    CompiledStep as CompiledStep,
)
from cccp.calculation.recipes.compile import (
    CompileInputError as CompileInputError,
)
from cccp.calculation.recipes.compile import (
    HistoricalRecipe as HistoricalRecipe,
)
from cccp.calculation.recipes.compile import (
    InvalidProviderVersionError as InvalidProviderVersionError,
)
from cccp.calculation.recipes.compile import (
    InvalidUpstreamDigestError as InvalidUpstreamDigestError,
)
from cccp.calculation.recipes.compile import (
    RecipeCompileError as RecipeCompileError,
)
from cccp.calculation.recipes.compile import (
    RecipeInputs as RecipeInputs,
)
from cccp.calculation.recipes.compile import (
    StepExecutionIdentity as StepExecutionIdentity,
)
from cccp.calculation.recipes.compile import (
    StepIdentityError as StepIdentityError,
)
from cccp.calculation.recipes.compile import (
    UnknownCompiledStepError as UnknownCompiledStepError,
)
from cccp.calculation.recipes.compile import (
    UnknownScientificFieldError as UnknownScientificFieldError,
)
from cccp.calculation.recipes.compile import (
    UnresolvedOutputRefError as UnresolvedOutputRefError,
)
from cccp.calculation.recipes.compile import (
    compile_recipe as compile_recipe,
)
from cccp.calculation.recipes.compile import (
    materialize_step_identity as materialize_step_identity,
)
from cccp.calculation.recipes.compile import (
    output_ref_address as output_ref_address,
)
from cccp.calculation.recipes.compile import (
    read_historical_recipe as read_historical_recipe,
)
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
    "Alternative",
    "Applicability",
    "ApprovalMetadata",
    "COMPILED_RECIPE_SCHEMA",
    "COMPILED_RECIPE_SCHEMA_VERSION",
    "CompileInputError",
    "CompiledRecipe",
    "CompiledStep",
    "DanglingReference",
    "DanglingReferenceError",
    "DependencyRule",
    "DiagnosticDependencyError",
    "DuplicateAlternativeError",
    "DuplicateEdgeError",
    "DuplicateStepError",
    "Edge",
    "HistoricalRecipe",
    "InvalidProviderVersionError",
    "InvalidUpstreamDigestError",
    "Limits",
    "MissingRequiredStepError",
    "OutputRef",
    "RECIPE_SCHEMA",
    "RECIPE_SCHEMA_VERSION",
    "Recipe",
    "RecipeCompileError",
    "RecipeContractError",
    "RecipeCycleError",
    "RecipeForbiddenFieldError",
    "RecipeInputs",
    "RecipeLimitError",
    "RecipeSchemaError",
    "RecipeSchemaVersionError",
    "RecipeValidationError",
    "RecipeValidationReport",
    "SAFE_FIELD_PATTERN",
    "SELECTOR_KINDS",
    "STEP_ID_PATTERN",
    "Selector",
    "Step",
    "StepExecutionIdentity",
    "StepFlagConflictError",
    "StepIdentityError",
    "UnknownCompiledStepError",
    "UnknownRecipeCapabilityError",
    "UnknownRecipeTaskKindError",
    "UnknownScientificFieldError",
    "UnresolvedOutputRefError",
    "ValidatorRef",
    "WHOLE_STEP_PRODUCT_KIND",
    "blocked_steps",
    "compile_recipe",
    "existing_task_kinds",
    "materialize_step_identity",
    "output_ref_address",
    "read_historical_recipe",
    "validate_recipe",
]
