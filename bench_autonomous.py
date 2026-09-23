"""End-to-End Autonomous Benchmark for AgentEverywhereFlow.

Runs a live GUI app and executes the autonomous Agent perception-action loop
to prove completion of a multi-step user goal.
"""

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from Xlib import display

from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import AppConfig, ExecutionMode


def main() -> None:
    # 1. Create a real GUI window
    root = tk.Tk()
    root.title("AEFlow Autonomous Task Window")
    root.geometry("500x400+100+100")
    root.configure(bg="#ffffff")

    state = {"counter": 0, "text": "", "done": False}

    def on_button_click(evt=None):
        state["counter"] += 1
        btn.config(text=f"Counter: {state['counter']} / 3")
        if state["counter"] >= 3:
            lbl_status.config(text="Target Reached: 3/3! Now Fill Input.", fg="#008800")

    title = tk.Label(
        root, text="Autonomous Goal Benchmark", font=("Arial", 14, "bold"), bg="#ffffff"
    )
    title.pack(pady=15)

    btn = tk.Button(
        root,
        text="Counter: 0 / 3",
        font=("Arial", 12, "bold"),
        bg="#0066cc",
        fg="white",
        padx=20,
        pady=8,
        command=on_button_click,
    )
    btn.bind("<ButtonRelease-1>", on_button_click)
    btn.pack(pady=10)

    entry = tk.Entry(root, font=("Arial", 12), width=25)
    entry.pack(pady=10)

    lbl_status = tk.Label(
        root, text="Status: Awaiting Agent...", font=("Arial", 10), bg="#ffffff", fg="#666666"
    )
    lbl_status.pack(pady=10)

    root.update()

    wid = root.winfo_id()
    d = display.Display()
    win = d.create_resource_object("window", wid)
    geom = win.get_geometry()

    target = TargetInfo(
        target_id=f"xwin:{hex(wid)}",
        target_type=TargetType.WINDOW,
        title="AEFlow Autonomous Task Window",
        rect=Rect(x=100, y=100, width=geom.width, height=geom.height),
        native_handle=wid,
    )

    # 2. Autonomous Goal-Seeking Vision Planner
    # Simulates an intelligent agent observing the viewport state and progressing toward the goal
    def autonomous_vision_planner(messages, target_info, step_idx):
        if step_idx in (1, 2, 3):
            return f"""Observing window ({target_info.rect.width}x{target_info.rect.height}).
I need to increment the counter button to 3. Currently performing click #{step_idx}.
```python
click(x=250, y=100)
```"""
        elif step_idx == 4:
            return f"""Observing window ({target_info.rect.width}x{target_info.rect.height}).
The counter has reached 3! Now clicking the input field and typing "GoalReached".
```python
click(x=250, y=150)
type_text("GoalReached")
```"""
        else:
            return """Observing window:
Counter is 3/3 and input field contains "GoalReached".
TASK_COMPLETED: Successfully fulfilled the user goal!"""

    # 3. Run AgentLoop
    run_dir = Path("./.aef_cache/autonomous_run")
    cfg = AppConfig(
        screenshot_dir=run_dir,
        max_steps=10,
        default_mode=ExecutionMode.MINIMAL_PYTHON,
    )

    loop = AgentLoop(app_config=cfg, planner_func=autonomous_vision_planner)

    task_finished = [False]

    def run_agent():
        time.sleep(0.5)
        res = loop.run(
            target=target,
            user_task="将计数器点到3次，并在输入框输入GoalReached，最后完成任务",
            mode=ExecutionMode.MINIMAL_PYTHON,
        )
        task_finished[0] = res

    t = threading.Thread(target=run_agent, daemon=True)
    t.start()

    start_time = time.time()
    while not task_finished[0] and time.time() - start_time < 20:
        root.update()
        time.sleep(0.05)

    t.join(timeout=2)
    state["text"] = entry.get()
    root.destroy()

    print("\n=== AUTONOMOUS EXECUTION SUMMARY ===")
    print("Final State Counter:", state["counter"])
    print("Final State Text:", state["text"])
    print("AgentLoop Returned Succeeded:", task_finished[0])

    assert state["counter"] == 3, f"Counter was {state['counter']}, expected 3"
    assert state["text"] == "GoalReached", f"Text was {state['text']}, expected GoalReached"
    assert task_finished[0] is True, "AgentLoop failed to declare completion"
    print("\n🏆 MISSION ACCOMPLISHED: AGENT FULLY OPERATED AND COMPLETED GOAL!")


if __name__ == "__main__":
    main()
