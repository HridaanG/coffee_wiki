from __future__ import annotations

import csv
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from src.parser import (
    CANONICAL_PROCESS_ALIASES,
    CANONICAL_REGION_ALIASES,
    CANONICAL_VARIETAL_ALIASES,
    classify_flavor_categories,
    compute_confidence,
)
from src.schemas import EstateMasterRecord, ParsedOffering, RoasterConfig

logger = logging.getLogger(__name__)


def build_alias_rows(estates_master: dict[str, EstateMasterRecord]) -> list[dict[str, str]]:
    alias_map: dict[str, dict[str, str]] = {}

    def register(alias: str, entity_type: str, canonical_id: str) -> None:
        key = alias.strip().lower()
        if key:
            alias_map[key] = {
                "alias": key,
                "entity_type": entity_type,
                "canonical_id": canonical_id,
            }

    for estate_id, estate in estates_master.items():
        register(estate_id, "estate", estate_id)
        register(estate.name, "estate", estate_id)
        for alias in estate.aliases:
            register(alias, "estate", estate_id)

    for alias, canonical_id in CANONICAL_REGION_ALIASES.items():
        register(alias, "region", canonical_id)
    for alias, canonical_id in CANONICAL_VARIETAL_ALIASES.items():
        register(alias, "varietal", canonical_id)
    for alias, canonical_id in CANONICAL_PROCESS_ALIASES.items():
        register(alias, "process", canonical_id)

    return list(alias_map.values())


def build_estate_lookup(estates_master: dict[str, EstateMasterRecord]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for estate_id, estate in estates_master.items():
        lookup[estate_id.lower()] = estate_id
        lookup[estate.name.lower()] = estate_id
        for alias in estate.aliases:
            lookup[alias.strip().lower()] = estate_id
    return lookup


def load_manual_coffees(
    csv_path: Path,
    estates_master: dict[str, EstateMasterRecord],
) -> list[ParsedOffering]:
    if not csv_path.exists():
        return []

    estate_lookup = build_estate_lookup(estates_master)
    manual_offerings: list[ParsedOffering] = []

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=1):
            roaster_id = (row.get("roaster_id") or "").strip()
            title = (row.get("title") or "").strip()
            source_url = (row.get("source_url") or "").strip()
            if not roaster_id or not title or not source_url:
                continue

            estate_name = (row.get("estate_name") or "").strip() or None
            estate_id = estate_lookup.get(estate_name.lower()) if estate_name else None
            region = (row.get("region") or "").strip() or None

            alt_min_str = (row.get("altitude_min_masl") or "").strip()
            alt_max_str = (row.get("altitude_max_masl") or "").strip()
            alt_min = int(alt_min_str) if alt_min_str.isdigit() else None
            alt_max = int(alt_max_str) if alt_max_str.isdigit() else None

            varietals = [
                v.strip()
                for v in (row.get("varietals") or "").split(";")
                if v.strip()
            ]
            process = (row.get("process") or "").strip() or None
            roast_level = (row.get("roast_level") or "").strip() or None
            tasting_notes_raw = [
                n.strip()
                for n in (row.get("tasting_notes_raw") or "").split(";")
                if n.strip()
            ]
            flavor_categories = classify_flavor_categories(tasting_notes_raw)
            is_blend = "blend" in title.lower()

            content_hash = hashlib.sha256(
                f"manual:{roaster_id}:{source_url}:{title}:{process}:{roast_level}:{';'.join(tasting_notes_raw)}".encode("utf-8")
            ).hexdigest()

            confidence = compute_confidence(
                estate_or_region=bool(estate_id or region),
                process=process,
                varietals=varietals,
                tasting_notes=tasting_notes_raw,
            )

            manual_offerings.append(
                ParsedOffering(
                    roaster_id=roaster_id,
                    estate_id=estate_id,
                    estate_name=estate_name,
                    source_product_id=f"manual-{roaster_id}-{idx}",
                    title=title,
                    source_url=source_url,
                    is_blend=is_blend,
                    region=region,
                    altitude_min_masl=alt_min,
                    altitude_max_masl=alt_max,
                    varietals=varietals,
                    process=process,
                    roast_level=roast_level,
                    tasting_notes_raw=tasting_notes_raw,
                    flavor_categories=flavor_categories,
                    in_stock=True,
                    min_price_inr=None,
                    price_250g_inr=None,
                    extraction_confidence=confidence,
                    content_hash=content_hash,
                )
            )
    return manual_offerings


def apply_canonical_estate_inheritance(
    offerings: list[ParsedOffering],
    estates_master: dict[str, EstateMasterRecord],
) -> list[ParsedOffering]:
    estate_lookup = build_estate_lookup(estates_master)
    enriched: list[ParsedOffering] = []

    for offering in offerings:
        resolved_estate_id = offering.estate_id
        if not resolved_estate_id and offering.estate_name:
            resolved_estate_id = estate_lookup.get(offering.estate_name.strip().lower())

        region = offering.region
        alt_min = offering.altitude_min_masl
        alt_max = offering.altitude_max_masl

        if resolved_estate_id and resolved_estate_id in estates_master:
            canonical = estates_master[resolved_estate_id]
            if not region:
                region = canonical.region
            if alt_min is None:
                alt_min = canonical.altitude_min_masl
            if alt_max is None:
                alt_max = canonical.altitude_max_masl

        confidence = compute_confidence(
            estate_or_region=bool(resolved_estate_id or region),
            process=offering.process,
            varietals=offering.varietals,
            tasting_notes=offering.tasting_notes_raw,
        )

        enriched.append(
            offering.model_copy(
                update={
                    "estate_id": resolved_estate_id,
                    "region": region,
                    "altitude_min_masl": alt_min,
                    "altitude_max_masl": alt_max,
                    "in_stock": True,
                    "min_price_inr": None,
                    "price_250g_inr": None,
                    "extraction_confidence": confidence,
                }
            )
        )
    return enriched


def merge_scraped_and_manual(
    scraped: list[ParsedOffering],
    manual: list[ParsedOffering],
    estates_master: dict[str, EstateMasterRecord],
) -> list[ParsedOffering]:
    by_url: dict[str, ParsedOffering] = {o.source_url: o for o in scraped}
    # Manual rows take strict precedence on matching source_url
    for m in manual:
        by_url[m.source_url] = m
    return apply_canonical_estate_inheritance(list(by_url.values()), estates_master)


def _merge_preserving_non_null(
    incoming: ParsedOffering,
    existing: dict[str, Any] | None,
    now_iso: str,
) -> dict[str, Any]:
    def pick_scalar(new_val: Any, old_key: str) -> Any:
        if new_val is not None and new_val != "":
            return new_val
        if existing is not None:
            return existing.get(old_key)
        return None

    def pick_list(new_list: list[str], old_key: str) -> list[str]:
        if new_list:
            return new_list
        if existing is not None and existing.get(old_key):
            return list(existing[old_key])
        return []

    merged_estate_id = pick_scalar(incoming.estate_id, "estate_id")
    merged_region = pick_scalar(incoming.region, "region")
    merged_alt_min = pick_scalar(incoming.altitude_min_masl, "altitude_min_masl")
    merged_alt_max = pick_scalar(incoming.altitude_max_masl, "altitude_max_masl")
    merged_varietals = pick_list(incoming.varietals, "varietals")
    merged_process = pick_scalar(incoming.process, "process")
    merged_roast = pick_scalar(incoming.roast_level, "roast_level")
    merged_notes = pick_list(incoming.tasting_notes_raw, "tasting_notes_raw")
    merged_categories = pick_list(list(incoming.flavor_categories), "flavor_categories")

    merged_confidence = compute_confidence(
        estate_or_region=bool(merged_estate_id or merged_region),
        process=merged_process,
        varietals=merged_varietals,
        tasting_notes=merged_notes,
    )

    row: dict[str, Any] = {
        "roaster_id": incoming.roaster_id,
        "estate_id": merged_estate_id,
        "source_product_id": incoming.source_product_id,
        "title": incoming.title,
        "source_url": incoming.source_url,
        "is_blend": incoming.is_blend,
        "region": merged_region,
        "altitude_min_masl": merged_alt_min,
        "altitude_max_masl": merged_alt_max,
        "varietals": merged_varietals,
        "process": merged_process,
        "roast_level": merged_roast,
        "tasting_notes_raw": merged_notes,
        "flavor_categories": merged_categories,
        "in_stock": True,
        "min_price_inr": None,
        "price_250g_inr": None,
        "extraction_confidence": merged_confidence,
        "content_hash": incoming.content_hash,
        "last_seen_at": now_iso,
    }
    if existing and existing.get("first_seen_at"):
        row["first_seen_at"] = existing["first_seen_at"]
    else:
        row["first_seen_at"] = now_iso
    return row


def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in environment for database sync."
        )
    return create_client(url, key)


def _fetch_existing_offerings(client: Client, source_urls: list[str]) -> dict[str, dict[str, Any]]:
    existing_map: dict[str, dict[str, Any]] = {}
    chunk_size = 100
    for i in range(0, len(source_urls), chunk_size):
        chunk = source_urls[i : i + chunk_size]
        response = (
            client.table("offerings")
            .select(
                "source_url,estate_id,region,altitude_min_masl,altitude_max_masl,"
                "varietals,process,roast_level,tasting_notes_raw,flavor_categories,first_seen_at"
            )
            .in_("source_url", chunk)
            .execute()
        )
        for row in response.data or []:
            existing_map[row["source_url"]] = row
    return existing_map


def sync_to_supabase(
    roasters: list[RoasterConfig],
    estates_master: dict[str, EstateMasterRecord],
    offerings: list[ParsedOffering],
) -> dict[str, int]:
    client = get_supabase_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Upsert public.roasters
    roaster_rows = [r.to_db_row() for r in roasters]
    if roaster_rows:
        client.table("roasters").upsert(roaster_rows, on_conflict="id").execute()

    # 2. Upsert public.estates
    estate_rows = [rec.to_db_row(eid) for eid, rec in estates_master.items()]
    if estate_rows:
        client.table("estates").upsert(estate_rows, on_conflict="id").execute()

    # 3. Upsert public.entity_aliases
    alias_rows = build_alias_rows(estates_master)
    if alias_rows:
        client.table("entity_aliases").upsert(alias_rows, on_conflict="alias").execute()

    # 4. Merge & upsert public.offerings without touching offering_snapshots
    existing_map = _fetch_existing_offerings(client, [o.source_url for o in offerings])
    offering_rows = [
        _merge_preserving_non_null(o, existing_map.get(o.source_url), now_iso)
        for o in offerings
    ]

    chunk_size = 100
    for i in range(0, len(offering_rows), chunk_size):
        chunk = offering_rows[i : i + chunk_size]
        client.table("offerings").upsert(chunk, on_conflict="source_url").execute()

    logger.info(
        "Synced %d roasters, %d estates, %d aliases, %d offerings to Supabase.",
        len(roaster_rows),
        len(estate_rows),
        len(alias_rows),
        len(offering_rows),
    )
    return {
        "roasters": len(roaster_rows),
        "estates": len(estate_rows),
        "entity_aliases": len(alias_rows),
        "offerings": len(offering_rows),
    }