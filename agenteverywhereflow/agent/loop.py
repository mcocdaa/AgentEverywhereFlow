"""Agent Core Loop: Observe -> Reason -> Act."""

import base64
import io
import json
import re
from typing import Any

from PIL import Image
from rich.console import Console
from rich.panel import Panel

from agenteverywhereflow.agent.prompts import get_prompt_for_target
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.config import AppConfig, ExecutionMode, config
from agenteverywhereflow.engine import get_engine


class AgentLoop:
    """Orchestrates the autonomous perception-action loop on the chosen target."""

    def __init__(
        self,
        app_config: AppConfig | None = None,
        planner_func: Any | None = None,
    ) -> None:
        self.config = app_config or config
        self.planner_func = planner_func
        self.capturer = get_capturer()
        self.console = Console()
        self._client: Any = None

    @property
    def client(self) -> Any:
        """Lazily initialize OpenAI client on demand."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.config.api_key or "sk-fake",
                base_url=self.config.base_url,
            )
        return self._client

    def _encode_image(self, img: Image.Image) -> str:
        """Encode PIL Image to base64 JPEG string."""
        buffer = io.BytesIO()
        # Convert RGBA or other modes to RGB if needed
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def run(
        self,
        target: TargetInfo,
        user_task: str,
        mode: ExecutionMode | None = None,
        max_steps: int | None = None,
    ) -> bool:
        """Execute the agent task loop bound to the specified target."""
        mode = mode or self.config.default_mode
        max_steps = max_steps or self.config.max_steps
        engine = get_engine(mode)
        is_minimal = mode == ExecutionMode.MINIMAL_PYTHON

        system_prompt = get_prompt_for_target(target, is_minimal_mode=is_minimal)

        self.console.print(
            Panel(
                f"[bold cyan]🎯 Target:[/bold cyan] {target}\n"
                f"[bold green]📋 Task:[/bold green] {user_task}\n"
                f"[bold yellow]⚙️ Mode:[/bold yellow] {mode.value.upper()}",
                title="🚀 AgentEverywhereFlow Loop Initialized",
            )
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Task to accomplish on this target window/screen: {user_task}",
                    }
                ],
            },
        ]

        step = 0
        while step < max_steps:
            step += 1
            self.console.rule(f"[bold blue]Step {step} / {max_steps}[/bold blue]")

            # 1. Bring target into foreground
            self.capturer.focus(target)

            # 2. Capture screenshot
            img = self.capturer.capture(target)
            img_b64 = self._encode_image(img)

            # Add screenshot to current observation message
            step_message = {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Current screenshot of {target.title} ({img.width}x{img.height}). What is your next action?",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                ],
            }
            messages.append(step_message)

            # Optionally persist step screenshot for visual audit
            try:
                self.config.screenshot_dir.mkdir(parents=True, exist_ok=True)
                img.save(self.config.screenshot_dir / f"step_{step}.png")
            except Exception:
                pass

            # 3. Call Vision-LLM or Custom Planner
            self.console.print("[dim]🧠 Thinking and analyzing viewport...[/dim]")
            if self.planner_func:
                try:
                    assistant_text = self.planner_func(messages, target, step)
                except Exception as e:
                    self.console.print(f"[bold red]❌ Planner Error: {e}[/bold red]")
                    return False
            else:
                try:
                    response = self.client.chat.completions.create(
                        model=self.config.model_name,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=0.2,
                    )
                    assistant_text = response.choices[0].message.content or ""
                except Exception as e:
                    self.console.print(f"[bold red]❌ LLM API Error: {e}[/bold red]")
                    return False

            self.console.print(Panel(assistant_text, title="🤖 Agent Reasoning", style="cyan"))
            messages.append({"role": "assistant", "content": assistant_text})

            # 4. Check for task completion declaration
            if "TASK_COMPLETED" in assistant_text:
                self.console.print("[bold green]🎉 Task successfully completed![/bold green]")
                return True

            # 5. Extract and Execute Actions
            if is_minimal:
                # Extract python block
                code_match = re.search(r"```(?:python)?\s*(.*?)\s*```", assistant_text, re.DOTALL)
                if not code_match:
                    self.console.print("[yellow]⚠️ No python block found in output.[/yellow]")
                    continue
                code_to_exec = code_match.group(1).strip()
                result = engine.execute(code_to_exec, target)
            else:
                # Extract JSON block
                json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", assistant_text, re.DOTALL)
                if not json_match:
                    self.console.print("[yellow]⚠️ No JSON action block found.[/yellow]")
                    continue
                try:
                    payload = json.loads(json_match.group(1).strip())
                except Exception as e:
                    self.console.print(f"[red]Failed to parse JSON action: {e}[/red]")
                    continue

                if payload.get("action") == "finish":
                    self.console.print(
                        f"[bold green]🎉 Finished: {payload.get('message', '')}[/bold green]"
                    )
                    return True

                result = engine.execute(payload, target)

            # 6. Report Execution Result
            status_style = "green" if result.success else "red"
            self.console.print(
                f"[{status_style}]Execution Success: {result.success}[/{status_style}] | "
                f"Output: {result.output or 'None'} | Error: {result.error or 'None'}"
            )

        self.console.print("[bold yellow]⚠️ Reached maximum step limit.[/bold yellow]")
        return False
