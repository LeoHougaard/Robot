"""Tensor implementations of the recorded servo and Pixel sensor contracts.

No Isaac imports: these transformations are testable on CPU. Policy joints are
logical radians; the calibrated knee encoder measures knee plus parent hip.
"""
import math
import torch


PARENTS = (-1, -1, 1, -1, -1, 4, -1, -1, 7, -1, -1, 10)


def policy_to_motor(position):
    result = position.clone()
    result[..., (2, 5, 8, 11)] += position[..., (1, 4, 7, 10)]
    return result


def motor_to_policy(position):
    result = position.clone()
    result[..., (2, 5, 8, 11)] -= position[..., (1, 4, 7, 10)]
    return result


def gravity_estimate(previous, acceleration_mg, gyro, dt, fresh):
    """Match Pixel GravityEstimator, including acceleration confidence."""
    magnitude = torch.linalg.vector_norm(acceleration_mg, dim=-1, keepdim=True).clamp_min(1e-6)
    acceleration_gravity = -acceleration_mg / magnitude
    predicted = previous + dt * torch.linalg.cross(previous, gyro)
    predicted /= torch.linalg.vector_norm(predicted, dim=-1, keepdim=True).clamp_min(1e-6)
    confidence = (1 - (magnitude / 1000 - 1).abs() / .35).clamp_min(0)
    correction = min(1., dt / max(.25, dt)) * confidence
    corrected = (1 - correction) * predicted + correction * acceleration_gravity
    result = torch.where(fresh[:, None], acceleration_gravity, corrected)
    return result / torch.linalg.vector_norm(result, dim=-1, keepdim=True).clamp_min(1e-6)


class ServoTrajectory:
    """A bounded internal target in servo coordinates, advanced at physics dt.

    This predicts the effective trajectory seen in one gait recording. The
    simulator's force-limited servo drive still resolves load/contact response.
    It must not be combined with the older fitted first-order gait lag.
    """
    def __init__(self, fit, count, device, control_dt=.02):
        joints = sorted(fit["joints"], key=lambda x: x["policy_index"])
        assert [j["policy_index"] for j in joints] == list(range(12))
        if any(j["model"]["kind"] != "acceleration_limited" for j in joints):
            raise ValueError("this servo trajectory requires an acceleration-limited fit")
        self.acceleration_nominal = torch.tensor(
            [math.radians(j["model"]["acceleration_deg_s2"]) for j in joints], device=device)
        self.speed_nominal = torch.tensor(
            [math.radians(j["speed_limit_deg_s"]) for j in joints], device=device)
        self.delay_nominal = torch.tensor(
            [round(j["model"]["delay_s"] / control_dt) for j in joints], device=device, dtype=torch.long)
        self.acceleration = self.acceleration_nominal.expand(count, -1).clone()
        self.speed = self.speed_nominal.expand(count, -1).clone()
        self.delay = self.delay_nominal.expand(count, -1).clone()
        self.position = torch.zeros(count, 12, device=device)
        self.velocity = torch.zeros_like(self.position)
        self.commands = torch.zeros(count, int(self.delay_nominal.max()) + 2, 12, device=device)
        self.requested = torch.zeros_like(self.position)

    def reset(self, env_ids, policy_position, randomize=False):
        motor = policy_to_motor(policy_position)
        self.position[env_ids] = motor
        self.velocity[env_ids] = 0
        self.commands[env_ids] = motor[:, None, :]
        self.requested[env_ids] = motor
        if randomize:
            self.acceleration[env_ids] = self.acceleration_nominal * torch.empty_like(motor).uniform_(.75, 1.25)
            self.speed[env_ids] = self.speed_nominal * torch.empty_like(motor).uniform_(.85, 1.05)
            # An extra causal control-frame delay covers unmeasured transport
            # and load variation without inventing a different clock input.
            self.delay[env_ids] = self.delay_nominal + torch.randint(0, 2, motor.shape, device=motor.device)
        else:
            self.acceleration[env_ids] = self.acceleration_nominal
            self.speed[env_ids] = self.speed_nominal
            self.delay[env_ids] = self.delay_nominal

    def command(self, policy_position):
        self.commands = torch.roll(self.commands, -1, dims=1)
        self.commands[:, -1] = policy_to_motor(policy_position)
        index = (self.commands.shape[1] - 1 - self.delay).unsqueeze(1)
        self.requested = self.commands.gather(1, index).squeeze(1)

    def step(self, dt):
        error = self.requested - self.position
        desired = error.sign() * torch.minimum(self.speed, (2 * self.acceleration * error.abs()).sqrt())
        self.velocity += (desired - self.velocity).clamp(-self.acceleration * dt, self.acceleration * dt)
        self.position += self.velocity * dt
        return motor_to_policy(self.position)
