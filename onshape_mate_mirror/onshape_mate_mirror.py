#!/usr/bin/env python3
"""Copy independent revolute mates onto mirrored Onshape assembly instances.

The tool deliberately separates reads, planning, and writes.  A plan can be
built entirely from saved JSON, and applying a plan performs exactly one
Onshape API call per new mate with no verification reads.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
DEFAULT_API_VERSION = "v12"
DEFAULT_MAX_CALLS = 9  # Keep the complete operation below ten calls by default.
ALLOWED_STACKS = {"cad.onshape.com"}
API_VERSION_RE = re.compile(r"v[0-9]+")


class ToolError(RuntimeError):
    pass


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Keep Basic credentials on the explicitly validated API origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class AssemblyRef:
    stack: str
    document_id: str
    workspace_id: str
    element_id: str

    @classmethod
    def parse(cls, url: str) -> "AssemblyRef":
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ToolError("Assembly URL must be an https:// Onshape URL.")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or (parsed.hostname or "").lower() not in ALLOWED_STACKS
        ):
            raise ToolError(
                "Assembly URL must use the trusted cad.onshape.com API stack."
            )
        match = re.search(
            r"/documents/([0-9a-fA-F]{24})/w/([0-9a-fA-F]{24})/e/([0-9a-fA-F]{24})(?:/|$)",
            parsed.path,
        )
        if not match:
            raise ToolError(
                "Assembly URL must point to a writable workspace: "
                ".../documents/<did>/w/<wid>/e/<eid>."
            )
        return cls(
            stack=f"{parsed.scheme}://{parsed.netloc}",
            document_id=match.group(1),
            workspace_id=match.group(2),
            element_id=match.group(3),
        )

    def assembly_path(self, suffix: str = "") -> str:
        return (
            f"/assemblies/d/{self.document_id}/w/{self.workspace_id}"
            f"/e/{self.element_id}{suffix}"
        )

    def canonical_url(self) -> str:
        return (
            f"{self.stack}/documents/{self.document_id}/w/{self.workspace_id}"
            f"/e/{self.element_id}"
        )


class OnshapeClient:
    def __init__(
        self,
        assembly: AssemblyRef,
        access_key: str,
        secret_key: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        max_calls: int = DEFAULT_MAX_CALLS,
    ) -> None:
        if not access_key or not secret_key:
            raise ToolError(
                "Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY in the current shell."
            )
        if not API_VERSION_RE.fullmatch(api_version):
            raise ToolError("Onshape API version must look like v12.")
        if max_calls < 1:
            raise ToolError("Onshape API call budget must be at least one.")
        self.assembly = assembly
        self.api_version = api_version
        self.max_calls = max_calls
        self.calls_attempted = 0
        token = base64.b64encode(f"{access_key}:{secret_key}".encode()).decode()
        self._authorization = f"Basic {token}"
        self._opener = urllib.request.build_opener(RefuseRedirects)

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        if self.calls_attempted >= self.max_calls:
            raise ToolError(
                f"Refusing API call {self.calls_attempted + 1}; budget is "
                f"{self.max_calls}."
            )
        self.calls_attempted += 1
        url = f"{self.assembly.stack}/api/{self.api_version}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/json;charset=UTF-8; qs=0.09",
                "Content-Type": "application/json;charset=UTF-8; qs=0.09",
                "Authorization": self._authorization,
                "User-Agent": "robot-training-onshape-mate-mirror/1",
            },
        )
        try:
            with self._opener.open(request, timeout=45) as response:
                payload = response.read()
                result = json.loads(payload) if payload else {}
                headers = {key.lower(): value for key, value in response.headers.items()}
                return result, headers
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ToolError(
                f"Onshape returned HTTP {exc.code} for {method} {path}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"Could not reach Onshape: {exc.reason}") from exc


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"Could not read JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolError(f"Expected a JSON object in {path}.")
    return value


def write_json(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=False)
        handle.write("\n")


def make_snapshot(
    assembly_url: str,
    definition: dict[str, Any],
    feature_response: dict[str, Any],
) -> dict[str, Any]:
    assembly = AssemblyRef.parse(assembly_url)
    if "rootAssembly" not in definition:
        raise ToolError("Assembly-definition JSON has no rootAssembly object.")
    if not isinstance(feature_response.get("features"), list):
        raise ToolError("Assembly-feature JSON has no features array.")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "assemblyUrl": assembly.canonical_url(),
        "definition": definition,
        "featureResponse": feature_response,
    }


def root_instances(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    root = snapshot.get("definition", {}).get("rootAssembly", {})
    instances = root.get("instances", [])
    if not isinstance(instances, list):
        raise ToolError("Snapshot rootAssembly.instances is not an array.")
    return [item for item in instances if isinstance(item, dict)]


def features(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result = snapshot.get("featureResponse", {}).get("features", [])
    if not isinstance(result, list):
        raise ToolError("Snapshot featureResponse.features is not an array.")
    flat: list[dict[str, Any]] = []
    for feature in result:
        if not isinstance(feature, dict):
            continue
        if "message" in feature and "parameters" not in feature:
            raise ToolError(
                "This snapshot uses the legacy nested feature format. Fetch it with "
                "API v12 so its feature bodies can safely be posted back to v12."
            )
        flat.append(feature)
    return flat


def parameter(feature: dict[str, Any], parameter_id: str) -> dict[str, Any] | None:
    for item in feature.get("parameters", []):
        if isinstance(item, dict) and item.get("parameterId") == parameter_id:
            return item
    return None


def mate_type(feature: dict[str, Any]) -> str | None:
    item = parameter(feature, "mateType")
    return item.get("value") if item else None


def is_revolute(feature: dict[str, Any]) -> bool:
    return feature.get("featureType") == "mate" and mate_type(feature) == "REVOLUTE"


def resolve_named(
    reference: str,
    values: Iterable[dict[str, Any]],
    *,
    kind: str,
) -> dict[str, Any]:
    candidates = list(values)
    by_id = [item for item in candidates if item.get("id") == reference]
    if not by_id and kind == "feature":
        by_id = [item for item in candidates if item.get("featureId") == reference]
    if len(by_id) == 1:
        return by_id[0]
    by_name = [item for item in candidates if item.get("name") == reference]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        ids = [item.get("id") or item.get("featureId") for item in by_name]
        raise ToolError(f"Ambiguous {kind} name {reference!r}; use one of IDs {ids}.")
    raise ToolError(f"Unknown {kind} name or ID: {reference!r}.")


def instance_identity(instance: dict[str, Any]) -> tuple[Any, ...]:
    return (
        instance.get("type"),
        instance.get("documentId"),
        instance.get("elementId"),
        instance.get("partId"),
        instance.get("fullConfiguration") or instance.get("configuration"),
    )


def clean_generated_fields(value: Any, *, top_level: bool = True) -> Any:
    if isinstance(value, list):
        return [clean_generated_fields(item, top_level=False) for item in value]
    if not isinstance(value, dict):
        return value
    create_feature_fields = {
        "btType",
        "featureType",
        "returnAfterSubfeatures",
        "subFeatures",
        "namespace",
        "version",
        "name",
        "parameters",
        "isHidden",
        "suppressed",
    }
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if top_level and key not in create_feature_fields:
            continue
        if key in {"nodeId", "hasUserCode"}:
            continue
        cleaned[key] = clean_generated_fields(item, top_level=False)
    return cleaned


def rewrite_references(
    value: Any,
    instance_map: dict[str, str],
    feature_map: dict[str, str],
) -> int:
    changes = 0
    if isinstance(value, list):
        for item in value:
            changes += rewrite_references(item, instance_map, feature_map)
        return changes
    if not isinstance(value, dict):
        return 0
    path = value.get("path")
    if isinstance(path, list):
        rewritten = [instance_map.get(item, item) for item in path]
        changes += sum(left != right for left, right in zip(path, rewritten))
        value["path"] = rewritten
    feature_id = value.get("featureId")
    if isinstance(feature_id, str) and feature_id in feature_map:
        value["featureId"] = feature_map[feature_id]
        changes += 1
    for key, item in value.items():
        if key not in {"path", "featureId"}:
            changes += rewrite_references(item, instance_map, feature_map)
    return changes


def referenced_instance_ids(value: Any, known_ids: set[str]) -> set[str]:
    """Return root-assembly instance IDs used in nested occurrence paths."""

    result: set[str] = set()
    if isinstance(value, list):
        for item in value:
            result.update(referenced_instance_ids(item, known_ids))
    elif isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, list):
            result.update(item for item in path if item in known_ids)
        for key, item in value.items():
            if key != "path":
                result.update(referenced_instance_ids(item, known_ids))
    return result


_QUANTITY_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*$"
)


def negate_expression(expression: str) -> str:
    match = _QUANTITY_RE.match(expression)
    if match:
        number = float(match.group(1))
        suffix = match.group(2)
        if number == 0:
            number_text = "0"
        else:
            number_text = f"{-number:g}"
        return f"{number_text}{(' ' + suffix) if suffix else ''}"
    stripped = expression.strip()
    if stripped.startswith("-(") and stripped.endswith(")"):
        return stripped[2:-1]
    return f"-({stripped})"


def negate_limit(parameter_value: dict[str, Any], boundary: str) -> None:
    null_value = parameter_value.get("nullValue")
    if isinstance(null_value, str) and null_value:
        parameter_value["nullValue"] = re.sub(
            r"minimum|maximum", boundary, null_value, flags=re.IGNORECASE
        )
        return
    expression = parameter_value.get("expression")
    if isinstance(expression, str) and expression.strip():
        parameter_value["expression"] = negate_expression(expression)
    value = parameter_value.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parameter_value["value"] = -value


def mirror_revolute_limits(feature: dict[str, Any]) -> None:
    params = feature.get("parameters", [])
    min_index = next(
        (index for index, item in enumerate(params) if item.get("parameterId") == "limitAxialZMin"),
        None,
    )
    max_index = next(
        (index for index, item in enumerate(params) if item.get("parameterId") == "limitAxialZMax"),
        None,
    )
    if min_index is None or max_index is None:
        return
    old_min = copy.deepcopy(params[min_index])
    old_max = copy.deepcopy(params[max_index])
    old_max["parameterId"] = "limitAxialZMin"
    old_min["parameterId"] = "limitAxialZMax"
    negate_limit(old_max, "minimum")
    negate_limit(old_min, "maximum")
    params[min_index] = old_max
    params[max_index] = old_min


def resolve_feature_map(
    configured: dict[str, str], all_features: list[dict[str, Any]]
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for source_ref, target_ref in configured.items():
        source = resolve_named(source_ref, all_features, kind="feature")
        target = resolve_named(target_ref, all_features, kind="feature")
        source_id = source.get("featureId")
        target_id = target.get("featureId")
        if not source_id or not target_id:
            raise ToolError("Feature mapping resolved to a feature without a featureId.")
        resolved[source_id] = target_id
    return resolved


def build_plan(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    *,
    max_calls: int = DEFAULT_MAX_CALLS,
) -> dict[str, Any]:
    assembly_url = snapshot.get("assemblyUrl")
    if not isinstance(assembly_url, str):
        raise ToolError("Snapshot has no assemblyUrl.")
    AssemblyRef.parse(assembly_url)
    all_instances = root_instances(snapshot)
    all_features = features(snapshot)
    default_mates = config.get("sourceMates", [])
    copies = config.get("copies")
    if not isinstance(default_mates, list) or not all(isinstance(x, str) for x in default_mates):
        raise ToolError("config.sourceMates must be an array of mate names or IDs.")
    if not isinstance(copies, list) or not copies:
        raise ToolError("config.copies must contain at least one copy definition.")

    planned: list[dict[str, Any]] = []
    planned_names: set[str] = set()
    existing_names = {item.get("name") for item in all_features}
    warnings: list[str] = []

    for copy_index, copy_config in enumerate(copies, start=1):
        if not isinstance(copy_config, dict):
            raise ToolError("Each copy definition must be an object.")
        label = str(copy_config.get("label") or f"copy-{copy_index}")
        configured_map = copy_config.get("instanceMap")
        if not isinstance(configured_map, dict) or not configured_map:
            raise ToolError(f"Copy {label!r} needs a non-empty instanceMap object.")
        instance_map: dict[str, str] = {}
        for source_ref, target_ref in configured_map.items():
            if not isinstance(source_ref, str) or not isinstance(target_ref, str):
                raise ToolError(f"Copy {label!r} instanceMap keys and values must be strings.")
            source = resolve_named(source_ref, all_instances, kind="instance")
            target = resolve_named(target_ref, all_instances, kind="instance")
            source_id = source.get("id")
            target_id = target.get("id")
            if not source_id or not target_id:
                raise ToolError("Instance mapping resolved to an instance without an ID.")
            if source_id == target_id:
                raise ToolError(f"Copy {label!r} maps {source_ref!r} to itself.")
            if instance_identity(source) != instance_identity(target):
                if not copy_config.get("allowDifferentParts", False):
                    raise ToolError(
                        f"Copy {label!r} maps {source_ref!r} to {target_ref!r}, but they "
                        "are not instances of the same part/configuration. Use Transform "
                        "for symmetric Assembly Mirror parts, or explicitly set "
                        "allowDifferentParts after verifying persistent references."
                    )
                warnings.append(
                    f"{label}: {source_ref} -> {target_ref} uses different part identities; "
                    "verify every mate connector in Onshape."
                )
            instance_map[source_id] = target_id

        configured_feature_map = copy_config.get("featureMap", {})
        if not isinstance(configured_feature_map, dict):
            raise ToolError(f"Copy {label!r} featureMap must be an object.")
        feature_map = resolve_feature_map(configured_feature_map, all_features)
        configured_shared = copy_config.get("sharedInstances", [])
        if not isinstance(configured_shared, list) or not all(
            isinstance(item, str) for item in configured_shared
        ):
            raise ToolError(f"Copy {label!r} sharedInstances must be an array of names or IDs.")
        shared_instance_ids = {
            resolve_named(item, all_instances, kind="instance").get("id")
            for item in configured_shared
        }
        known_instance_ids = {
            item["id"] for item in all_instances if isinstance(item.get("id"), str)
        }
        identity_counts: dict[tuple[Any, ...], int] = {}
        instances_by_id = {
            item["id"]: item
            for item in all_instances
            if isinstance(item.get("id"), str)
        }
        for item in all_instances:
            identity = instance_identity(item)
            identity_counts[identity] = identity_counts.get(identity, 0) + 1
        source_mates = copy_config.get("sourceMates", default_mates)
        if not isinstance(source_mates, list) or not source_mates:
            raise ToolError(f"Copy {label!r} has no source mates.")
        mirror_limits = copy_config.get("mirrorLimits", True)
        if not isinstance(mirror_limits, bool):
            raise ToolError(f"Copy {label!r} mirrorLimits must be true or false.")
        name_template = copy_config.get("nameTemplate", "{source} - {label}")
        if not isinstance(name_template, str):
            raise ToolError(f"Copy {label!r} nameTemplate must be a string.")

        for source_mate_ref in source_mates:
            if not isinstance(source_mate_ref, str):
                raise ToolError(f"Copy {label!r} source mate references must be strings.")
            source_feature = resolve_named(source_mate_ref, all_features, kind="feature")
            if not is_revolute(source_feature):
                raise ToolError(f"{source_mate_ref!r} is not a revolute mate.")
            referenced_ids = referenced_instance_ids(
                source_feature, known_instance_ids
            )
            ambiguous_unmapped = sorted(
                instance_id
                for instance_id in referenced_ids
                if instance_id not in instance_map
                and instance_id not in shared_instance_ids
                and identity_counts[instance_identity(instances_by_id[instance_id])] > 1
            )
            if ambiguous_unmapped:
                names = [
                    instances_by_id[instance_id].get("name", instance_id)
                    for instance_id in ambiguous_unmapped
                ]
                raise ToolError(
                    f"Copy {label!r}, mate {source_mate_ref!r}: duplicated referenced "
                    f"instance(s) were left unmapped: {', '.join(names)}. Add each "
                    "source-to-target mapping, or list an intentionally shared "
                    "instance in sharedInstances."
                )
            cloned = clean_generated_fields(copy.deepcopy(source_feature))
            changes = rewrite_references(cloned, instance_map, feature_map)
            if changes == 0:
                raise ToolError(
                    f"Copy {label!r}, mate {source_mate_ref!r}: no connector reference "
                    "was mapped. Check instanceMap; assembly-level explicit mate "
                    "connectors also need featureMap entries."
                )
            if mirror_limits:
                mirror_revolute_limits(cloned)
            try:
                new_name = name_template.format(
                    source=source_feature.get("name", source_mate_ref),
                    label=label,
                    index=copy_index,
                )
            except (KeyError, ValueError) as exc:
                raise ToolError(f"Invalid nameTemplate for copy {label!r}: {exc}") from exc
            if new_name in existing_names or new_name in planned_names:
                raise ToolError(
                    f"Refusing duplicate destination feature name {new_name!r}. "
                    "The plan may already have been applied."
                )
            cloned["name"] = new_name
            planned_names.add(new_name)
            planned.append(
                {
                    "sourceFeatureId": source_feature.get("featureId"),
                    "sourceName": source_feature.get("name"),
                    "copy": label,
                    "requestBody": {"feature": cloned},
                }
            )

    if len(planned) > max_calls:
        raise ToolError(
            f"Plan needs {len(planned)} write calls, exceeding the {max_calls}-call "
            "budget. Split the work or deliberately raise --max-api-calls."
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "assemblyUrl": assembly_url,
        "apiVersion": DEFAULT_API_VERSION,
        "apiCalls": len(planned),
        "warnings": warnings,
        "features": planned,
    }


def print_snapshot_summary(snapshot: dict[str, Any]) -> None:
    print("Instances (use the ID if a name is duplicated):")
    for item in root_instances(snapshot):
        print(
            f"  {item.get('name', '<unnamed>')} | id={item.get('id')} | "
            f"part={item.get('partId', '-')} | type={item.get('type', '-')}"
        )
    print("\nRevolute mates:")
    revolutes = [item for item in features(snapshot) if is_revolute(item)]
    for item in revolutes:
        minimum = parameter(item, "limitAxialZMin") or {}
        maximum = parameter(item, "limitAxialZMax") or {}
        print(
            f"  {item.get('name', '<unnamed>')} | id={item.get('featureId')} | "
            f"limits={minimum.get('expression', '-')}..{maximum.get('expression', '-')}"
        )
    print(f"\nFound {len(root_instances(snapshot))} instances and {len(revolutes)} revolute mates.")


def credentials() -> tuple[str, str]:
    return os.environ.get("ONSHAPE_ACCESS_KEY", ""), os.environ.get(
        "ONSHAPE_SECRET_KEY", ""
    )


def command_fetch(args: argparse.Namespace) -> None:
    assembly = AssemblyRef.parse(args.assembly_url)
    access_key, secret_key = credentials()
    client = OnshapeClient(
        assembly,
        access_key,
        secret_key,
        api_version=args.api_version,
        max_calls=args.max_api_calls,
    )
    definition, _ = client.request(
        "GET",
        assembly.assembly_path(),
        query={
            "includeMateFeatures": "true",
            "includeMateConnectors": "true",
            "includeNonSolids": "false",
            "excludeSuppressed": "true",
        },
    )
    feature_response, headers = client.request(
        "GET", assembly.assembly_path("/features")
    )
    snapshot = make_snapshot(args.assembly_url, definition, feature_response)
    write_json(args.output, snapshot)
    remaining = headers.get("x-rate-limit-remaining", "not reported")
    print(
        f"Saved {args.output}. API calls attempted: {client.calls_attempted}; "
        f"endpoint rate-limit remaining: {remaining}."
    )


def command_combine(args: argparse.Namespace) -> None:
    snapshot = make_snapshot(
        args.assembly_url, read_json(args.definition), read_json(args.features)
    )
    write_json(args.output, snapshot)
    print(f"Saved {args.output}. API calls made by this tool: 0.")


def command_inspect(args: argparse.Namespace) -> None:
    print_snapshot_summary(read_json(args.snapshot))


def command_plan(args: argparse.Namespace) -> None:
    plan = build_plan(
        read_json(args.snapshot),
        read_json(args.config),
        max_calls=args.max_api_calls,
    )
    write_json(args.output, plan)
    print(f"Saved {args.output}: {plan['apiCalls']} write call(s), 0 calls made now.")
    for warning in plan["warnings"]:
        print(f"WARNING: {warning}")


def command_apply(args: argparse.Namespace) -> None:
    if not args.yes:
        raise ToolError(
            "Apply is write-only and requires --yes. Review the plan and create an "
            "Onshape version first."
        )
    plan = read_json(args.plan)
    planned_features = plan.get("features")
    if not isinstance(planned_features, list):
        raise ToolError("Plan has no features array.")
    if len(planned_features) > args.max_api_calls:
        raise ToolError(
            f"Plan requires {len(planned_features)} calls; budget is {args.max_api_calls}."
        )
    if plan.get("apiCalls") != len(planned_features):
        raise ToolError("Plan apiCalls does not match its features array.")
    assembly = AssemblyRef.parse(plan.get("assemblyUrl", ""))
    access_key, secret_key = credentials()
    client = OnshapeClient(
        assembly,
        access_key,
        secret_key,
        api_version=plan.get("apiVersion", DEFAULT_API_VERSION),
        max_calls=args.max_api_calls,
    )
    created: list[str] = []
    for planned in planned_features:
        body = planned.get("requestBody")
        if not isinstance(body, dict) or not isinstance(body.get("feature"), dict):
            raise ToolError("Plan contains an invalid requestBody.")
        name = body["feature"].get("name", "<unnamed>")
        try:
            client.request("POST", assembly.assembly_path("/features"), body=body)
        except ToolError as exc:
            raise ToolError(
                f"Stopped after creating {len(created)} mate(s) {created}. "
                f"Failed while creating {name!r}: {exc}"
            ) from exc
        created.append(name)
        print(f"Created {name}")
    print(
        f"Done: created {len(created)} independent revolute mate(s) using "
        f"{client.calls_attempted} API call(s). Verify limits and motion in Onshape."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy independent revolute mates to mirrored Onshape instances."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch and cache assembly JSON (2 calls).")
    fetch.add_argument("--assembly-url", required=True)
    fetch.add_argument("--output", required=True)
    fetch.add_argument("--api-version", default=DEFAULT_API_VERSION)
    fetch.add_argument("--max-api-calls", type=int, default=DEFAULT_MAX_CALLS)
    fetch.set_defaults(function=command_fetch)

    combine = subparsers.add_parser(
        "combine", help="Combine two manually saved API responses (0 calls)."
    )
    combine.add_argument("--assembly-url", required=True)
    combine.add_argument("--definition", required=True)
    combine.add_argument("--features", required=True)
    combine.add_argument("--output", required=True)
    combine.set_defaults(function=command_combine)

    inspect = subparsers.add_parser("inspect", help="List cached instances and mates.")
    inspect.add_argument("--snapshot", required=True)
    inspect.set_defaults(function=command_inspect)

    plan = subparsers.add_parser("plan", help="Build a reviewed write plan (0 calls).")
    plan.add_argument("--snapshot", required=True)
    plan.add_argument("--config", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--max-api-calls", type=int, default=DEFAULT_MAX_CALLS)
    plan.set_defaults(function=command_plan)

    apply = subparsers.add_parser("apply", help="Create mates from a reviewed plan.")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--max-api-calls", type=int, default=DEFAULT_MAX_CALLS)
    apply.add_argument("--yes", action="store_true")
    apply.set_defaults(function=command_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "max_api_calls", 1) < 1:
        parser.error("--max-api-calls must be at least 1.")
    try:
        args.function(args)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
