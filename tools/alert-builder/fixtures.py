"""Deterministic offline VSS stand-in for MOCK mode."""

from __future__ import annotations

import copy
import re

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "are",
    "was",
    "were",
    "but",
    "not",
    "you",
    "your",
    "has",
    "have",
    "had",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {word for word in words if len(word) >= 3 and word not in _STOPWORDS}


def _overlap(query: str, caption: str) -> float:
    wanted = _tokens(query)
    if not wanted:
        return 0.0
    return len(wanted & _tokens(caption)) / len(wanted)


def _segment(camera_id: str, location: str, capture_type: str, original_video: str, upload_timestamp: str, number: int, caption: str, counts: dict[str, int]) -> dict:
    return {
        "source": f"s3://fixtures/segments/{camera_id}/seg_{number:04d}.mp4",
        "original_video": original_video,
        "segment_number": number,
        "start_sec": float(number * 5),
        "end_sec": float((number + 1) * 5),
        "caption": caption,
        "object_counts": dict(counts),
        "object_classes": list(counts.keys()),
        "camera_id": camera_id,
        "location": location,
        "capture_type": capture_type,
        "upload_timestamp": upload_timestamp,
        "similarity": None,
    }


def _video(camera_id: str, location: str, capture_type: str, upload_timestamp: str, segments: list[tuple[str, dict[str, int]]]) -> tuple[dict, list[dict]]:
    original_video = f"s3://fixtures/videos/{camera_id}.mp4"
    rows = [
        _segment(
            camera_id,
            location,
            capture_type,
            original_video,
            upload_timestamp,
            number,
            caption,
            counts,
        )
        for number, (caption, counts) in enumerate(segments)
    ]
    card = {
        "original_video": original_video,
        "filename": f"{camera_id}.mp4",
        "camera_id": camera_id,
        "location": location,
        "capture_type": capture_type,
        "total_segments": len(rows),
        "duration_sec": float(len(rows) * 5),
        "upload_timestamp": upload_timestamp,
        "preview_source": rows[0]["source"] if rows else "",
    }
    return card, rows


_WAREHOUSE = [
    (
        "An overhead camera looks down an empty racking corridor. Cardboard boxes sit on pallets and nothing travels through the frame.",
        {},
    ),
    (
        "A person is walking near a moving forklift in a warehouse aisle. The pedestrian stays a few metres ahead of the yellow forklift as it carries a pallet toward the dock.",
        {"person": 1},
    ),
    (
        "A person is walking near a moving forklift in a warehouse aisle. The forklift slows while the person crosses in front of the forks.",
        {"person": 1},
    ),
    (
        "A worker in a blue vest stands at a packing bench sealing cartons. The corridor behind them is clear of vehicles.",
        {"person": 1},
    ),
    (
        "A person is walking near a moving forklift in a warehouse aisle. Two people are visible, and one steps aside as the forklift approaches.",
        {"person": 2},
    ),
    (
        "A yellow forklift is parked and switched off beside the racking. No pedestrians are present and the forks rest on the floor.",
        {},
    ),
    (
        "Two workers talk beside a stack of shrink-wrapped cartons. They stay still and no vehicle is operating nearby.",
        {"person": 2},
    ),
    (
        "A person is walking near a moving forklift in a warehouse aisle. The forklift travels the same direction as the person and closes the gap.",
        {"person": 1},
    ),
    (
        "The dock door is shut. A row of idle pallet jacks lines the wall and the concrete floor is empty.",
        {},
    ),
    (
        "Fluorescent lights reflect off a scuffed concrete floor. Shelves are full and the camera sees no activity.",
        {},
    ),
    (
        "A person is walking near a moving forklift in a warehouse aisle. The person turns across the aisle directly ahead of the moving forklift.",
        {"person": 1},
    ),
    (
        "A stationary forklift sits at the far end of the building while a worker checks a clipboard near the office door, well away from the vehicle.",
        {"person": 1},
    ),
]

_DASHCAM = [
    (
        "The dashcam travels along a multi-lane city street with parked vehicles on both sides. Traffic ahead is light and the crosswalk is empty.",
        {"car": 3},
    ),
    (
        "A pedestrian steps into the road in front of the car. The person leaves the curb while vehicles approach the intersection.",
        {"person": 1, "car": 2},
    ),
    (
        "A pedestrian steps into the road in front of the car. Two people are mid-crossing and a car waits close behind them.",
        {"person": 2, "car": 1},
    ),
    (
        "People wait on the sidewalk at a red signal. They remain on the curb and the roadway ahead stays clear.",
        {"person": 2, "car": 1},
    ),
    (
        "A pedestrian steps into the road in front of the car. The person is in the travel lane as the dashcam closes distance.",
        {"person": 1, "car": 3},
    ),
    (
        "Cars are stopped at a red traffic signal. Brake lights fill the lane and nobody enters the roadway.",
        {"car": 4},
    ),
    (
        "The vehicle passes a row of parked cars beside a quiet storefront. The sidewalk is empty.",
        {"car": 2},
    ),
    (
        "A pedestrian steps into the road in front of the car. A person steps off the median into the lane with a car passing on the left.",
        {"person": 1, "car": 1},
    ),
    (
        "Night footage shows a wet street and a single vehicle ahead. Streetlights reflect on the pavement.",
        {"car": 1},
    ),
    (
        "The dashcam approaches a marked crosswalk that is currently clear. Vehicles continue through the intersection.",
        {"car": 2},
    ),
]

_HIGHWAY = [
    (
        "Traffic flows steadily on a three-lane interstate. Cars keep their lanes and spacing stays even.",
        {"car": 4},
    ),
    (
        "A truck changes lanes in dense traffic. The truck moves from the right lane toward the center while cars brake nearby.",
        {"truck": 1, "car": 3},
    ),
    (
        "A truck changes lanes in dense traffic. The trailer swings across the dashed line as a car occupies the adjacent lane.",
        {"truck": 1, "car": 2},
    ),
    (
        "Several cars travel in a loose pack. No large vehicles are changing position.",
        {"car": 5},
    ),
    (
        "A truck changes lanes in dense traffic. Two trucks are visible and one moves left across a lane line between cars.",
        {"truck": 2, "car": 2},
    ),
    (
        "A motorcycle and several cars continue straight. Lane positions stay constant.",
        {"car": 3, "motorcycle": 1},
    ),
    (
        "A truck changes lanes in dense traffic. The truck drifts left without a large gap and cars bunch up behind it.",
        {"truck": 1, "car": 4},
    ),
    (
        "Open highway with moderate flow. Cars remain centered in their lanes.",
        {"car": 3},
    ),
    (
        "A truck stays in its lane beside a line of cars. It does not cross the lane marking.",
        {"truck": 1, "car": 2},
    ),
    (
        "A truck changes lanes in dense traffic. The truck completes a move into a gap with a car close on its bumper.",
        {"truck": 1, "car": 1},
    ),
    (
        "A wide section of freeway with two cars far ahead. The road is otherwise clear.",
        {"car": 2},
    ),
    (
        "Brake lights ripple through a queue of cars. Everyone holds their lane.",
        {"car": 4},
    ),
    (
        "A truck changes lanes in dense traffic. The truck crosses two lane markings while cars occupy both sides.",
        {"truck": 1, "car": 3},
    ),
    (
        "The highway opens up and a single car travels ahead in the left lane.",
        {"car": 1},
    ),
]


def _catalog() -> tuple[list[dict], dict[str, list[dict]]]:
    specs = [
        ("sdg_warehouse_cam-2", "warehouse3", "cctv", "2026-09-01T12:00:00", _WAREHOUSE),
        ("pie_cam-3", "toronto", "dashcam", "2026-09-02T08:30:00", _DASHCAM),
        ("i24_cam-1", "nashville", "traffic", "2026-09-03T16:45:00", _HIGHWAY),
    ]
    videos: list[dict] = []
    by_video: dict[str, list[dict]] = {}
    for camera_id, location, capture_type, uploaded, segments in specs:
        card, rows = _video(camera_id, location, capture_type, uploaded, segments)
        videos.append(card)
        by_video[card["original_video"]] = rows
    return videos, by_video


_VIDEOS, _SEGMENTS = _catalog()


class _EmptyBody:
    def read(self, n: int = -1) -> bytes:
        return b""

    def close(self) -> None:
        return None


class FakeVSSClient:
    def __init__(self, base_url="", username="", password="", timeout=30):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout

    def list_videos(self) -> list[dict]:
        return copy.deepcopy(_VIDEOS)

    def segments(self, original_video) -> list[dict]:
        rows = _SEGMENTS.get(original_video, [])
        return copy.deepcopy(sorted(rows, key=lambda row: row["segment_number"]))

    def search(self, query, top_k=20, min_similarity=0.25, metadata_filters=None) -> list[dict]:
        filters = metadata_filters if isinstance(metadata_filters, dict) else {}
        limit = top_k if isinstance(top_k, int) else 20
        if limit < 0:
            limit = 0
        threshold = min_similarity if isinstance(min_similarity, (int, float)) else 0.25
        hits: list[dict] = []
        for rows in _SEGMENTS.values():
            for row in rows:
                if not _filters_match(row, filters):
                    continue
                score = _overlap(query, row["caption"])
                if score < threshold:
                    continue
                hit = copy.deepcopy(row)
                hit["similarity"] = score
                hits.append(hit)
        hits.sort(key=lambda row: (-row["similarity"], row["camera_id"], row["segment_number"]))
        return hits[:limit]

    def detections(self, source) -> dict | None:
        counts: dict[str, int] = {}
        for rows in _SEGMENTS.values():
            for row in rows:
                if row["source"] == source:
                    counts = dict(row["object_counts"])
                    break
        detections = []
        for label, count in counts.items():
            for _ in range(min(int(count), 3)):
                detections.append(
                    {"label": label, "confidence": 0.86, "bbox": [12, 24, 80, 160]}
                )
        return {
            "source": source,
            "segment_source": source,
            "fps": 30.0,
            "frame_count": 1,
            "detection_count": len(detections),
            "object_classes": list(counts.keys()),
            "object_counts": counts,
            "max_detection_conf": 0.86 if detections else 0.0,
            "frames": [
                {
                    "frame_index": 0,
                    "time_sec": 0.0,
                    "detections": detections,
                }
            ],
        }

    def open_stream(self, source, range_header=None) -> tuple[int, dict, _EmptyBody]:
        return (404, {}, _EmptyBody())

    def ping(self) -> bool:
        return True


def _filters_match(row: dict, filters: dict) -> bool:
    for key, expected in filters.items():
        if expected is None:
            continue
        if row.get(key) != expected:
            return False
    return True
