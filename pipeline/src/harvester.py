from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from src.schemas import RawProductSnapshot, RoasterConfig

logger = logging.getLogger(__name__)

USER_AGENT = "IndianCoffeeWikiBot/1.0 (+mailto:contact@indian-coffee-wiki.in)"
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
POLITE_DELAY_SECONDS = 1.0
SHOPIFY_PAGE_LIMIT = 250
WOOCOMMERCE_PAGE_LIMIT = 100


def compute_content_hash(title: str, body_html: str, tags: list[str]) -> str:
    norm_title = re.sub(r"\s+", " ", (title or "").strip()).lower()
    norm_body = re.sub(r"\s+", " ", (body_html or "").strip())
    norm_tags = sorted({re.sub(r"\s+", " ", t.strip()).lower() for t in tags if t and t.strip()})
    payload = json.dumps(
        {"title": norm_title, "body_html": norm_body, "tags": norm_tags},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def should_exclude_product(
    title: str,
    product_type: str,
    tags: list[str],
    exclude_keywords: list[str],
) -> bool:
    if not exclude_keywords:
        return False
    haystack = " ".join([title or "", product_type or "", " ".join(tags or [])]).lower()
    for kw in exclude_keywords:
        needle = kw.strip().lower()
        if not needle:
            continue
        pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
        if re.search(pattern, haystack):
            return True
    return False


class PoliteHarvester:
    def __init__(self, raw_data_dir: Path) -> None:
        self.raw_data_dir = raw_data_dir
        self._last_request_ts = 0.0
        self.client = httpx.Client(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PoliteHarvester:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _sleep_politely(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if self._last_request_ts > 0 and elapsed < POLITE_DELAY_SECONDS:
            time.sleep(POLITE_DELAY_SECONDS - elapsed)

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> tuple[int, Any | None]:
        for attempt in range(1, MAX_RETRIES + 1):
            self._sleep_politely()
            try:
                self._last_request_ts = time.monotonic()
                response = self.client.get(url, params=params)
                if response.status_code == 404:
                    return 404, None
                if response.status_code in {429, 500, 502, 503, 504}:
                    backoff = float(2 ** (attempt - 1))
                    logger.warning(
                        "Transient HTTP %s for %s (attempt %s/%s); retrying in %.1fs",
                        response.status_code,
                        url,
                        attempt,
                        MAX_RETRIES,
                        backoff,
                    )
                    if attempt < MAX_RETRIES:
                        time.sleep(backoff)
                        continue
                    return response.status_code, None
                response.raise_for_status()
                return response.status_code, response.json()
            except (httpx.HTTPError, ValueError) as exc:
                backoff = float(2 ** (attempt - 1))
                logger.warning(
                    "Request error for %s (attempt %s/%s): %s",
                    url,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(backoff)
                else:
                    return 0, None
        return 0, None

    def _fetch_shopify_endpoint(self, base_url: str, path: str) -> tuple[bool, list[dict[str, Any]]]:
        products: list[dict[str, Any]] = []
        page = 1
        endpoint = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        while True:
            status, payload = self._get_json(
                endpoint,
                params={"limit": SHOPIFY_PAGE_LIMIT, "page": page},
            )
            if status == 404:
                return False, []
            if status != 200 or not isinstance(payload, dict):
                break
            batch = payload.get("products")
            if not isinstance(batch, list) or not batch:
                break
            products.extend(batch)
            if len(batch) < SHOPIFY_PAGE_LIMIT:
                break
            page += 1
        return bool(products), products

    def _harvest_shopify(self, roaster: RoasterConfig) -> list[RawProductSnapshot]:
        if not roaster.website_url:
            return []
        base_url = roaster.website_url.rstrip("/")
        collection_path = f"collections/{roaster.collection_handle}/products.json"
        ok, raw_items = self._fetch_shopify_endpoint(base_url, collection_path)
        if not ok:
            logger.info(
                "[%s] Falling back from /%s to /products.json",
                roaster.id,
                collection_path,
            )
            _, raw_items = self._fetch_shopify_endpoint(base_url, "products.json")

        scraped_at = datetime.now(timezone.utc)
        seen_ids: set[str] = set()
        snapshots: list[RawProductSnapshot] = []

        for item in raw_items:
            product_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            handle = str(item.get("handle") or "").strip()
            if not product_id or not title or not handle:
                continue
            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            raw_tags = item.get("tags") or []
            if isinstance(raw_tags, str):
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            elif isinstance(raw_tags, list):
                tags = [str(t).strip() for t in raw_tags if str(t).strip()]
            else:
                tags = []

            product_type = str(item.get("product_type") or "").strip()
            if should_exclude_product(title, product_type, tags, roaster.exclude_keywords):
                continue

            body_html = str(item.get("body_html") or "")
            source_url = f"{base_url}/products/{handle}"
            snapshots.append(
                RawProductSnapshot(
                    roaster_id=roaster.id,
                    source_product_id=product_id,
                    source_url=source_url,
                    title=title,
                    body_html=body_html,
                    tags=tags,
                    product_type=product_type,
                    content_hash=compute_content_hash(title, body_html, tags),
                    scraped_at=scraped_at,
                )
            )
        return snapshots

    def _harvest_woocommerce(self, roaster: RoasterConfig) -> list[RawProductSnapshot]:
        if not roaster.website_url:
            return []
        base_url = roaster.website_url.rstrip("/")
        endpoint = f"{base_url}/wp-json/wc/store/v1/products"
        raw_items: list[dict[str, Any]] = []
        page = 1

        while True:
            status, payload = self._get_json(
                endpoint,
                params={"per_page": WOOCOMMERCE_PAGE_LIMIT, "page": page},
            )
            if status != 200 or not isinstance(payload, list) or not payload:
                break
            raw_items.extend(payload)
            if len(payload) < WOOCOMMERCE_PAGE_LIMIT:
                break
            page += 1

        scraped_at = datetime.now(timezone.utc)
        seen_ids: set[str] = set()
        snapshots: list[RawProductSnapshot] = []

        for item in raw_items:
            product_id = str(item.get("id") or "").strip()
            title = str(item.get("name") or "").strip()
            source_url = str(item.get("permalink") or "").strip()
            if not product_id or not title or not source_url:
                continue
            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            categories = [
                str(c.get("name", "")).strip()
                for c in (item.get("categories") or [])
                if isinstance(c, dict) and c.get("name")
            ]
            wc_tags = [
                str(t.get("name", "")).strip()
                for t in (item.get("tags") or [])
                if isinstance(t, dict) and t.get("name")
            ]
            tags = categories + wc_tags
            product_type = categories[0] if categories else str(item.get("type") or "")

            if should_exclude_product(title, product_type, tags, roaster.exclude_keywords):
                continue

            short_desc = str(item.get("short_description") or "")
            full_desc = str(item.get("description") or "")
            body_html = f"{short_desc}\n{full_desc}".strip()

            snapshots.append(
                RawProductSnapshot(
                    roaster_id=roaster.id,
                    source_product_id=product_id,
                    source_url=source_url,
                    title=title,
                    body_html=body_html,
                    tags=tags,
                    product_type=product_type,
                    content_hash=compute_content_hash(title, body_html, tags),
                    scraped_at=scraped_at,
                )
            )
        return snapshots

    def harvest_roaster(self, roaster: RoasterConfig, run_date: str | None = None) -> list[RawProductSnapshot]:
        if roaster.platform == "shopify":
            snapshots = self._harvest_shopify(roaster)
        elif roaster.platform == "woocommerce":
            snapshots = self._harvest_woocommerce(roaster)
        else:
            logger.info("[%s] Skipping manual platform roaster during harvest.", roaster.id)
            return []

        date_folder = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_dir = self.raw_data_dir / date_folder
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{roaster.id}.json"

        serialized = [snap.model_dump(mode="json") for snap in snapshots]
        out_path.write_text(json.dumps(serialized, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("[%s] Wrote %d raw product snapshots to %s", roaster.id, len(snapshots), out_path)
        return snapshots


def load_latest_raw_snapshots(
    raw_data_dir: Path,
    roaster_filter: str | None = None,
) -> tuple[str | None, list[RawProductSnapshot]]:
    if not raw_data_dir.exists():
        return None, []
    date_dirs = sorted(
        [p for p in raw_data_dir.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)],
        key=lambda p: p.name,
        reverse=True,
    )
    if not date_dirs:
        return None, []

    latest_dir = date_dirs[0]
    snapshots: list[RawProductSnapshot] = []
    json_files = sorted(latest_dir.glob("*.json"))
    for path in json_files:
        if roaster_filter and path.stem != roaster_filter:
            continue
        raw_list = json.loads(path.read_text(encoding="utf-8"))
        for entry in raw_list:
            snapshots.append(RawProductSnapshot.model_validate(entry))
    return latest_dir.name, snapshots