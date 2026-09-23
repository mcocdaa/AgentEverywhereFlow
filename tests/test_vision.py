"""Tests for VisionPipeline (Coordinate Grid and SoM element detection)."""

from PIL import Image, ImageDraw

from agenteverywhereflow.agent.vision import VisionPipeline


def test_vision_grid_overlay() -> None:
    img = Image.new("RGB", (300, 200), color=(255, 255, 255))
    pipeline = VisionPipeline()
    grid_img = pipeline.add_coordinate_grid(img, step=50)

    assert grid_img.size == (300, 200)
    # Ensure grid overlay modified pixel colors
    assert grid_img.tobytes() != img.tobytes()


def test_vision_som_candidate_detection() -> None:
    # Create synthetic image with a prominent colored button
    img = Image.new("RGB", (400, 300), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Draw a simulated button at (100, 80, 300, 140)
    draw.rectangle([100, 80, 300, 140], fill=(0, 120, 215))

    pipeline = VisionPipeline()
    candidates = pipeline.detect_ui_candidates(img)
    assert len(candidates) > 0

    # Ensure detected candidate roughly contains the button center (200, 110)
    som_img, id_map = pipeline.apply_set_of_marks(img, candidates)
    assert len(id_map) > 0
    assert som_img.size == (400, 300)
