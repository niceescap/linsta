"""Bounded dev-only engine entry. Import never starts playback."""
import argparse
import dev_runtime


def run(seconds=3600, *, mode="preview"):
    if mode == 'continuous':
        dev_runtime.check_paths()
        from continuous_radio_runner import run as continuous_run
        return continuous_run(dev_runtime.ROOT/'private-radio', normalize=True)
    if mode != 'preview':
        raise ValueError('Unknown radio mode')
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError('Radio duration must be 1..3600 seconds')
    dev_runtime.check_paths()
    # Fixed local catalogue/media/HLS root, no environment or CLI root override.
    from preview_radio_runner import run as bounded_run
    return bounded_run(dev_runtime.ROOT/'private-radio', seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--mode', choices=('preview', 'continuous'), default='preview')
    args = parser.parse_args()
    run(args.seconds, mode=args.mode)


if __name__ == '__main__':
    main()
