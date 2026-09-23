"""Coordinate mapping and transformation between target viewports and physical screen."""

from agenteverywhereflow.capturer.base import TargetInfo


class CoordinateProjector:
    """Projects viewport/image coordinates onto physical screen coordinates."""

    @staticmethod
    def to_screen_coords(
        target: TargetInfo,
        x: float,
        y: float,
        img_width: int,
        img_height: int,
        is_normalized: bool = False,
    ) -> tuple[int, int]:
        """Convert image/viewport coordinates into global desktop coordinates.

        Args:
            target: The TargetInfo representing the active screen or window.
            x: Horizontal coordinate (pixel or normalized 0.0-1.0 or 0-1000).
            y: Vertical coordinate (pixel or normalized 0.0-1.0 or 0-1000).
            img_width: Width of the image analyzed by the VLM.
            img_height: Height of the image analyzed by the VLM.
            is_normalized: True if x, y are in [0, 1] range.

        Returns:
            (screen_x, screen_y) ready for OS-level mouse clicks.
        """
        # 1. Convert to relative target pixel coordinates
        if is_normalized:
            rel_x = x * target.rect.width
            rel_y = y * target.rect.height
        elif x <= 1.0 and y <= 1.0 and (img_width > 1 and img_height > 1):
            # Auto-detect normalized [0.0, 1.0]
            rel_x = x * target.rect.width
            rel_y = y * target.rect.height
        elif x <= 1000 and y <= 1000 and (img_width > 1000 or target.rect.width > 1000):
            # Sometimes models return [0, 1000] scale (e.g. Qwen2-VL or Claude)
            # If img_width is roughly target.rect.width, treat directly
            scale_x = target.rect.width / img_width
            scale_y = target.rect.height / img_height
            rel_x = x * scale_x
            rel_y = y * scale_y
        else:
            # Direct pixel coordinate from captured image
            scale_x = target.rect.width / max(1, img_width)
            scale_y = target.rect.height / max(1, img_height)
            rel_x = x * scale_x
            rel_y = y * scale_y

        # Clamp inside target boundaries
        rel_x = max(0, min(rel_x, target.rect.width - 1))
        rel_y = max(0, min(rel_y, target.rect.height - 1))

        # 2. Add target window/display origin
        screen_x = int(target.rect.x + rel_x)
        screen_y = int(target.rect.y + rel_y)

        return screen_x, screen_y
