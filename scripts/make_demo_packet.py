"""Generate the synthetic demo packet under examples/packets."""

import base64

from helper.packets import new_record, write_packet

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

record = new_record(
    title="Synthetic demo packet (YouTube)",
    source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    page_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    site="youtube",
    timestamp_sec=142.5,
    capture_method="canvas",
    tags=["demo", "synthetic"],
    notes="Synthetic example only — no real client data.",
    packet_id="01JDEMO000000000000000000",
    created_at="2026-09-25T12:00:00+00:00",
)

written = write_packet(record, base64.b64decode(PNG_1PX), root="examples/packets")
print(f"wrote examples/packets/{written['site']}/{written['id']}/{written['image']}")
