"""Reject mismatched actors, replayed suites and changed evidence at export."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from delivery_provenance import sha, validate
from test_delivery_evaluation import ideal_delivery


class DeliveryProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        segments = ideal_delivery()
        lines = []
        for name, record in segments.items():
            def value(item):
                return ",".join(map(str, item)) if isinstance(item, list) else str(item)
            lines.append("EVAL_SEGMENT " + " ".join(f"{key}={value(item)}" for key,item in record.items()))
        (self.root / "console.log").write_text("\n".join(lines))
        sources = {}
        for name in ("evaluate_simple_dog_policy.py", "delivery_contract.py", "deployable_dynamics.py",
                     "simple_dog_task_current_body_v20/env.py", "simple_dog_task_current_body_v20/env_cfg.py",
                     "fits/servo-response-20260829.json"):
            path = self.root / "source" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).parent / name, path)
            sources[name] = sha(path)
        (self.root / "video.mp4").write_bytes(b"test fixture, not a real rollout")
        (self.root / "resolved_env.yaml").write_text("test: true\n")
        (self.root / "resolved_agent.yaml").write_text("test: true\n")
        self.result = dict(stage="deliveryflat", passed=True, segments=segments, player_exit=0,
                           console_sha256=sha(self.root / "console.log"),
                           videos=[dict(path="video.mp4",sha256=sha(self.root/"video.mp4"))],
                           resolved_config_hashes={name:sha(self.root/name) for name in ("resolved_env.yaml","resolved_agent.yaml")},
                           provenance=dict(checkpoint_sha256="a"*64,profile_sha256="b"*64,simulation_fit_sha256="c"*64,
                                           suite="deliveryflat",task="Isaac-Locomotion-CurrentBodyV20-Flat-Eval-Simple-Dog-Direct-v0",
                                           deterministic=True,control_hz=50,source_files=sources))
        (self.root / "visual_review.json").write_text(json.dumps(dict(decision="pass",notes="test fixture only",
            checkpoint_sha256="a"*64,video_sha256=sha(self.root/"video.mp4"))))
        self.save()

    def save(self):
        (self.root / "result.json").write_text(json.dumps(self.result))

    def check(self, checkpoint="a"*64, suite="deliveryflat"):
        return validate(self.root/"result.json",checkpoint,"b"*64,"c"*64,suite)

    def test_bound_evidence_passes_and_wrong_actor_or_suite_fails(self):
        self.assertTrue(self.check()["passed"])
        with self.assertRaises(ValueError): self.check(checkpoint="d"*64)
        with self.assertRaises(ValueError): self.check(suite="deliverystress")

    def test_changed_console_cannot_be_hidden_by_passed_flag(self):
        (self.root/"console.log").write_text("missing rollout")
        with self.assertRaises(ValueError): self.check()

    def test_changed_video_rejected(self):
        (self.root/"video.mp4").write_bytes(b"different actor's video")
        with self.assertRaises(ValueError): self.check()

    def test_changed_source_rejected(self):
        (self.root/"source/delivery_contract.py").write_text("changed commands")
        with self.assertRaises(ValueError): self.check()

    def test_relabeling_flat_as_stress_does_not_pass(self):
        self.result["stage"]="deliverystress"
        self.result["provenance"]["suite"]="deliverystress"
        self.save()
        with self.assertRaises(ValueError): self.check(suite="deliverystress")


if __name__ == "__main__":
    unittest.main()
