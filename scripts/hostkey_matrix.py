#!/usr/bin/env python3
"""Discover Hostkey stock servers and rank benchmark candidates.

This script:
1) fetches all stock servers from Hostkey API;
2) keeps only available servers that can install Linux on hourly billing;
3) categorizes hardware into "common" or "odd" based on README variance goals;
4) writes ordered JSON/CSV outputs for benchmark orchestration.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_API_BASE = "https://invapi.hostkey.com"
FALLBACK_API_BASES = ("https://invapi.hostkey.com", "https://api.hostkey.com")
DEFAULT_LOCATIONS = ("all", "NL", "DE", "FI", "IS", "US", "SG")
DEFAULT_GROUPS = ("all", "dedicated", "gpu", "1xCPU", "2xCPU", "AMD Ryzen", "Intel", "legacy")

RAM_COMMON = {32, 64, 128, 256}

CPU_COMMON_PATTERNS = [
    r"\bi[357]-\d{4,5}\b",  # consumer Intel generations
    r"\bxeon\b",
    r"\be5\b",
    r"\bdual xeon\b",
    r"\bryzen\b",
    r"\bthreadripper\b",
    r"\bepyc\b",
    r"\bzen\b",
]

GPU_COMMON_PATTERNS = [
    r"\brtx\s?30(80|90)\b",
    r"\brtx\s?40(80|90)\b",
    r"\brtx\s?5090\b",
    r"\ba\d{3,4}\b",
    r"\bh\d{2,3}\b",
    r"\bb\d{2,3}\b",
    r"\b(7\d{3}|48\d{2})(\s?xtx?|\s?xt)?\b",  # Radeon 7xxx / 48xx
]

LINUX_HINTS = (
    "ubuntu",
    "debian",
    "centos",
    "alma",
    "rocky",
    "fedora",
    "linux",
    "gentoo",
    "arch",
)


@dataclass
class ServerDecision:
    server_id: int
    keep: bool
    reason: str
    hourly_price_eur: float | None = None
    linux_os_count: int = 0


def post_form_json(url: str, payload: dict[str, Any], timeout: int = 8) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    # Some edge/CDN policies block default Python urllib user agents.
    req.add_header("User-Agent", "curl/8.5.0")
    req.add_header("Accept", "application/json")
    req.add_header("Origin", "https://invapi.hostkey.com")
    req.add_header("Referer", "https://invapi.hostkey.com/")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def safe_filename(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", text).strip("_") or "value"


def classify_patterns(text: str, patterns: list[str]) -> bool:
    norm = text.lower()
    return any(re.search(pat, norm) for pat in patterns)


def extract_price(server: dict[str, Any]) -> float | None:
    # New invapi schema: billing_plan.EUR.hourly
    billing_plan = server.get("billing_plan") or {}
    eur_plan = billing_plan.get("EUR") if isinstance(billing_plan, dict) else None
    if isinstance(eur_plan, dict):
        hourly = eur_plan.get("hourly")
        if hourly is not None:
            try:
                return float(hourly)
            except (TypeError, ValueError):
                pass

    price = server.get("price") or {}
    # API docs show EUR/USD maps. We prefer EUR for ordering.
    eur = price.get("EUR")
    if eur is None:
        return None
    try:
        return float(eur)
    except (TypeError, ValueError):
        return None


def looks_linux(os_name: str) -> bool:
    n = os_name.lower()
    return any(h in n for h in LINUX_HINTS) and "windows" not in n


def extract_cpu(server: dict[str, Any]) -> str:
    specs = server.get("specs") or {}
    hardware = server.get("hardware") or {}
    return str(specs.get("cpu") or hardware.get("cpu_name") or "")


def extract_ram(server: dict[str, Any]) -> int:
    specs = server.get("specs") or {}
    hardware = server.get("hardware") or {}
    raw = specs.get("ram", hardware.get("ram", 0))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def extract_disk(server: dict[str, Any]) -> int | None:
    specs = server.get("specs") or {}
    if specs.get("disk") is not None:
        try:
            return int(specs.get("disk"))
        except (TypeError, ValueError):
            return None

    hardware = server.get("hardware") or {}
    hdd_groups = hardware.get("hdd_groups") or []
    total = 0
    for item in hdd_groups:
        size = item.get("size")
        count = item.get("count", 1)
        try:
            total += int(size) * int(count)
        except (TypeError, ValueError):
            continue
    return total or None


def is_available(server: dict[str, Any]) -> bool:
    # In invapi stocks/list results are typically available entries and may not include status.
    status = str(server.get("status", "")).strip().lower()
    if not status:
        return True
    return status in {"available", "in_stock", "instock", "free"}


def server_has_gpu(server: dict[str, Any]) -> bool:
    hardware = server.get("hardware") or {}
    text = " ".join(
        [
            str(server.get("name", "")),
            str((server.get("specs") or {}).get("cpu", "")),
            str((server.get("specs") or {}).get("gpu", "")),
            str(hardware.get("cpu_name", "")),
            str(hardware.get("gpu_name", "")),
            str(hardware.get("config", "")),
        ]
    ).lower()
    if "gpu" in text or "rtx" in text or "radeon" in text:
        return True
    return bool(classify_patterns(text, GPU_COMMON_PATTERNS))


def classify_server(server: dict[str, Any]) -> str:
    cpu = extract_cpu(server)
    ram = extract_ram(server)

    cpu_common = classify_patterns(cpu, CPU_COMMON_PATTERNS)
    ram_common = ram in RAM_COMMON
    gpu_common = classify_patterns(" ".join([str(server.get("name", "")), cpu]), GPU_COMMON_PATTERNS)

    score = sum([cpu_common, ram_common, gpu_common])
    return "common" if score >= 2 else "odd"


def evaluate_server(
    server: dict[str, Any],
    api_base: str,
    token: str | None = None,
    require_linux_hourly: bool = False,
    request_timeout: int = 8,
) -> ServerDecision:
    server_id = int(server.get("id"))
    if not is_available(server):
        return ServerDecision(server_id, False, "not_available")

    linux_hourly: list[dict[str, Any]] = []
    if require_linux_hourly:
        data = {"action": "list", "id": server_id, "bill_period": "hourly"}
        if token:
            data["token"] = token

        try:
            os_resp = post_form_json(f"{api_base}/os.php", data, timeout=request_timeout)
        except Exception as exc:  # noqa: BLE001
            return ServerDecision(server_id, False, f"os_query_failed:{exc}")

        os_list = os_resp.get("os_list") or []
        linux_hourly = [
            item
            for item in os_list
            if looks_linux(str(item.get("name", "")))
            and str(item.get("billing_plan", "")).lower() == "hourly"
            and int(item.get("active", 0)) == 1
        ]
        if not linux_hourly:
            return ServerDecision(server_id, False, "no_linux_hourly")
    hourly_price = extract_price(server)
    return ServerDecision(
        server_id=server_id,
        keep=True,
        reason="ok" if (linux_hourly or not require_linux_hourly) else "ok_no_linux_hourly",
        hourly_price_eur=hourly_price,
        linux_os_count=len(linux_hourly),
    )


def to_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in candidates:
        rows.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "location": s.get("location"),
                "group": s.get("group") or s.get("server_group"),
                "price_eur": s.get("price_eur"),
                "class": s.get("class"),
                "cpu": extract_cpu(s),
                "ram_gb": extract_ram(s),
                "disk_gb": extract_disk(s),
                "gpu_detected": s.get("gpu_detected"),
                "linux_os_count_hourly": s.get("linux_os_count_hourly"),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Hostkey stock hardware matrix builder")
    parser.add_argument("--api-base", default="", help="Hostkey API base URL (optional)")
    parser.add_argument("--token", default="", help="Optional API token")
    parser.add_argument("--workers", type=int, default=10, help="Parallel OS checks")
    parser.add_argument(
        "--require-linux-hourly",
        action="store_true",
        help="Only keep servers with at least one Linux OS available on hourly billing",
    )
    parser.add_argument(
        "--probe-filters",
        action="store_true",
        help="Probe multiple location/group combinations for stocks/list",
    )
    parser.add_argument(
        "--locations",
        default=",".join(DEFAULT_LOCATIONS),
        help="Comma-separated location list for probing",
    )
    parser.add_argument(
        "--groups",
        default=",".join(DEFAULT_GROUPS),
        help="Comma-separated group list for probing",
    )
    parser.add_argument(
        "--debug-log-dir",
        default="outputs/debug",
        help="Directory to write raw API responses and probe summaries",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=8,
        help="Per-request timeout in seconds for API calls",
    )
    parser.add_argument(
        "--out-json",
        default="outputs/hostkey_candidates.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--out-csv",
        default="outputs/hostkey_candidates.csv",
        help="Output CSV file path",
    )
    args = parser.parse_args()

    api_candidates = [args.api_base] if args.api_base else list(FALLBACK_API_BASES)
    debug_dir = Path(args.debug_log_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)
    probe_log_path = debug_dir / "stocks_probe_log.json"

    stock: dict[str, Any] = {"result": "OK", "action": "list", "servers": []}
    merged_servers: dict[int, dict[str, Any]] = {}
    used_api_base = ""
    last_error = ""
    probe_records: list[dict[str, Any]] = []

    locations = [x.strip() for x in args.locations.split(",") if x.strip()]
    groups = [x.strip() for x in args.groups.split(",") if x.strip()]

    for base in api_candidates:
        if not base:
            continue

        attempts: list[tuple[str | None, str | None]] = [(None, None)]
        if args.probe_filters:
            for loc in locations:
                attempts.append((loc, None))
            for grp in groups:
                attempts.append((None, grp))
            for loc in locations:
                for grp in groups:
                    attempts.append((loc, grp))

        for loc, grp in attempts:
            payload = {"action": "list"}
            if args.token:
                payload["token"] = args.token
            if loc and loc.lower() != "all":
                payload["location"] = loc
            if grp and grp.lower() != "all":
                payload["group"] = grp

            rec: dict[str, Any] = {"api_base": base, "payload": payload}
            try:
                resp = post_form_json(f"{base}/stocks.php", payload, timeout=args.request_timeout)
                server_count = len(resp.get("servers") or [])
                rec["result"] = "ok"
                rec["server_count"] = server_count
                probe_records.append(rec)

                loc_name = safe_filename(payload.get("location", "all"))
                grp_name = safe_filename(payload.get("group", "all"))
                raw_path = debug_dir / f"stocks_{safe_filename(base)}_{loc_name}_{grp_name}.json"
                raw_path.write_text(json.dumps(resp, indent=2), encoding="utf-8")

                if not used_api_base:
                    used_api_base = base
                for srv in resp.get("servers") or []:
                    try:
                        merged_servers[int(srv["id"])] = srv
                    except (KeyError, TypeError, ValueError):
                        continue
            except Exception as exc:  # noqa: BLE001
                rec["result"] = "error"
                rec["error"] = str(exc)
                probe_records.append(rec)
                last_error = str(exc)

    probe_log_path.write_text(json.dumps(probe_records, indent=2), encoding="utf-8")

    if not used_api_base and probe_records:
        # If every successful call returned zero servers, retain first successful base.
        first_ok = next((r for r in probe_records if r.get("result") == "ok"), None)
        if first_ok:
            used_api_base = str(first_ok["api_base"])

    if not used_api_base and not probe_records:
        print(
            f"failed to query stocks/list on all api bases ({', '.join(api_candidates)}): {last_error}",
            file=sys.stderr,
        )
        return 1

    if merged_servers:
        stock = {"result": "OK", "action": "list", "servers": list(merged_servers.values())}

    servers = stock.get("servers") or []

    by_id = {int(s["id"]): s for s in servers if "id" in s}
    decisions: list[ServerDecision] = []

    if servers:
        with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as pool:
            futures = [
                pool.submit(
                    evaluate_server,
                    s,
                    used_api_base,
                    args.token or None,
                    args.require_linux_hourly,
                    args.request_timeout,
                )
                for s in servers
            ]
            for fut in as_completed(futures):
                decisions.append(fut.result())

        keep_ids = {d.server_id for d in decisions if d.keep}
        candidates = [by_id[sid] for sid in keep_ids]
        for s in candidates:
            sid = int(s["id"])
            decision = next(d for d in decisions if d.server_id == sid)
            s["linux_os_count_hourly"] = decision.linux_os_count
            s["price_eur"] = extract_price(s)
            s["gpu_detected"] = server_has_gpu(s)
            s["class"] = classify_server(s)
    else:
        candidates = []

    candidates.sort(key=lambda item: (item.get("price_eur") is None, item.get("price_eur", 0.0)))

    grouped = {
        "common": [s for s in candidates if s["class"] == "common"],
        "odd": [s for s in candidates if s["class"] == "odd"],
    }

    report = {
        "api_base": used_api_base,
        "probe_filters_enabled": bool(args.probe_filters),
        "debug_probe_log": str(probe_log_path),
        "total_servers": len(servers),
        "available_servers": len(candidates),
        "eligible_servers": len(candidates),
        "excluded": [
            {
                "server_id": d.server_id,
                "reason": d.reason,
            }
            for d in sorted(decisions, key=lambda x: x.server_id)
            if not d.keep
        ],
        "groups": grouped,
    }

    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    rows = to_rows(candidates)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "name",
                "location",
                "group",
                "price_eur",
                "class",
                "cpu",
                "ram_gb",
                "disk_gb",
                "gpu_detected",
                "linux_os_count_hourly",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {out_json} and {out_csv}")
    print(f"eligible: {len(candidates)} / total: {len(servers)}")
    if not servers:
        print("warning: stocks/list returned zero servers; wrote empty candidate outputs")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
