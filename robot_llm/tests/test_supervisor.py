"""Offline event traces. No test imports robot drivers or opens a connection."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import threading
import unittest

from robot_llm import (CapabilitySnapshot, ContractError, HostEnvelope, OutcomeState,
                       ShadowAuthorization, ShadowSupervisor)

ROOT = Path(__file__).resolve().parents[2]
METADATA_PATH = ROOT / "pixel_robot/app/src/main/assets/policy_metadata.json"


def metadata():
    return json.loads(METADATA_PATH.read_text(encoding="utf-8"))


def velocity(**updates):
    value = {"schema_version": 1, "kind": "velocity_request", "frame": "body",
             "forward_m_s": 0.04, "lateral_m_s": 0.0, "yaw_rate_rad_s": 0.0,
             "duration_ms": 100}
    return json.dumps(dict(value, **updates))


class CapabilityTests(unittest.TestCase):
    def test_current_active_metadata_not_readme_defaults(self):
        caps = CapabilitySnapshot.from_metadata_json(METADATA_PATH.read_text())
        self.assertEqual(caps.forward_m_s, (-0.04, 0.04))
        self.assertEqual(caps.lateral_m_s, (-0.02, 0.02))
        self.assertEqual(caps.yaw_rate_rad_s, (-0.1, 0.1))
        self.assertTrue(caps.requires_unqualified_test_opt_in)
        self.assertFalse(caps.promoted)
        self.assertFalse(caps.physical_transport_verified)

    def test_digest_binds_every_metadata_field_and_duration(self):
        original = metadata()
        caps = CapabilitySnapshot.from_metadata(original)
        for key, value in (("checkpoint_epoch", 3751), ("new_evidence", "changed"),
                           ("promoted", True), ("weights_sha256", "0" * 64)):
            changed = dict(original, **{key: value})
            self.assertNotEqual(caps.digest, CapabilitySnapshot.from_metadata(changed).digest)
        self.assertNotEqual(caps.digest, CapabilitySnapshot.from_metadata(original, max_duration_ms=500).digest)
        original["validated_command_limits"]["forward_m_s"][1] = 100
        self.assertEqual(caps.forward_m_s, (-0.04, 0.04))

    def test_bad_or_missing_metadata_fails_closed(self):
        for key in ("schema_version", "validated_command_limits", "promoted", "weights_sha256",
                    "physical_transport_verified", "deployment_tier", "profile_sha256", "checkpoint_sha256"):
            value = metadata()
            del value[key]
            with self.subTest(missing=key), self.assertRaises(ContractError):
                CapabilitySnapshot.from_metadata(value)
        for key, bad in (("schema_version", True), ("schema_version", 3),
                         ("promoted", "false"), ("physical_transport_verified", 1),
                         ("weights_sha256", "not-a-hash")):
            value = metadata()
            value[key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ContractError):
                CapabilitySnapshot.from_metadata(value)
        for bad in ([0.1, 0.2], [0.1, -0.1], [False, 1], ["0", 1], [0, float("inf")], [0], "0,1"):
            value = metadata()
            value["validated_command_limits"]["forward_m_s"] = bad
            with self.subTest(bounds=bad), self.assertRaises(ContractError):
                CapabilitySnapshot.from_metadata(value)
        value = metadata()
        value["validated_command_limits"]["forward_cm_s"] = [0, 1]
        with self.assertRaises(ContractError):
            CapabilitySnapshot.from_metadata(value)
        with self.assertRaises(ContractError):
            CapabilitySnapshot.from_metadata_json(METADATA_PATH.read_text().replace('"schema_version": 2', '"schema_version": 2,"schema_version":2'))


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.caps = CapabilitySnapshot.from_metadata(metadata())
        self.sup = ShadowSupervisor(session_id="s1", capabilities=self.caps)
        self.auth = ShadowAuthorization("s1", "operator", self.caps.digest, 10_000,
                                        allow_unqualified_metadata=True)

    def begin(self, request_id="r1", source_id="operator", now=0, ttl=2_000):
        return self.sup.begin_request(source_id=source_id, request_id=request_id,
                                      now_ms=now, ttl_ms=ttl)

    def submit(self, envelope, raw=None, now=1, auth="default"):
        return self.sup.submit(envelope, raw if raw is not None else velocity(), now_ms=now,
                               authorization=self.auth if auth == "default" else auth)

    def test_acceptance_is_only_shadow_and_does_not_claim_completion(self):
        result = self.submit(self.begin())
        self.assertEqual(result.state, OutcomeState.SHADOW_ACCEPTED)
        self.assertEqual(result.lease_expires_at_ms, 101)
        self.assertFalse(result.physical_stop_confirmed)
        self.assertNotIn("succeeded", [state.value for state in OutcomeState])
        self.assertEqual(self.sup.outcome("r1", now_ms=100).state, OutcomeState.SHADOW_ACCEPTED)
        expired = self.sup.outcome("r1", now_ms=101)
        self.assertEqual(expired.state, OutcomeState.EXPIRED)
        self.assertIn("no physical outcome", expired.reason)

    def test_grant_required_even_when_metadata_claims_promoted(self):
        for promoted in (False, True):
            value = metadata()
            value["promoted"] = promoted
            value["physical_transport_verified"] = promoted
            caps = CapabilitySnapshot.from_metadata(value)
            sup = ShadowSupervisor(session_id="s1", capabilities=caps)
            env = sup.begin_request(source_id="operator", request_id="r1", now_ms=0, ttl_ms=1000)
            self.assertEqual(sup.submit(env, velocity(), now_ms=1).state, OutcomeState.REJECTED)

    def test_unqualified_metadata_needs_specific_test_opt_in(self):
        auth = replace(self.auth, allow_unqualified_metadata=False)
        result = self.submit(self.begin(), auth=auth)
        self.assertEqual(result.state, OutcomeState.REJECTED)
        self.assertIn("unqualified", result.reason)
        with self.assertRaises(ContractError):
            replace(self.auth, allow_unqualified_metadata="true")

    def test_grant_bound_to_session_source_capability_and_expiry(self):
        for updates in ({"session_id": "old"}, {"source_id": "different"},
                        {"capability_digest": "0" * 64}, {"expires_at_ms": 1}):
            with self.subTest(updates=updates):
                result = self.submit(self.begin(request_id=str(updates), now=1),
                                     auth=replace(self.auth, **updates))
                self.assertEqual(result.state, OutcomeState.REJECTED)

    def test_bounds_rejected_not_clamped_and_units_not_inferred(self):
        bad = [{"forward_m_s": 0.040001}, {"forward_m_s": -0.040001},
               {"lateral_m_s": 0.020001}, {"lateral_m_s": -0.020001},
               {"yaw_rate_rad_s": 0.100001}, {"yaw_rate_rad_s": -0.100001},
               {"duration_ms": 1001}, {"forward_m_s": 4}, {"frame": "world"}]
        for index, updates in enumerate(bad):
            with self.subTest(updates=updates):
                result = self.submit(self.begin(request_id=f"r{index}", now=1), velocity(**updates))
                self.assertEqual(result.state, OutcomeState.REJECTED)

    def test_limits_inclusive_and_shadow_lease_exclusive(self):
        env = self.begin()
        self.assertEqual(self.submit(env, velocity(forward_m_s=-0.04, lateral_m_s=-0.02,
                                                   yaw_rate_rad_s=0.1)).state, OutcomeState.SHADOW_ACCEPTED)
        other = self.begin(request_id="r2", source_id="other", now=1)
        auth = replace(self.auth, source_id="other")
        result = self.submit(other, auth=auth)
        self.assertEqual(result.state, OutcomeState.REJECTED)
        self.assertIn("owns the shadow lease", result.reason)

    def test_deadline_never_renews_and_duration_must_fit(self):
        env = self.begin(ttl=100)
        result = self.submit(env, velocity(duration_ms=100), now=1)
        self.assertEqual(result.state, OutcomeState.REJECTED)
        self.assertIn("original", result.reason)
        env2 = self.begin(request_id="r2", now=1, ttl=100)
        self.assertEqual(self.submit(env2, now=1).state, OutcomeState.SHADOW_ACCEPTED)
        self.assertEqual(self.submit(env2, now=99).state, OutcomeState.REJECTED)
        self.assertEqual(self.sup.outcome("r2", now_ms=101).state, OutcomeState.EXPIRED)

    def test_authorization_duration_also_bounds_lease(self):
        result = self.submit(self.begin(), auth=replace(self.auth, expires_at_ms=100))
        self.assertEqual(result.state, OutcomeState.REJECTED)

    def test_expired_response_cannot_replace_terminal_outcome(self):
        env = self.begin(ttl=1)
        result = self.submit(env, now=1)
        self.assertEqual(result.state, OutcomeState.REJECTED)
        self.assertEqual(self.sup.outcome("r1", now_ms=1).state, OutcomeState.EXPIRED)

    def test_original_envelope_cannot_be_forged_or_extended(self):
        env = self.begin()
        for altered in (replace(env, expires_at_ms=10_000), replace(env, source_id="other"),
                        replace(env, session_id="other"), replace(env, request_id="unknown"),
                        replace(env, capability_digest="0" * 64), replace(env, source_generation=1)):
            self.assertEqual(self.submit(altered).state, OutcomeState.REJECTED)
        self.assertEqual(self.submit(env).state, OutcomeState.SHADOW_ACCEPTED)
        with self.assertRaises(ContractError):
            replace(env, issued_at_ms=False)

    def test_request_ids_cannot_be_reused_after_reject_cancel_or_expiry(self):
        env = self.begin()
        self.submit(env, "invalid")
        with self.assertRaises(ContractError):
            self.begin(now=1)
        self.assertEqual(self.submit(env).state, OutcomeState.REJECTED)

    def test_source_cancel_before_and_after_response_fences_old_work(self):
        old = self.begin()
        self.sup.cancel_source("operator", now_ms=1)
        self.assertEqual(self.submit(old).state, OutcomeState.REJECTED)
        self.assertEqual(self.sup.outcome("r1", now_ms=1).state, OutcomeState.CANCELLED)
        new = self.begin(request_id="r2", now=1)
        self.assertEqual(new.source_generation, old.source_generation + 1)
        self.assertEqual(self.submit(new).state, OutcomeState.SHADOW_ACCEPTED)
        self.sup.cancel_source("operator", now_ms=2)
        self.assertEqual(self.sup.outcome("r2", now_ms=2).state, OutcomeState.CANCELLED)
        self.assertEqual(self.submit(new, now=2).state, OutcomeState.REJECTED)

    def test_source_cancel_does_not_cancel_other_source(self):
        env = self.begin(source_id="other")
        self.sup.cancel_source("operator", now_ms=1)
        self.assertEqual(self.submit(env, auth=replace(self.auth, source_id="other")).state,
                         OutcomeState.SHADOW_ACCEPTED)

    def test_capability_change_cancels_pending_and_active(self):
        env = self.begin()
        self.assertEqual(self.submit(env).state, OutcomeState.SHADOW_ACCEPTED)
        pending = self.begin(request_id="r2", now=1)
        changed = metadata()
        changed["validated_command_limits"]["forward_m_s"] = [-0.02, 0.02]
        self.sup.replace_capabilities(CapabilitySnapshot.from_metadata(changed), now_ms=2)
        for old in (env, pending):
            self.assertEqual(self.sup.outcome(old.request_id, now_ms=2).state, OutcomeState.CANCELLED)
            self.assertEqual(self.submit(old, now=2).state, OutcomeState.REJECTED)
        new = self.begin(request_id="r3", now=2)
        self.assertNotEqual(new.capability_digest, env.capability_digest)
        self.assertEqual(self.submit(new, now=2).state, OutcomeState.REJECTED)  # old grant

    def test_deterministic_stop_bypasses_model_and_latches(self):
        active = self.begin()
        self.assertEqual(self.submit(active).state, OutcomeState.SHADOW_ACCEPTED)
        pending = self.begin(request_id="r2", now=1)
        stopped = self.sup.request_stop(now_ms=2)
        self.assertEqual(stopped.state, OutcomeState.STOP_REQUESTED)
        self.assertFalse(stopped.physical_stop_confirmed)
        for env in (active, pending):
            self.assertEqual(self.sup.outcome(env.request_id, now_ms=2).state, OutcomeState.CANCELLED)
            self.assertEqual(self.submit(env, now=2).state, OutcomeState.REJECTED)
        with self.assertRaises(ContractError):
            self.begin(request_id="new", now=2)
        self.assertFalse(self.sup.request_stop(now_ms=2).physical_stop_confirmed)

    def test_model_stop_not_blocked_by_missing_motion_authorization(self):
        env = self.begin()
        result = self.submit(env, '{"schema_version":1,"kind":"stop_request"}', auth=None)
        self.assertEqual(result.state, OutcomeState.STOP_REQUESTED)
        self.assertFalse(result.physical_stop_confirmed)

    def test_clarification_unsupported_do_not_need_motion_grant(self):
        for index, (kind, field, text) in enumerate((("clarify", "question", "Which direction?"),
                                                    ("unsupported", "reason", "No navigation"))):
            env = self.begin(request_id=f"r{index}", now=1)
            result = self.submit(env, json.dumps({"schema_version": 1, "kind": kind, field: text}), auth=None)
            self.assertEqual(result.state.value, kind)
            self.assertIsNone(result.lease_expires_at_ms)

    def test_monotonic_time_inputs_reject_bool_and_rollback(self):
        self.begin(now=10)
        for now in (9, True, 10.0, -1):
            with self.subTest(now=now), self.assertRaises(ContractError):
                self.sup.expire(now_ms=now)

    def test_newer_request_fences_delayed_older_response_after_lease_expires(self):
        older = self.begin()
        newer = self.begin(request_id="r2")
        self.assertEqual(self.sup.outcome("r1", now_ms=0).state, OutcomeState.CANCELLED)
        self.assertEqual(self.submit(newer).state, OutcomeState.SHADOW_ACCEPTED)
        self.assertEqual(self.sup.outcome("r2", now_ms=101).state, OutcomeState.EXPIRED)
        self.assertEqual(self.submit(older, now=102).state, OutcomeState.REJECTED)
        self.assertEqual(self.sup.outcome("r1", now_ms=102).state, OutcomeState.CANCELLED)

    def test_new_request_does_not_silently_preempt_active_shadow_lease(self):
        active = self.begin()
        self.assertEqual(self.submit(active).state, OutcomeState.SHADOW_ACCEPTED)
        newer = self.begin(request_id="r2", now=1)
        self.assertEqual(self.sup.outcome("r1", now_ms=1).state, OutcomeState.SHADOW_ACCEPTED)
        self.assertEqual(self.submit(newer).state, OutcomeState.REJECTED)
        self.assertEqual(self.sup.outcome("r1", now_ms=1).state, OutcomeState.SHADOW_ACCEPTED)

    def test_pending_supersession_is_scoped_to_source(self):
        other = self.begin(source_id="other")
        self.begin(request_id="r2")
        self.assertEqual(self.submit(other, auth=replace(self.auth, source_id="other")).state,
                         OutcomeState.SHADOW_ACCEPTED)

    def test_capability_round_trip_cannot_revive_original_envelope(self):
        original = self.begin()
        changed = metadata()
        changed["validated_command_limits"]["forward_m_s"] = [-0.02, 0.02]
        self.sup.replace_capabilities(CapabilitySnapshot.from_metadata(changed), now_ms=1)
        self.sup.replace_capabilities(self.caps, now_ms=1)
        self.assertEqual(self.submit(original).state, OutcomeState.REJECTED)
        self.assertEqual(self.sup.outcome("r1", now_ms=1).state, OutcomeState.CANCELLED)
        # Grants are digest-scoped test permissions, not a revocation registry.
        fresh = self.begin(request_id="r2", now=1)
        self.assertEqual(self.submit(fresh).state, OutcomeState.SHADOW_ACCEPTED)

    def test_replay_race_grants_exactly_one_shadow_lease(self):
        env = self.begin()
        barrier = threading.Barrier(2)
        def submit():
            barrier.wait()
            return self.submit(env).state
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit), pool.submit(submit)]
            states = [future.result() for future in futures]
        self.assertCountEqual(states, [OutcomeState.SHADOW_ACCEPTED, OutcomeState.REJECTED])
        self.assertEqual(self.sup.outcome("r1", now_ms=1).lease_expires_at_ms, 101)

    def test_concurrent_stop_and_submit_cannot_leave_a_live_lease(self):
        for index in range(25):
            sup = ShadowSupervisor(session_id="s1", capabilities=self.caps)
            env = sup.begin_request(source_id="operator", request_id=str(index), now_ms=0, ttl_ms=1000)
            barrier = threading.Barrier(2)
            def submit():
                barrier.wait()
                return sup.submit(env, velocity(), now_ms=1, authorization=self.auth)
            def stop():
                barrier.wait()
                return sup.request_stop(now_ms=1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = pool.submit(submit), pool.submit(stop)
                first.result()
                stopped = second.result()
            self.assertEqual(stopped.state, OutcomeState.STOP_REQUESTED)
            self.assertFalse(stopped.physical_stop_confirmed)
            self.assertEqual(sup.outcome(str(index), now_ms=1).state, OutcomeState.CANCELLED)
            with self.assertRaises(ContractError):
                sup.begin_request(source_id="operator", request_id="later", now_ms=1, ttl_ms=1000)

    def test_concurrent_cancel_and_submit_cannot_leave_a_live_lease(self):
        # Either ordering is valid. Atomic event handling must leave the final
        # result cancelled, and cannot resurrect it on a late response.
        for index in range(25):
            sup = ShadowSupervisor(session_id="s1", capabilities=self.caps)
            env = sup.begin_request(source_id="operator", request_id=str(index), now_ms=0, ttl_ms=1000)
            barrier = threading.Barrier(2)
            def submit():
                barrier.wait()
                return sup.submit(env, velocity(), now_ms=1, authorization=self.auth)
            def cancel():
                barrier.wait()
                sup.cancel_source("operator", now_ms=1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = pool.submit(submit), pool.submit(cancel)
                first.result()
                second.result()
            self.assertEqual(sup.outcome(str(index), now_ms=1).state, OutcomeState.CANCELLED)
            self.assertEqual(sup.submit(env, velocity(), now_ms=1, authorization=self.auth).state,
                             OutcomeState.REJECTED)


if __name__ == "__main__":
    unittest.main()
