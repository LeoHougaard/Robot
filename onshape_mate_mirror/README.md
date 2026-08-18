# Onshape independent mirrored mates

Onshape's Assembly Mirror intentionally mirrors motion without recreating
mates. The mirrored leg therefore follows the source leg and is not an
independent articulated chain. FeatureScript cannot fix that because custom
features execute in Part Studios, not in Assembly feature lists.

This utility uses Onshape's supported Assembly API to copy existing revolute
mates, replace source-leg instance references with target-leg references, and
optionally mirror angular limits from `[min, max]` to `[-max, -min]`.

It is deliberately safe and call-efficient:

- inspecting and planning are local operations;
- a saved snapshot can be reused for every leg;
- applying a plan performs exactly one write call per mate and no verification
  reads;
- the default budget is 9 calls, so the tool refuses to reach 10;
- it never stores API keys and never deletes or edits an existing mate;
- it sends credentials only to the trusted `cad.onshape.com` API stack.

## Recommended workflow

Before starting, create an Onshape version of the known-good assembly. For
symmetric leg parts, use **Transform** in Assembly Mirror. That keeps the same
part identity and persistent geometry references. A **Derived** asymmetric part
may have different references; the planner rejects that by default.

### 1. Capture the assembly once

The convenient path costs two API calls:

```powershell
$env:ONSHAPE_ACCESS_KEY = "<access key>"
$env:ONSHAPE_SECRET_KEY = "<secret key>"

python .\onshape_mate_mirror\onshape_mate_mirror.py fetch `
  --assembly-url "https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>" `
  --output "$env:TEMP\dog-assembly-snapshot.json"
```

For zero billable read calls, use Onshape's API Explorer while authenticated
with the normal Onshape browser session (not API-key or OAuth authentication).
Onshape documents browser-session Explorer calls as excluded from annual API
usage. Save the JSON responses from these two v12 endpoints:

1. `Assembly/getAssemblyDefinition`, with `includeMateFeatures=true`,
   `includeMateConnectors=true`, `excludeSuppressed=true`.
2. `Assembly/getFeatures`.

Then combine them locally:

```powershell
python .\onshape_mate_mirror\onshape_mate_mirror.py combine `
  --assembly-url "https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>" `
  --definition .\assembly-definition.json `
  --features .\assembly-features.json `
  --output "$env:TEMP\dog-assembly-snapshot.json"
```

Do not commit snapshots: they contain document structure and stable internal
IDs.

### 2. Inspect names and IDs locally

```powershell
python .\onshape_mate_mirror\onshape_mate_mirror.py inspect `
  --snapshot "$env:TEMP\dog-assembly-snapshot.json"
```

Use exact instance and mate names in a configuration copied from
`example-config.json`. If Onshape has duplicate names, use the printed IDs.

Each `instanceMap` maps source-leg parts to one independently moving target
leg. The body is intentionally omitted: an unmapped body reference remains the
same. Add one `copies` entry per target leg. Set `mirrorLimits` to `false` only
when the target mate axes already use the same sign convention.

If a mate intentionally references a duplicated instance that must remain
shared, list that exact name or ID in the copy's `sharedInstances` array. The
planner otherwise rejects duplicated referenced instances left out of
`instanceMap`, because a partial leg map can accidentally create a cross-leg
mate.

Assembly-level explicit mate connectors must already have independent target
connectors. Map those connector feature names or IDs with an optional
`featureMap` object. Part Studio-owned or implicit connectors normally need
only `instanceMap`.

### 3. Build and review a zero-call plan

```powershell
python .\onshape_mate_mirror\onshape_mate_mirror.py plan `
  --snapshot "$env:TEMP\dog-assembly-snapshot.json" `
  --config .\my-dog-mate-map.json `
  --output "$env:TEMP\dog-mate-plan.json"
```

The command prints the exact number of writes. By default it refuses any plan
over nine mates. A typical 3-DOF source leg copied to three other legs needs
nine writes; use the zero-call snapshot route to keep the whole operation at
nine billable calls.

Review the generated JSON. In particular, confirm every rewritten `path`, mate
name, and `limitAxialZMin`/`limitAxialZMax` expression.

### 4. Apply once

The API key needs read/write document permissions. The key remains only in the
current process environment.

```powershell
python .\onshape_mate_mirror\onshape_mate_mirror.py apply `
  --plan "$env:TEMP\dog-mate-plan.json" `
  --yes

Remove-Item Env:ONSHAPE_ACCESS_KEY
Remove-Item Env:ONSHAPE_SECRET_KEY
```

If a write fails, the tool stops immediately and reports exactly which mates
were already created. Delete any unwanted partial mates in the Onshape UI;
automatic rollback would consume more API calls.

## Verification before export

In the Onshape assembly:

1. Suppress or remove the original Assembly Mirror motion feature after the new
   independent mates solve correctly.
2. Animate every new revolute and confirm it moves only its own leg.
3. Check that the displayed minimum and maximum are the expected mirrored
   mechanical limits.
4. Check the mate primary-axis sign against the actuator convention.
5. Confirm there are no over-defined or failed mates.
6. Open Omniverse Publisher's **Model preparation**, confirm all independent
   joints appear, save, version, and export.

The utility copies mate definitions; it cannot prove that a mirrored CAD face
has the desired physical axis. The visual motion and limit check is therefore a
required export gate.
