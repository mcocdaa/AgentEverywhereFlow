"""Vision processing pipeline for AgentEverywhereFlow.

Includes:
- High-contrast Coordinate Grid & Ruler Overlay
- Set-of-Marks (SoM) with Non-Maximum Suppression (NMS) bounding box merging
- Viewport coordinate calibration
"""

from typing import NamedTuple
from PIL import Image, ImageDraw


class DetectedElement(NamedTuple):
    """An interactive UI element candidate detected in the viewport."""
    element_id: int
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    center: tuple[int, int]          # (cx, cy)
    area: int


class VisionPipeline:
    """Processes viewport screenshots for visual grounding and Set-of-Marks (SoM)."""

    def add_coordinate_grid(
        self,
        img: Image.Image,
        step: int = 50,
        grid_color: tuple[int, int, int, int] = (255, 60, 60, 80),
    ) -> Image.Image:
        """Overlay a semi-transparent coordinate grid with pixel tick labels."""
        canvas = img.convert("RGBA")
        overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        width, height = canvas.size

        # Vertical grid lines & tick marks
        for x in range(0, width, step):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
            draw.text((x + 2, 2), f"{x}", fill=(220, 30, 30, 220))

        # Horizontal grid lines & tick marks
        for y in range(0, height, step):
            draw.line([(0, y), (width, y)], fill=grid_color, width=1)
            draw.text((2, y + 2), f"{y}", fill=(220, 30, 30, 220))

        return Image.alpha_composite(canvas, overlay).convert("RGB")

    def _boxes_intersect_or_near(
        self,
        b1: tuple[int, int, int, int],
        b2: tuple[int, int, int, int],
        margin: int = 4,
    ) -> bool:
        """Check if two bounding boxes intersect or are close neighbors."""
        x1_a, y1_a, x2_a, y2_a = b1
        x1_b, y1_b, x2_b, y2_b = b2

        # Expand box A by margin
        return not (
            x2_a + margin < x1_b
            or x1_a - margin > x2_b
            or y2_a + margin < y1_b
            or y1_a - margin > y2_b
        )

    def _merge_boxes(
        self,
        boxes: list[tuple[int, int, int, int]],
        margin: int = 4,
    ) -> list[tuple[int, int, int, int]]:
        """Cluster and merge overlapping or adjacent bounding boxes."""
        if not boxes:
            return []

        merged: list[tuple[int, int, int, int]] = []
        unmerged = list(boxes)

        while unmerged:
            curr = unmerged.pop(0)
            did_merge = False

            for i, target in enumerate(merged):
                if self._boxes_intersect_or_near(curr, target, margin=margin):
                    # Merge curr into target
                    merged[i] = (
                        min(curr[0], target[0]),
                        min(curr[1], target[1]),
                        max(curr[2], target[2]),
                        max(curr[3], target[3]),
                    )
                    did_merge = True
                    break

            if not did_merge:
                merged.append(curr)

        # Multiple passes until stable
        if len(merged) < len(boxes):
            return self._merge_boxes(merged, margin=margin)
        return merged

    def detect_ui_candidates(
        self,
        img: Image.Image,
        min_width: int = 40,
        min_height: int = 15,
        max_area_ratio: float = 0.85,
    ) -> list[DetectedElement]:
        """Detect and cluster UI candidate regions into clean, unified controls."""
        gray = img.convert("L")
        width, height = gray.size
        if hasattr(gray, "get_flattened_data"):
            pixels = list(gray.get_flattened_data())
        else:
            pixels = list(gray.getdata())

        total_area = width * height
        max_allowed_area = int(total_area * max_area_ratio)

        # 1. Edge & gradient scan
        raw_boxes: list[tuple[int, int, int, int]] = []
        step = 10
        threshold = 20

        for y in range(step, height - step, step):
            for x in range(step, width - step, step):
                idx = y * width + x
                val = pixels[idx]

                diff_x = abs(val - pixels[y * width + (x + 1)])
                diff_y = abs(val - pixels[(y + 1) * width + x])

                if diff_x > threshold or diff_y > threshold:
                    # Bounding box around gradient change
                    raw_boxes.append((x - 10, y - 8, x + 25, y + 16))

        # 2. Merge overlapping boxes
        merged_boxes = self._merge_boxes(raw_boxes)

        # 3. Filter by size and create DetectedElement instances
        elements: list[DetectedElement] = []
        element_id = 1

        for box in sorted(merged_boxes, key=lambda b: (b[1], b[0])):
            x1 = max(0, box[0])
            y1 = max(0, box[1])
            x2 = min(width - 1, box[2])
            y2 = min(height - 1, box[3])
            bw = x2 - x1
            bh = y2 - y1
            area = bw * bh

            if bw >= min_width and bh >= min_height and area <= max_allowed_area:
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                elements.append(
                    DetectedElement(
                        element_id=element_id,
                        bbox=(x1, y1, x2, y2),
                        center=(cx, cy),
                        area=area,
                    )
                )
                element_id += 1

        return elements

    def apply_set_of_marks(
        self,
        img: Image.Image,
        elements: list[DetectedElement] | None = None,
    ) -> tuple[Image.Image, dict[int, tuple[int, int]]]:
        """Render clean, consolidated Set-of-Marks tags onto viewport."""
        if elements is None:
            elements = self.detect_ui_candidates(img)

        canvas = img.convert("RGBA")
        overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        id_to_coords: dict[int, tuple[int, int]] = {}

        palette = [
            (230, 57, 70, 220),   # Red
            (0, 119, 182, 220),   # Blue
            (42, 157, 143, 220),  # Teal Green
            (217, 119, 6, 220),   # Amber
            (124, 58, 237, 220),  # Violet
        ]

        for el in elements:
            x1, y1, x2, y2 = el.bbox
            cx, cy = el.center
            id_to_coords[el.element_id] = (cx, cy)
            color = palette[(el.element_id - 1) % len(palette)]

            # Draw bounding box
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)

            # Draw badge tag
            tag_text = f"[{el.element_id}]"
            badge_w = len(tag_text) * 8 + 8
            badge_h = 16
            tag_top = max(0, y1 - badge_h)
            draw.rectangle([x1, tag_top, x1 + badge_w, y1], fill=color)
            draw.text((x1 + 4, tag_top + 1), tag_text, fill=(255, 255, 255, 255))

        result = Image.alpha_composite(canvas, overlay).convert("RGB")
        return result, id_to_coords
