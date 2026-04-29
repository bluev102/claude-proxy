"""OpenCode provider — BeautifulSoup HTML catalog parser."""
import re
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from core.utils import normalize_label, normalize_name_key


def normalize_model_id_from_str(model_id: str) -> str:
    """Strip 'opencode/' prefix and whitespace from a raw model ID string."""
    value = (model_id or "").strip()
    if value.startswith("opencode/"):
        value = value.split("/", 1)[1].strip()
    return value


def html_table_to_dicts(table) -> List[Dict[str, str]]:
    rows = table.find_all("tr")
    if not rows:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    headers = [normalize_label(cell.get_text(" ", strip=True)) for cell in header_cells]

    out: List[Dict[str, str]] = []
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        values = [cell.get_text(" ", strip=True) for cell in cells]
        if len(values) < len(headers):
            values += [""] * (len(headers) - len(values))
        elif len(values) > len(headers):
            values = values[: len(headers)]

        out.append({headers[i]: values[i] for i in range(len(headers))})

    return out


def parse_docs_catalog_from_html(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    routes: Dict[str, Dict[str, Any]] = {}
    pricing_free_names: Dict[str, Dict[str, Any]] = {}
    bullet_free_names: List[str] = []

    for table in tables:
        entries = html_table_to_dicts(table)
        if not entries:
            continue

        headers = set(entries[0].keys())

        if {"model", "model id", "endpoint", "ai sdk package"}.issubset(headers):
            for entry in entries:
                model_name = entry.get("model", "")
                model_id = normalize_model_id_from_str(entry.get("model id", ""))
                endpoint = entry.get("endpoint", "")
                sdk_package = entry.get("ai sdk package", "")

                if not model_id or not endpoint:
                    continue

                family = None
                path = None

                if endpoint.endswith("/v1/messages"):
                    family = "anthropic_messages"
                    path = "/messages"
                elif endpoint.endswith("/v1/chat/completions"):
                    family = "openai_chat"
                    path = "/chat/completions"
                elif endpoint.endswith("/v1/responses"):
                    family = "openai_responses"
                    path = "/responses"
                elif "/v1/models/" in endpoint:
                    family = "google_models"
                    path = endpoint.split("/zen/v1", 1)[-1]

                if family:
                    routes[model_id] = {
                        "model_name": model_name,
                        "name_key": normalize_name_key(model_name),
                        "model_id": model_id,
                        "endpoint": endpoint,
                        "sdk_package": sdk_package,
                        "family": family,
                        "path": path,
                    }

        if {"model", "input", "output", "cached read", "cached write"}.issubset(headers):
            for entry in entries:
                model_name = entry.get("model", "")
                input_price = normalize_label(entry.get("input", ""))
                output_price = normalize_label(entry.get("output", ""))
                cached_read = normalize_label(entry.get("cached read", ""))

                if input_price == "free" and output_price == "free" and cached_read == "free":
                    pricing_free_names[model_name] = {
                        "model_name": model_name,
                        "name_key": normalize_name_key(model_name),
                        "input_price": entry.get("input", ""),
                        "output_price": entry.get("output", ""),
                        "cached_read_price": entry.get("cached read", ""),
                        "cached_write_price": entry.get("cached write", ""),
                    }

    free_anchor = soup.find(string=re.compile(r"The free models:", re.I))
    if free_anchor:
        parent = free_anchor.parent
        next_ul = parent.find_next("ul") if parent else None
        if next_ul:
            for li in next_ul.find_all("li"):
                text = li.get_text(" ", strip=True)
                name = re.split(r"\s+is\s+", text, maxsplit=1)[0].strip()
                if name:
                    bullet_free_names.append(name)

    free_name_keys = {meta["name_key"] for meta in pricing_free_names.values()}
    free_name_keys.update(normalize_name_key(name) for name in bullet_free_names if name)

    free_route_ids = {
        model_id
        for model_id, meta in routes.items()
        if meta["name_key"] in free_name_keys
    }

    return {
        "routes": routes,
        "pricing_free_names": pricing_free_names,
        "bullet_free_names": bullet_free_names,
        "free_name_keys": free_name_keys,
        "free_route_ids": free_route_ids,
    }
