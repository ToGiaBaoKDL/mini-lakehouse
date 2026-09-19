# Schema Change

## Classify

- Backward compatible: additive optional field or widened representation accepted by all readers.
- Conditionally compatible: a change whose safety depends on a declared consumer or table-format
  capability.
- Breaking: removal, rename, required field, narrowing, key/grain/semantic/unit change, or behavior
  that changes existing results.

## Procedure

1. Inventory producers, consumers, shared extracts, serving copies, policies, and field usage.
2. Update the contract first and run compatibility checks in CI.
3. For breaking changes, create a new contract/version or dual-publication compatibility view with
   an owner and removal date.
4. Deploy readers before writers for additive changes; deploy replacement consumers before
   removing old fields for breaking changes.
5. Validate old and new shapes, key/grain invariants, reconciliation, rollback, and replay.
6. Observe consumer usage through the deprecation window, then remove obsolete writes and
   compatibility objects.

Stop if consumer ownership is unknown, field meaning or unit is ambiguous, rollback would require
manual data editing, or two writers would own the same published relation.

