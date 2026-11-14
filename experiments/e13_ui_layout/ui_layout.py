"""Launch E13's simulator; reusable rendering code is in layout_assets/layout_renderer."""

import argparse


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-assets", action="store_true", help="Regenerate HUD SVG/PNG artwork in asset/ui_layout and exit")
    parser.add_argument("--snapshot", action="store_true", help="Export layout examples without desktop interaction")
    parser.add_argument("--benchmark", action="store_true", help="Export examples and benchmark software overlay composition")
    options = parser.parse_args()
    if options.prepare_assets:
        from asset_tools import prepare_layout_assets
        prepare_layout_assets()
    else:
        from simulator import run_experiment
        run_experiment(snapshot=options.snapshot, benchmark=options.benchmark)
