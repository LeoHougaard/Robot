"""Fixed intermediate CAD gait checks; passing does not authorize deployment."""
import argparse
import json
from pathlib import Path

def screen(result):
    failures = []
    for i, r in enumerate(result['results']):
        vx, vy, yaw = r['command']

        def check(ok, name):
            if not ok:
                failures.append(dict(environment=i, check=name))
        check(r['resets'] == 0, 'no falls')
        check(r['mean_tilt_rad'] <= 0.08, 'mean tilt <= .08 rad')
        check(abs(r['mean_height_m'] - 0.18) <= 0.015, 'height within 15 mm')
        for axis, command, actual, absolute in (('forward', vx, r['mean_forward_m_s'], r['mean_abs_forward_m_s']), ('lateral', vy, r['mean_signed_lateral_m_s'], r['mean_abs_lateral_m_s'])):
            check(abs(actual - command) <= max(0.02, 0.35 * abs(command)), axis + ' tracking')
            if command:
                check(actual * (1 if command > 0 else -1) >= 0.65 * abs(command), axis + ' signed progress')
            else:
                check(absolute <= 0.02, axis + ' unrequested motion')
        check(abs(r['mean_signed_yaw_rate_rad_s'] - yaw) <= max(0.05, 0.35 * abs(yaw)), 'yaw tracking')
        if yaw:
            check(r['mean_signed_yaw_rate_rad_s'] * (1 if yaw > 0 else -1) >= 0.65 * abs(yaw), 'yaw signed progress')
        else:
            check(abs(r['mean_signed_yaw_rate_rad_s']) <= 0.02, 'unrequested signed yaw')
        if any((vx, vy, yaw)):
            for foot in range(4):
                check(r['landings_frflbrbl'][foot] >= 6 * result['measured_seconds'] / 14, f'foot {foot} landing rate')
                check(r['air_fraction_frflbrbl'][foot] >= 0.03, f'foot {foot} lifts')
                check(r['max_continuous_air_s_frflbrbl'][foot] <= 1, f'foot {foot} returns within 1 s')
        else:
            for foot in range(4):
                check(r['air_fraction_frflbrbl'][foot] <= 0.05, f'foot {foot} stopped support')
    return failures

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args()
    failures = {str(path): screen(json.loads(path.read_text())) for path in args.results}
    print(json.dumps(failures, indent=2))
    raise SystemExit(int(any(failures.values())))
