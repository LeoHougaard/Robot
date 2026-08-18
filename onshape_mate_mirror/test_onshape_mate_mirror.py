import copy
import unittest

import onshape_mate_mirror as tool


def instance(identifier, name, part_id):
    return {
        "id": identifier,
        "name": name,
        "type": "Part",
        "documentId": "d" * 24,
        "elementId": "e" * 24,
        "partId": part_id,
        "configuration": "default",
    }


def revolute(identifier="mate-source", name="FR knee revolute"):
    return {
        "btType": "BTMMate-64",
        "featureType": "mate",
        "featureId": identifier,
        "nodeId": "generated",
        "featureListFieldIndex": 123,
        "name": name,
        "parameters": [
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "mateType",
                "value": "REVOLUTE",
                "nodeId": "generated",
            },
            {
                "btType": "BTMParameterQueryWithOccurrenceList-67",
                "parameterId": "mateConnectorsQuery",
                "queries": [
                    {
                        "btType": "BTMInferenceQueryWithOccurrence-1083",
                        "path": ["body"],
                        "deterministicIds": ["body-face"],
                    },
                    {
                        "btType": "BTMInferenceQueryWithOccurrence-1083",
                        "path": ["fr-upper"],
                        "deterministicIds": ["joint-face"],
                    },
                ],
            },
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "limitsEnabled",
                "value": True,
            },
            {
                "btType": "BTMParameterNullableQuantity-807",
                "parameterId": "limitAxialZMin",
                "expression": "-20 deg",
                "value": -0.349,
                "nullValue": "",
            },
            {
                "btType": "BTMParameterNullableQuantity-807",
                "parameterId": "limitAxialZMax",
                "expression": "45 deg",
                "value": 0.785,
                "nullValue": "",
            },
        ],
        "suppressed": False,
    }


def snapshot(mate_count=1):
    feature_list = [
        revolute(f"mate-{index}", f"FR joint {index}") for index in range(mate_count)
    ]
    return {
        "schemaVersion": 1,
        "assemblyUrl": (
            "https://cad.onshape.com/documents/" + "d" * 24 + "/w/" + "a" * 24
            + "/e/" + "e" * 24
        ),
        "definition": {
            "rootAssembly": {
                "instances": [
                    instance("body", "Body <1>", "body-part"),
                    instance("fr-upper", "FR upper <1>", "upper-part"),
                    instance("fl-upper", "FL upper <1>", "upper-part"),
                ]
            }
        },
        "featureResponse": {"features": feature_list},
    }


class AssemblyRefTests(unittest.TestCase):
    def test_parse_workspace_url(self):
        ref = tool.AssemblyRef.parse(snapshot()["assemblyUrl"] + "?renderMode=0")
        self.assertEqual(ref.document_id, "d" * 24)
        self.assertEqual(ref.workspace_id, "a" * 24)

    def test_reject_version_url(self):
        with self.assertRaises(tool.ToolError):
            tool.AssemblyRef.parse(
                "https://cad.onshape.com/documents/" + "d" * 24 + "/v/" + "a" * 24
                + "/e/" + "e" * 24
            )

    def test_rejects_untrusted_api_stack(self):
        with self.assertRaisesRegex(tool.ToolError, "trusted cad.onshape.com"):
            tool.AssemblyRef.parse(
                "https://example.com/documents/" + "d" * 24 + "/w/" + "a" * 24
                + "/e/" + "e" * 24
            )

    def test_rejects_invalid_api_version(self):
        with self.assertRaisesRegex(tool.ToolError, "version"):
            tool.OnshapeClient(
                tool.AssemblyRef.parse(snapshot()["assemblyUrl"]),
                "access",
                "secret",
                api_version="../../v12",
            )


class LimitTests(unittest.TestCase):
    def test_mirrors_revolute_limit_interval(self):
        feature = revolute()
        tool.mirror_revolute_limits(feature)
        self.assertEqual(
            tool.parameter(feature, "limitAxialZMin")["expression"], "-45 deg"
        )
        self.assertEqual(
            tool.parameter(feature, "limitAxialZMax")["expression"], "20 deg"
        )

    def test_swaps_unbounded_limit_labels(self):
        feature = revolute()
        minimum = tool.parameter(feature, "limitAxialZMin")
        maximum = tool.parameter(feature, "limitAxialZMax")
        minimum["nullValue"] = "No minimum"
        maximum["nullValue"] = "No maximum"
        tool.mirror_revolute_limits(feature)
        self.assertEqual(
            tool.parameter(feature, "limitAxialZMin")["nullValue"], "No minimum"
        )
        self.assertEqual(
            tool.parameter(feature, "limitAxialZMax")["nullValue"], "No maximum"
        )


class PlanTests(unittest.TestCase):
    def test_plan_rewrites_instance_and_strips_generated_ids(self):
        source = snapshot()
        config = {
            "sourceMates": ["FR joint 0"],
            "copies": [
                {
                    "label": "front-left",
                    "instanceMap": {"FR upper <1>": "FL upper <1>"},
                }
            ],
        }
        plan = tool.build_plan(source, config)
        self.assertEqual(plan["apiCalls"], 1)
        feature = plan["features"][0]["requestBody"]["feature"]
        self.assertNotIn("featureId", feature)
        self.assertNotIn("nodeId", feature)
        self.assertNotIn("featureListFieldIndex", feature)
        queries = tool.parameter(feature, "mateConnectorsQuery")["queries"]
        self.assertEqual(queries[0]["path"], ["body"])
        self.assertEqual(queries[1]["path"], ["fl-upper"])
        self.assertEqual(feature["name"], "FR joint 0 - front-left")

    def test_does_not_mutate_snapshot(self):
        source = snapshot()
        before = copy.deepcopy(source)
        tool.build_plan(
            source,
            {
                "sourceMates": ["FR joint 0"],
                "copies": [
                    {
                        "label": "front-left",
                        "instanceMap": {"fr-upper": "fl-upper"},
                    }
                ],
            },
        )
        self.assertEqual(source, before)

    def test_rejects_ten_call_plan_by_default(self):
        source = snapshot(mate_count=10)
        config = {
            "sourceMates": [f"FR joint {index}" for index in range(10)],
            "copies": [
                {
                    "label": "front-left",
                    "instanceMap": {"fr-upper": "fl-upper"},
                }
            ],
        }
        with self.assertRaisesRegex(tool.ToolError, "10 write calls"):
            tool.build_plan(source, config)

    def test_rejects_different_part_identity(self):
        source = snapshot()
        source["definition"]["rootAssembly"]["instances"][2]["partId"] = "derived"
        with self.assertRaisesRegex(tool.ToolError, "not instances of the same part"):
            tool.build_plan(
                source,
                {
                    "sourceMates": ["FR joint 0"],
                    "copies": [
                        {
                            "label": "front-left",
                            "instanceMap": {"fr-upper": "fl-upper"},
                        }
                    ],
                },
            )

    def test_rejects_a_duplicated_leg_instance_left_unmapped(self):
        source = snapshot()
        source["definition"]["rootAssembly"]["instances"].extend(
            [
                instance("fr-lower", "FR lower <1>", "lower-part"),
                instance("fl-lower", "FL lower <1>", "lower-part"),
            ]
        )
        queries = tool.parameter(
            source["featureResponse"]["features"][0], "mateConnectorsQuery"
        )["queries"]
        queries.append(
            {
                "btType": "BTMInferenceQueryWithOccurrence-1083",
                "path": ["fr-lower"],
                "deterministicIds": ["lower-face"],
            }
        )
        with self.assertRaisesRegex(tool.ToolError, "left unmapped"):
            tool.build_plan(
                source,
                {
                    "sourceMates": ["FR joint 0"],
                    "copies": [
                        {
                            "label": "front-left",
                            "instanceMap": {"fr-upper": "fl-upper"},
                        }
                    ],
                },
            )


if __name__ == "__main__":
    unittest.main()
