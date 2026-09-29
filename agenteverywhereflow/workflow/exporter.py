"""Exporter for converting agent conversation sessions into standalone scripts and YAML workflows."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agenteverywhereflow.session.session import ChatSession
from agenteverywhereflow.workflow.models import WorkflowDefinition, WorkflowStep, WorkflowTarget


class WorkflowExporter:
    """Extracts, formats, and exports recorded agent actions into standalone workflows."""

    @classmethod
    def from_session(
        cls,
        session: ChatSession,
        name: str | None = None,
        description: str | None = None,
    ) -> WorkflowDefinition:
        """Construct a WorkflowDefinition from an active or restored ChatSession."""
        wf_name = name or f"workflow_{session.session_id}"
        wf_desc = (
            description
            or f"Replayable workflow exported from dialogue session '{session.session_id}'"
        )

        # 1. Collect targets
        targets: list[WorkflowTarget] = []
        for t in session.targets:
            targets.append(
                WorkflowTarget(
                    target_id=t.target_id,
                    title=t.title,
                    process_name=t.process_name,
                    target_type=t.target_type.value
                    if hasattr(t.target_type, "value")
                    else str(t.target_type),
                    rect={
                        "x": t.rect.x,
                        "y": t.rect.y,
                        "width": t.rect.width,
                        "height": t.rect.height,
                    },
                )
            )

        # 2. Extract recorded steps
        steps: list[WorkflowStep] = []
        step_counter = 1

        for rec in session.recorded_steps:
            mode = rec.get("mode", "minimal")
            t_title = rec.get("target_title") or session.target.title

            if mode == "guarded":
                act_data = dict(rec.get("action", {}))
                act_name = act_data.pop("action", "unknown").lower()
                tgt_override = act_data.pop("target", None) or t_title
                steps.append(
                    WorkflowStep(
                        step_number=step_counter,
                        action=act_name,
                        target=tgt_override,
                        params=act_data,
                        description=f"{act_name} on {tgt_override}",
                    )
                )
                step_counter += 1

            elif mode == "minimal":
                code = rec.get("code", "")
                parsed_atomic = cls._parse_codeact_lines(code, default_target=t_title)
                if parsed_atomic:
                    for s in parsed_atomic:
                        s.step_number = step_counter
                        steps.append(s)
                        step_counter += 1
                else:
                    steps.append(
                        WorkflowStep(
                            step_number=step_counter,
                            action="codeact",
                            target=t_title,
                            code=code,
                            description=f"CodeAct block on {t_title}",
                        )
                    )
                    step_counter += 1

        # Fallback: if recorded_steps was empty (e.g. from an older session), parse assistant messages
        if not steps and session.messages:
            steps = cls._extract_steps_from_messages(session.messages, session.target.title)

        return WorkflowDefinition(
            name=wf_name,
            description=wf_desc,
            created_at=session.created_at,
            targets=targets,
            steps=steps,
            metadata={
                "session_id": session.session_id,
                "total_turns": session.turn_count,
                "total_steps": session.total_steps,
                "mode": session.mode.value,
            },
        )

    @staticmethod
    def _parse_codeact_lines(code: str, default_target: str) -> list[WorkflowStep]:
        """Try parsing standard tool calls in Python CodeAct block into discrete WorkflowSteps."""
        steps: list[WorkflowStep] = []
        current_target = default_target

        for raw_line in code.split("\n"):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # switch_to("query") or focus("query")
            m_switch = re.match(r"(?:switch_to|focus)\(\s*['\"]([^'\"]+)['\"]\s*\)", line)
            if m_switch:
                current_target = m_switch.group(1)
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="switch_target",
                        target=current_target,
                        params={"target": current_target},
                        code=line,
                        description=f"Switch focus to '{current_target}'",
                    )
                )
                continue

            # click(x, y, button="left", clicks=1, target=...)
            m_click = re.match(
                r"click\(\s*([0-9.]+)\s*,\s*([0-9.]+)(?:,\s*button=['\"]([^'\"]+)['\"])?(?:,\s*clicks=(\d+))?(?:,\s*target=['\"]([^'\"]+)['\"])?\)",
                line,
            )
            if m_click:
                x = float(m_click.group(1))
                y = float(m_click.group(2))
                button = m_click.group(3) or "left"
                clicks = int(m_click.group(4) or 1)
                target = m_click.group(5) or current_target
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="click",
                        target=target,
                        params={"x": x, "y": y, "button": button, "clicks": clicks},
                        code=line,
                        description=f"Click at ({x}, {y}) on '{target}'",
                    )
                )
                continue

            # double_click(x, y, ...)
            m_dclick = re.match(
                r"double_click\(\s*([0-9.]+)\s*,\s*([0-9.]+)(?:,\s*target=['\"]([^'\"]+)['\"])?\)",
                line,
            )
            if m_dclick:
                x = float(m_dclick.group(1))
                y = float(m_dclick.group(2))
                target = m_dclick.group(3) or current_target
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="double_click",
                        target=target,
                        params={"x": x, "y": y},
                        code=line,
                        description=f"Double-click at ({x}, {y}) on '{target}'",
                    )
                )
                continue

            # right_click(x, y, ...)
            m_rclick = re.match(
                r"right_click\(\s*([0-9.]+)\s*,\s*([0-9.]+)(?:,\s*target=['\"]([^'\"]+)['\"])?\)",
                line,
            )
            if m_rclick:
                x = float(m_rclick.group(1))
                y = float(m_rclick.group(2))
                target = m_rclick.group(3) or current_target
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="right_click",
                        target=target,
                        params={"x": x, "y": y},
                        code=line,
                        description=f"Right-click at ({x}, {y}) on '{target}'",
                    )
                )
                continue

            # type_text("text", target=...)
            m_type = re.match(
                r"type_text\(\s*(?:text=)?['\"](.*?)['\"](?:,\s*target=['\"]([^'\"]+)['\"])?\)",
                line,
            )
            if m_type:
                text = m_type.group(1)
                target = m_type.group(2) or current_target
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="type",
                        target=target,
                        params={"text": text},
                        code=line,
                        description=f"Type text '{text}' on '{target}'",
                    )
                )
                continue

            # press("key", target=...)
            m_press = re.match(
                r"press\(\s*(?:key=)?['\"]([^'\"]+)['\"](?:,\s*target=['\"]([^'\"]+)['\"])?\)",
                line,
            )
            if m_press:
                key = m_press.group(1)
                target = m_press.group(2) or current_target
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="press",
                        target=target,
                        params={"key": key},
                        code=line,
                        description=f"Press key '{key}' on '{target}'",
                    )
                )
                continue

            # hotkey("ctrl", "c", ...)
            m_hotkey = re.match(r"hotkey\((.*?)\)", line)
            if m_hotkey:
                arg_tokens = [a.strip().strip("'\"") for a in m_hotkey.group(1).split(",")]
                keys = [k for k in arg_tokens if not k.startswith("target=")]
                target = current_target
                for k in arg_tokens:
                    if k.startswith("target="):
                        target = k.split("=", 1)[1].strip("'\"")
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="hotkey",
                        target=target,
                        params={"keys": keys},
                        code=line,
                        description=f"Hotkey ({'+'.join(keys)}) on '{target}'",
                    )
                )
                continue

            # wait(seconds)
            m_wait = re.match(r"wait\(\s*([0-9.]+)\s*\)", line)
            if m_wait:
                sec = float(m_wait.group(1))
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="wait",
                        target=current_target,
                        params={"seconds": sec},
                        code=line,
                        description=f"Wait {sec}s",
                    )
                )
                continue

            # scroll(amount, ...)
            m_scroll = re.match(
                r"scroll\(\s*([0-9\-]+)(?:,\s*([0-9.]+),\s*([0-9.]+))?(?:,\s*target=['\"]([^'\"]+)['\"])?\)",
                line,
            )
            if m_scroll:
                amount = int(m_scroll.group(1))
                sx_opt: float | None = float(m_scroll.group(2)) if m_scroll.group(2) else None
                sy_opt: float | None = float(m_scroll.group(3)) if m_scroll.group(3) else None
                target = m_scroll.group(4) or current_target
                params: dict[str, Any] = {"amount": amount}
                if sx_opt is not None and sy_opt is not None:
                    params["x"] = sx_opt
                    params["y"] = sy_opt
                steps.append(
                    WorkflowStep(
                        step_number=0,
                        action="scroll",
                        target=target,
                        params=params,
                        code=line,
                        description=f"Scroll amount={amount} on '{target}'",
                    )
                )
                continue

            # If complex python statement is found, return empty so it falls back to full codeact block
            return []

        return steps

    @classmethod
    def _extract_steps_from_messages(
        cls, messages: list[dict[str, Any]], default_target: str
    ) -> list[WorkflowStep]:
        """Fallback extractor from assistant text messages."""
        from agenteverywhereflow.agent.loop import extract_codeact_blocks

        steps: list[WorkflowStep] = []
        step_counter = 1
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                code = extract_codeact_blocks(content)
                if code:
                    parsed = cls._parse_codeact_lines(code, default_target)
                    if parsed:
                        for s in parsed:
                            s.step_number = step_counter
                            steps.append(s)
                            step_counter += 1
                    else:
                        steps.append(
                            WorkflowStep(
                                step_number=step_counter,
                                action="codeact",
                                target=default_target,
                                code=code,
                                description=f"CodeAct block on {default_target}",
                            )
                        )
                        step_counter += 1
        return steps

    @classmethod
    def export_to_yaml(cls, workflow: WorkflowDefinition, output_path: Path | str) -> Path:
        """Export workflow definition to declarative YAML format."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        dump_data = workflow.model_dump()
        with open(out, "w", encoding="utf-8") as f:
            yaml.safe_dump(dump_data, f, sort_keys=False, allow_unicode=True, indent=2)
        return out

    @classmethod
    def export_to_python(cls, workflow: WorkflowDefinition, output_path: Path | str) -> Path:
        """Export workflow to a standalone, zero-LLM executable Python script."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        created_str = datetime.fromtimestamp(workflow.created_at).strftime("%Y-%m-%d %H:%M:%S")

        lines: list[str] = [
            "#!/usr/bin/env python3",
            '"""',
            f"Standalone Automated GUI Workflow: {workflow.name}",
            f"Generated by AgentEverywhereFlow at {created_str}",
            f"Description: {workflow.description}",
            "",
            "Requires zero LLM API calls. Executes actions deterministically at maximum speed.",
            '"""',
            "",
            "import sys",
            "import time",
            "from agenteverywhereflow.actions import driver",
            "from agenteverywhereflow.actions.coords import CoordinateProjector",
            "from agenteverywhereflow.capturer import get_capturer",
            "from agenteverywhereflow.capturer.base import TargetInfo",
            "",
            "",
            "def find_target(capturer, query: str) -> TargetInfo:",
            '    """Find and re-bind window by title or process name."""',
            "    all_targets = capturer.list_targets()",
            "    q = query.strip().lower()",
            "    # 1. Exact or prefix ID match",
            "    for t in all_targets:",
            "        if q == t.target_id.lower() or t.target_id.lower().startswith(q):",
            "            return t",
            "    # 2. Substring match on title or process name",
            "    for t in all_targets:",
            '        if q in t.title.lower() or q in (t.process_name or "").lower():',
            "            return t",
            "    raise RuntimeError(f\"Required target window matching '{query}' was not found.\")",
            "",
            "",
            "def main():",
            "    capturer = get_capturer()",
            '    print("🚀 Replaying Workflow: ' + workflow.name + '")',
            "",
            "    # Target resolution cache",
            "    resolved_targets: dict[str, TargetInfo] = {}",
            "    active_target: TargetInfo | None = None",
            "",
        ]

        # Targets mapping table for initial resolutions
        for t in workflow.targets:
            lines.append(f"    # Target: {t.title} (ID: {t.target_id})")

        lines.append("")
        lines.append("    # --- Begin Replay Sequence ---")

        for s in workflow.steps:
            lines.append(f"    # Step {s.step_number}: {s.description or s.action}")
            tgt_name = s.target or (workflow.targets[0].title if workflow.targets else "")

            if s.action == "switch_target":
                lines.append(f'    active_target = find_target(capturer, "{tgt_name}")')
                lines.append("    capturer.focus(active_target)")
                lines.append(
                    f'    print("  [Step {s.step_number}] Focused target: " + active_target.title)'
                )
                lines.append("    time.sleep(0.3)")

            elif s.action in ("click", "double_click", "right_click"):
                if tgt_name:
                    lines.append(
                        f'    if not active_target or "{tgt_name.lower()}" not in active_target.title.lower():'
                    )
                    lines.append(f'        active_target = find_target(capturer, "{tgt_name}")')
                    lines.append("        capturer.focus(active_target)")
                x = s.params.get("x", 0)
                y = s.params.get("y", 0)
                button = s.params.get("button", "left")
                clicks = s.params.get("clicks", 2 if s.action == "double_click" else 1)
                if s.action == "right_click":
                    button = "right"
                    clicks = 1

                lines.append(f"    # Relative coords ({x}, {y}) against active target")
                lines.append("    sx, sy = CoordinateProjector.to_screen_coords(")
                lines.append("        target=active_target,")
                lines.append(f"        x={x},")
                lines.append(f"        y={y},")
                lines.append("        img_width=active_target.rect.width,")
                lines.append("        img_height=active_target.rect.height,")
                lines.append("    )")
                lines.append(
                    f'    driver.click(sx, sy, button="{button}", clicks={clicks}, window_handle=active_target.native_handle)'
                )
                lines.append(
                    f'    print("  [Step {s.step_number}] Clicked at ({x}, {y}) -> Screen (" + str(sx) + ", " + str(sy) + ")")'
                )
                lines.append("    time.sleep(0.3)")

            elif s.action == "type":
                text = s.params.get("text", "")
                if tgt_name:
                    lines.append(
                        f'    if not active_target or "{tgt_name.lower()}" not in active_target.title.lower():'
                    )
                    lines.append(f'        active_target = find_target(capturer, "{tgt_name}")')
                    lines.append("        capturer.focus(active_target)")
                lines.append(
                    f"    driver.type_text({repr(text)}, window_handle=active_target.native_handle)"
                )
                lines.append(f'    print("  [Step {s.step_number}] Typed: {repr(text)}")')
                lines.append("    time.sleep(0.3)")

            elif s.action == "press":
                key = s.params.get("key", "")
                if tgt_name:
                    lines.append(
                        f'    if not active_target or "{tgt_name.lower()}" not in active_target.title.lower():'
                    )
                    lines.append(f'        active_target = find_target(capturer, "{tgt_name}")')
                    lines.append("        capturer.focus(active_target)")
                lines.append(
                    f"    driver.press_key({repr(key)}, window_handle=active_target.native_handle)"
                )
                lines.append(f'    print("  [Step {s.step_number}] Pressed key: {repr(key)}")')
                lines.append("    time.sleep(0.2)")

            elif s.action == "hotkey":
                keys = s.params.get("keys", [])
                if tgt_name:
                    lines.append(
                        f'    if not active_target or "{tgt_name.lower()}" not in active_target.title.lower():'
                    )
                    lines.append(f'        active_target = find_target(capturer, "{tgt_name}")')
                    lines.append("        capturer.focus(active_target)")
                keys_arg = ", ".join(repr(k) for k in keys)
                lines.append(
                    f"    driver.hotkey({keys_arg}, window_handle=active_target.native_handle)"
                )
                lines.append(f'    print("  [Step {s.step_number}] Hotkey: {keys_arg}")')
                lines.append("    time.sleep(0.3)")

            elif s.action == "scroll":
                amount = s.params.get("amount", 0)
                lines.append(f"    driver.scroll(amount={amount})")
                lines.append(f'    print("  [Step {s.step_number}] Scrolled amount: {amount}")')
                lines.append("    time.sleep(0.2)")

            elif s.action == "wait":
                sec = s.params.get("seconds", 1.0)
                lines.append(f"    time.sleep({sec})")
                lines.append(f'    print("  [Step {s.step_number}] Waited {sec}s")')

            elif s.action == "codeact" and s.code:
                lines.append("    # Raw CodeAct execution block")
                code_indented = "\n".join("    " + line for line in s.code.split("\n"))
                lines.append(code_indented)

            lines.append("")

        lines.append('    print("✅ Workflow execution finished successfully!")')
        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append("    main()")
        lines.append("")

        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return out
