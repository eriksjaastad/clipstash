"""Generate the synthetic demo packet under examples/packets."""

import base64
from pathlib import Path

import yaml

from helper.packets import new_record, packet_dir

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

record = new_record(
    title="Synthetic demo packet (YouTube)",
    source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    page_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    site="youtube",
    timestamp_sec=142.5,
    tags=["demo", "synthetic"],
    notes="Synthetic example only — no real client data.",
    packet_id="01JDEMO000000000000000000",
    created_at="2026-09-25T12:00:00+00:00",
)

directory = packet_dir("examples/packets", record["id"])
directory.mkdir(parents=True, exist_ok=True)
(directory / "still.png").write_bytes(base64.b64decode(PNG_1PX))
(directory / "record.yaml").write_text(
    yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8"
)
print(f"wrote {directory}")
