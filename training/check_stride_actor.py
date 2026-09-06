"""Compare a V21 RL-Games actor with portable inference math without exporting it."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from export_v2_policy import TENSOR_KEYS, numpy_actor
import export_v2_policy
from initialize_delivery_stride import build_actor


def check(checkpoint, config, fixture, output):
    if output.exists():
        raise ValueError('refusing to overwrite actor evidence')
    torch.set_num_threads(1)
    _, actor = build_actor(config)
    state = torch.load(checkpoint, map_location='cpu', weights_only=False)
    actor.load_state_dict(state['model'], strict=True)
    actor.eval()
    arrays = {name: state['model'][key].detach().float().numpy() for name,key in TENSOR_KEYS.items()}
    if any(not np.isfinite(value).all() for value in arrays.values()) or (arrays['obs_var'] < 0).any():
        raise ValueError('nonfinite actor or invalid normalization variance')
    assert arrays['obs_mean'].shape == (428,)
    data = json.loads(fixture.read_text())
    assert data['kind'] == 'synthetic_stride_session_parity'
    generator = np.random.default_rng(2042)
    observations = [generator.normal(0,scale,428).astype(np.float32)
                    for scale in (0.,.1,1.,5.,20.) for _ in range(100)]
    observations += [np.asarray(row['observation'],dtype=np.float32) for row in data['expected']]
    errors=[]
    with torch.inference_mode():
        for observation in observations:
            expected = actor(dict(is_train=False,prev_actions=None,obs=torch.from_numpy(observation[None]),
                                  rnn_states=None))['mus'][0].clamp(-1,1).numpy()
            actual = numpy_actor(observation,arrays)
            np.testing.assert_allclose(actual,expected,atol=2e-5,rtol=1e-5)
            errors.append(float(np.max(np.abs(actual-expected))))
    sha=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    result=dict(passed=True,checkpoint_sha256=sha(checkpoint),epoch=state['epoch'],actor_inputs=428,
        config_sha256=sha(config),fixture_sha256=sha(fixture),checker_sha256=sha(Path(__file__)),
        portable_math_sha256=sha(Path(export_v2_policy.__file__)),
        cases=len(observations),synthetic_sensor_cases=len(data['expected']),
        maximum_absolute_error=max(errors),limitation='RL-Games versus NumPy only; no ONNX export or promotion')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('checkpoint','config','fixture','output'):
        parser.add_argument(name,type=Path)
    args=parser.parse_args()
    check(args.checkpoint,args.config,args.fixture,args.output)
