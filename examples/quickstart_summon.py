"""Quickstart example: Programmatic usage of AgentEverywhereFlow.

Shows how to discover target windows, capture the viewport, and trigger an agent loop.
"""

from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.config import AppConfig, ExecutionMode


def main():
    # 1. Initialize capturer for current OS
    capturer = get_capturer()

    # 2. List available targets (Displays & Windows)
    targets = capturer.list_targets()
    print(f"Discovered {len(targets)} available targets on your system:")
    for idx, t in enumerate(targets[:5]):
        print(f"  [{idx}] ({t.target_type.value}) {t.title} [Size: {t.rect.width}x{t.rect.height}]")

    if not targets:
        print("No interactive targets found.")
        return

    # 3. Select target (e.g. the first target)
    chosen_target = targets[0]
    print(f"\nBinding agent to: '{chosen_target.title}'")

    # 4. Capture initial viewport
    img = capturer.capture(chosen_target)
    print(f"Viewport captured: {img.size[0]}x{img.size[1]} px")

    # 5. Initialize Agent loop with AppConfig
    cfg = AppConfig(
        max_steps=5,
        default_mode=ExecutionMode.MINIMAL_PYTHON,
    )
    loop = AgentLoop(app_config=cfg)

    # 6. Execute task (Requires valid API key in .env or AEF_API_KEY)
    print(f"AgentLoop instance created successfully: {loop}")
    print("To execute an autonomous run, uncomment:")
    print("  loop.run(target=chosen_target, user_task='Read header')")


if __name__ == "__main__":
    main()
