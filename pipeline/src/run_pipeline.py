from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from src.harvester import PoliteHarvester, load_latest_raw_snapshots
from src.loader import load_manual_coffees, merge_scraped_and_manual, sync_to_supabase
from src.parser import parse_raw_snapshot
from src.schemas import EstateMasterRecord, ParsedOffering, RawProductSnapshot, RoasterConfig

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PIPELINE_ROOT / "config"
DATA_DIR = PIPELINE_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
MANUAL_CSV_PATH = DATA_DIR / "manual_coffees.csv"


def load_roasters(config_path: Path) -> list[RoasterConfig]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return [RoasterConfig.model_validate(item) for item in raw]


def load_estates_master(config_path: Path) -> dict[str, EstateMasterRecord]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        estate_id: EstateMasterRecord.model_validate(record)
        for estate_id, record in raw.items()
    }


def print_extraction_report(offerings: list[ParsedOffering], show_samples: bool = False) -> None:
    total = len(offerings)
    confidence_counts = Counter(o.extraction_confidence for o in offerings)
    category_counts = Counter(cat for o in offerings for cat in o.flavor_categories)

    with_estate = sum(1 for o in offerings if o.estate_id)
    with_region = sum(1 for o in offerings if o.region)
    with_altitude = sum(1 for o in offerings if o.altitude_min_masl is not None)
    with_varietals = sum(1 for o in offerings if o.varietals)
    with_process = sum(1 for o in offerings if o.process)
    with_roast = sum(1 for o in offerings if o.roast_level)
    with_notes = sum(1 for o in offerings if o.tasting_notes_raw)
    blends = sum(1 for o in offerings if o.is_blend)

    print("\n=== coffee-wiki Terroir Extraction Summary ===")
    print(f"Total merged offerings : {total} (Blends: {blends})")
    print(
        f"Confidence breakdown   : high={confidence_counts.get('high', 0)}, "
        f"medium={confidence_counts.get('medium', 0)}, "
        f"low={confidence_counts.get('low', 0)}"
    )
    if total > 0:
        print(f"Coverage - Estate ID   : {with_estate}/{total} ({with_estate / total:.0%})")
        print(f"Coverage - Region      : {with_region}/{total} ({with_region / total:.0%})")
        print(f"Coverage - Altitude    : {with_altitude}/{total} ({with_altitude / total:.0%})")
        print(f"Coverage - Varietals   : {with_varietals}/{total} ({with_varietals / total:.0%})")
        print(f"Coverage - Process     : {with_process}/{total} ({with_process / total:.0%})")
        print(f"Coverage - Roast Level : {with_roast}/{total} ({with_roast / total:.0%})")
        print(f"Coverage - Notes       : {with_notes}/{total} ({with_notes / total:.0%})")

    if category_counts:
        print("Flavor Compass counts  :")
        for cat, count in category_counts.most_common():
            print(f"  - {cat}: {count}")

    if show_samples and offerings:
        print("\n=== Sample Parsed Offerings (first 3) ===")
        sample_payload = [o.model_dump(mode="json") for o in offerings[:3]]
        print(json.dumps(sample_payload, indent=2, ensure_ascii=False))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the coffee-wiki static terroir & sensory pipeline."
    )
    parser.add_argument(
        "--roaster",
        type=str,
        default=None,
        help="Optional single roaster ID filter (e.g. blue-tokai).",
    )
    parser.add_argument(
        "--skip-harvest",
        action="store_true",
        help="Skip HTTP harvesting and re-parse the latest snapshot in pipeline/data/raw/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print extraction statistics and sample JSON without writing to Supabase.",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    load_dotenv(PIPELINE_ROOT / ".env")
    load_dotenv(PIPELINE_ROOT.parent / ".env")

    args = build_arg_parser().parse_args()

    all_roasters = load_roasters(CONFIG_DIR / "roasters.json")
    estates_master = load_estates_master(CONFIG_DIR / "estates_master.json")

    if args.roaster:
        target_roasters = [r for r in all_roasters if r.id == args.roaster]
        if not target_roasters:
            valid_ids = ", ".join(r.id for r in all_roasters)
            raise SystemExit(f"Unknown roaster '{args.roaster}'. Valid IDs: {valid_ids}")
    else:
        target_roasters = all_roasters

    raw_snapshots: list[RawProductSnapshot] = []

    if args.skip_harvest:
        snapshot_date, raw_snapshots = load_latest_raw_snapshots(
            RAW_DATA_DIR,
            roaster_filter=args.roaster,
        )
        logging.info(
            "Loaded %d raw snapshots from latest archive (%s).",
            len(raw_snapshots),
            snapshot_date or "none found",
        )
    else:
        with PoliteHarvester(RAW_DATA_DIR) as harvester:
            for roaster in target_roasters:
                snaps = harvester.harvest_roaster(roaster)
                raw_snapshots.extend(snaps)

    parsed_scraped = [
        parse_raw_snapshot(snapshot, estates_master)
        for snapshot in raw_snapshots
    ]

    manual_offerings = load_manual_coffees(MANUAL_CSV_PATH, estates_master)
    if args.roaster:
        manual_offerings = [m for m in manual_offerings if m.roaster_id == args.roaster]

    merged_offerings = merge_scraped_and_manual(
        scraped=parsed_scraped,
        manual=manual_offerings,
        estates_master=estates_master,
    )

    print_extraction_report(merged_offerings, show_samples=args.dry_run)

    if args.dry_run:
        logging.info("Dry-run enabled; skipped Supabase writes.")
        return

    sync_counts = sync_to_supabase(
        roasters=all_roasters,
        estates_master=estates_master,
        offerings=merged_offerings,
    )
    print(f"\nSupabase sync complete: {sync_counts}")


if __name__ == "__main__":
    main()