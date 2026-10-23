# Design: quota update mode (add vs set)

**Status:** Implemented — `mode=add|set` query param on `PATCH .../quota`
(`api/namespaces.py`), `NamespaceManager.update_quota(..., mode)`. Default `add`.
**Author:** platform-team
**Date:** 2026-10-23
**Related:** [nodes-and-quota-changes.md](nodes-and-quota-changes.md), [../USER.md](../USER.md)

## 1. What's being asked

`PATCH /api/v1/namespaces/{name}/quota` is currently **additive** — the value
sent is added to the current quota. We want a toggle to switch that off:

| "patch mode" | meaning | result per dimension |
|--------------|---------|----------------------|
| **ON** (default today) | add the sent value | `current + sent` |
| **OFF** | replace with the sent value | `sent` |

"OFF" is exactly the original pre-delta behavior (absolute set). So this toggle
lets one endpoint do **both** — add a delta, or set an absolute value.

## 2. API shape

A per-request **query parameter** on the existing endpoint (the body is
unchanged):

```
PATCH /api/v1/namespaces/{name}/quota?mode=add    # default — additive
PATCH /api/v1/namespaces/{name}/quota?mode=set    # absolute replace
```

```jsonc
// current memory = 8Gi
{"limits": {"memory": 2}}   // mode=add -> 10Gi
{"limits": {"memory": 2}}   // mode=set ->  2Gi
```

- **Default `mode=add`** keeps the just-shipped behavior, so existing callers
  are unaffected.
- A query param (not a body field) because it's an *operation* modifier, like
  `?force=true` on delete — it says *how* to interpret the body, not *what* the
  body is.

> Naming: `mode=add|set` is chosen over a boolean so the two cases read clearly
> at the call site and in logs, and to avoid confusion with HTTP `PATCH`. If you
> prefer your own wording we can use `?patch=true|false` ("patch mode on/off")
> instead — purely cosmetic (open question §6).

## 3. Logic (the only real change)

`update_quota` today computes, per supplied dimension:

```
base   = current hard value (or 0 if the dimension isn't set yet)
result = base + sent
```

The toggle adds **one branch**:

```
result = base + sent   if mode == add    (current behavior)
result = sent          if mode == set    (ignore base)
```

Everything downstream is unchanged and still applies in both modes:
- memory / storage / pvc result **< 0 → 422** (in `set` mode this can only
  happen if the caller sends a negative, which is already nonsensical there);
- **cpu floored at 1** in `apply_quota`;
- integer `Gi` serialization, merge-patch (only supplied dimensions change).

Pseudocode diff vs. what's implemented:

```python
def update_quota(self, name, values, mode="add"):      # + mode param
    ...
    base = _gi_to_int(hard.get("requests.memory", "0Gi"))
    sent = _gi_to_int(values.memory)
    result = base + sent if mode == "add" else sent     # <-- the whole change
    ...
```

That's it: one parameter threaded from the route, one ternary per dimension.

## 4. Schema / validation

- **Reuse the existing body** (`QuotaDelta`, holding the `limits`). In `set`
  mode a negative value is meaningless; the existing below-zero `422` already
  rejects it, so no new validation is strictly required.
- The route gains `mode: Literal["add", "set"] = "add"` (FastAPI validates the
  enum and rejects anything else with `422` automatically).
- No new models, no new config, no change to create (create is always absolute).

## 5. Why this is *not* complex

| Touch point | Change |
|-------------|--------|
| Route (`api/namespaces.py`) | add `mode` query param, pass it through |
| Service (`update_quota`) | add `mode` arg + one ternary per dimension |
| Models | none (reuse `QuotaDelta`) |
| Template / `apply_quota` | none (floor, render, merge-patch already there) |
| Config / Helm / RBAC | none |
| Docs / tests | a few lines each (add-vs-set cases) |

No new arithmetic, no new object, no new failure mode. The costly part (reading
the current value, integer math, CPU floor, serialization) already exists and is
shared by both modes. Risk is low and fully covered by a couple of extra tests.

The one conceptual cost: the same endpoint now has **two meanings** for the body
depending on a flag. That's a documentation/clarity cost, not a code cost — it's
mitigated by the explicit `mode=add|set` name and the `add` default.

## 6. Open questions

- **Param name** — `mode=add|set` (recommended) vs. `patch=true|false`
  (your "patch mode on/off" wording). Behavior identical.
- **Default** — `add` (chosen, preserves current behavior). Flip to `set` only
  if absolute should be the norm.
- **Reject negatives in `set` mode explicitly** (clearer `422` message) vs. let
  the generic below-zero check handle it (chosen — less code).

## 7. Plan (when approved)

1. Route: `mode` query param → `update_quota(name, limits, mode)`.
2. Service: `mode` arg + the ternary; everything else untouched.
3. Tests: `mode=set` replaces, `mode=add` (default) adds, `set` below-zero 422,
   invalid `mode` → 422.
4. Docs: USER.md update-quota section (both modes).
