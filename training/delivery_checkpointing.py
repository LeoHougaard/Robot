"""Preserve V20's separate RL-Games critic optimizer across future resumes."""
import warnings

import torch
from rl_games.algos_torch.a2c_continuous import A2CAgent


def freeze_exploration(model):
    """Keep the restored, state-independent exploration noise at its sane level.

    The raw Gaussian entropy is unbounded even though actuator actions are
    clipped. Freezing this parameter removes that incentive without changing
    the actor means, physical action path or PPO learning-rate schedule.
    """
    sigma = model.a2c_network.sigma
    if not isinstance(sigma, torch.nn.Parameter) or sigma.ndim != 1:
        raise ValueError("requires the reviewed state-independent log-standard-deviation actor")
    std = sigma.detach().exp()
    if not torch.isfinite(std).all() or not ((std >= .05) & (std <= 1.)).all():
        raise ValueError("refusing to freeze an already collapsed or excessive exploration distribution")
    sigma.requires_grad_(False)
    return std


class DeliveryA2CAgent(A2CAgent):
    def __init__(self, base_name, params):
        self._delivery_policy_contract = params["config"].get("delivery_policy_contract")
        super().__init__(base_name, params)
        self._freeze_delivery_sigma = bool(params["config"].get("freeze_exploration_std", False))
        if self._freeze_delivery_sigma:
            freeze_exploration(self.model)

    def get_full_state_weights(self):
        weights = super().get_full_state_weights()
        weights["delivery_actor_schedule"] = dict(last_lr=self.last_lr, entropy_coef=self.entropy_coef)
        if self._delivery_policy_contract is not None:
            weights["delivery_policy_contract"] = self._delivery_policy_contract
        if self.has_central_value:
            value = self.central_value_net
            weights["delivery_central_training"] = dict(
                optimizer=value.optimizer.state_dict(), epoch=value.epoch_num,
                frame=value.frame, lr=value.lr)
        return weights

    def set_full_state_weights(self, weights, set_epoch=True):
        if self._delivery_policy_contract != weights.get("delivery_policy_contract"):
            raise ValueError("checkpoint coordinate/reference contract does not match the task")
        super().set_full_state_weights(weights, set_epoch=set_epoch)
        if getattr(self, "_freeze_delivery_sigma", False):
            std = freeze_exploration(self.model)
            print(f"DELIVERY_FIXED_EXPLORATION std={std.cpu().tolist()}", flush=True)
        schedule = weights.get("delivery_actor_schedule")
        # Older checkpoints already contain the actor optimizer's actual LR.
        self.last_lr = (schedule["last_lr"] if schedule else self.optimizer.param_groups[0]["lr"])
        if schedule:
            self.entropy_coef = schedule["entropy_coef"]
        if self.has_central_value:
            value = self.central_value_net
            state = weights.get("delivery_central_training")
            if state:
                value.optimizer.load_state_dict(state["optimizer"])
                value.lr = state["lr"]
                if set_epoch:
                    value.epoch_num, value.frame = state["epoch"], state["frame"]
            elif weights["epoch"] > 0:
                warnings.warn("Legacy V20 checkpoint has no central critic optimizer. "
                              "Critic weights are restored, but critic Adam moments restart. "
                              "Future delivery checkpoints preserve them.", RuntimeWarning)
                if set_epoch:
                    value.epoch_num, value.frame = weights["epoch"], weights["frame"]
