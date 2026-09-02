"""CurrentBodyV14 compact-history variant; rewards and physics stay V13."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v4.simple_dog_current_body_v4_env_cfg import (
    V4_COMMAND_SIZE,
    V4_FRAME_SIZE,
)
from simple_dog_task_current_body_v13.simple_dog_current_body_v13_env_cfg import (
    SimpleDogCurrentBodyV13EvalEnvCfg,
    SimpleDogCurrentBodyV13HardEnvCfg,
    SimpleDogCurrentBodyV13PlayEnvCfg,
    SimpleDogCurrentBodyV13PushEvalEnvCfg,
)


# V4-V13 feed twelve 70-value frames (846 values including the current body
# command). V14 retains half-second context but selects six frames, reducing
# the actor input to 426 physically deployable values without changing reward.
V14_SELECTED_HISTORY_INDICES = (0, 5, 10, 15, 20, 23)
V14_OBSERVATION_SPACE = len(V14_SELECTED_HISTORY_INDICES) * V4_FRAME_SIZE + V4_COMMAND_SIZE


@configclass
class SimpleDogCurrentBodyV14HardEnvCfg(SimpleDogCurrentBodyV13HardEnvCfg):
    policy_family = "current_body_v14"
    selected_history_indices = V14_SELECTED_HISTORY_INDICES
    observation_space = V14_OBSERVATION_SPACE


@configclass
class SimpleDogCurrentBodyV14EvalEnvCfg(SimpleDogCurrentBodyV13EvalEnvCfg):
    policy_family = "current_body_v14"
    selected_history_indices = V14_SELECTED_HISTORY_INDICES
    observation_space = V14_OBSERVATION_SPACE


@configclass
class SimpleDogCurrentBodyV14PlayEnvCfg(SimpleDogCurrentBodyV13PlayEnvCfg):
    policy_family = "current_body_v14"
    selected_history_indices = V14_SELECTED_HISTORY_INDICES
    observation_space = V14_OBSERVATION_SPACE


@configclass
class SimpleDogCurrentBodyV14PushEvalEnvCfg(SimpleDogCurrentBodyV13PushEvalEnvCfg):
    policy_family = "current_body_v14"
    selected_history_indices = V14_SELECTED_HISTORY_INDICES
    observation_space = V14_OBSERVATION_SPACE
