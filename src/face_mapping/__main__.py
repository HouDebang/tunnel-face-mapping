"""Single-command local executor. Exit 0=completed, 2=partial/failed/invalid."""
import argparse
import logging
from pathlib import Path
from .config import PipelineConfig, load_config
from .pipeline import run_pipeline


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Automatic tunnel-face image candidate mapping MVP")
    parser.add_argument("--input",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path,help="Empty or nonexistent directory; use a separate folder per run")
    parser.add_argument("--config",type=Path)
    parser.add_argument("--meters-per-pixel",type=float,help="m per ORIGINAL EXIF-oriented pixel")
    parser.add_argument("--assume-planar",action="store_true",help="Acknowledge uniform scale and approximately planar, front-facing projection")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        if args.meters_per_pixel is not None or args.assume_planar:
            data = config.model_dump()
            if args.meters_per_pixel is not None:
                data["calibration"]["meters_per_pixel"] = args.meters_per_pixel
            if args.assume_planar:
                data["calibration"]["planar_approximation_acknowledged"] = True
            config = PipelineConfig.model_validate(data)
        result = run_pipeline(args.input,args.output,config)
        print(f"{result.status}: {args.output.resolve() / 'results.json'}")
        return 0 if result.status.startswith("completed") else 2
    except Exception as exc:
        logging.getLogger("face_mapping").error("%s: %s",type(exc).__name__,exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
