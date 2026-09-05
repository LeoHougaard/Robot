"""Preserve V20's separate RL-Games critic optimizer across future resumes."""
import warnings

from rl_games.algos_torch.a2c_continuous import A2CAgent


class DeliveryA2CAgent(A2CAgent):
    def get_full_state_weights(self):
        weights = super().get_full_state_weights()
        weights["delivery_actor_schedule"] = dict(last_lr=self.last_lr, entropy_coef=self.entropy_coef)
        if self.has_central_value:
            value = self.central_value_net
            weights["delivery_central_training"] = dict(
                optimizer=value.optimizer.state_dict(), epoch=value.epoch_num,
                frame=value.frame, lr=value.lr)
        return weights

    def set_full_state_weights(self, weights, set_epoch=True):
        super().set_full_state_weights(weights, set_epoch=set_epoch)
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
