"""Fixed read-only workflow entrypoint. No caller-selectable action mode."""
from ops.light_native_lane_owner_runner import main

if __name__=='__main__':
    try:status=main(read_only=True)
    except BaseException:
        print('{"audit":"LIGHT_LANE_OBSERVATION_RUNNER_REFUSED"}')
        raise SystemExit(2) from None
    else:raise SystemExit(status or 0)
