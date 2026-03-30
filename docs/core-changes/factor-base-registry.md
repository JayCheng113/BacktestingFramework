# Core Change Proposal: Factor base.py — Auto-Registration

**Date**: 2026-03-30
**Version**: V2.10
**File**: ez/factor/base.py
**Status**: Implemented (retroactive proposal)

## Change

Added `__init_subclass__` + `_registry` + `get_registry()` to `Factor(ABC)`, mirroring the existing pattern in `Strategy(ABC)` since V1.

## Motivation

- Factor and CrossSectionalFactor were the only extensible types without auto-registration
- Users could create factor files via CodeEditor but they were invisible to the API (hardcoded `_FACTOR_MAP`)
- Inconsistency: Strategy/PortfolioStrategy auto-register, Factor/CrossSectionalFactor don't

## Impact

- **Backward compatible**: Existing Factor subclasses automatically register on import
- **No API change**: `_FACTOR_MAP` replaced by dynamic `_get_factor_map()` that includes registry
- **No behavior change for existing code**: All 9 builtin factors continue to work identically

## Testing

- 1195 tests pass (7 new regression tests for factor lifecycle)
- Factor save/validate/rollback/delete/registry-cleanup all tested
