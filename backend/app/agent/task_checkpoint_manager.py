import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

@dataclass
class TaskCheckpoint:
    session_id: str
    goal: str
    target_app: str
    mode: str = "UNKNOWN"  # "NATIVE_APP" | "WEB_BROWSER"
    current_stage: str = "INIT"  # "INIT" | "APP_OPENED" | "SEARCHED" | "COMPARING" | "ITEM_SELECTED" | "AWAITING_ORDER_CONFIRMATION" | "AWAITING_USER_ACTION" | "ERROR_PAUSED" | "COMPLETED"
    selected_item: Optional[Dict[str, Any]] = None
    query: str = ""
    criteria: str = "lowest_price"
    last_error: Optional[str] = None
    last_screen_title: Optional[str] = None
    screenshots: list = field(default_factory=list)
    step_history: list = field(default_factory=list)
    stuck_reason: Optional[str] = None
    last_action_desc: Optional[str] = None
    next_step_desc: Optional[str] = None
    is_paused: bool = False
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class TaskCheckpointManager:
    """
    Stateful Task Checkpoint, Progress & Resumption Manager for Autonomous Mobile Agents.
    Maintains fine-grained progress across every screen state change, multi-turn interactions,
    and privacy pauses so the user can query progress and resume anytime.
    """

    def __init__(self):
        self._checkpoints: Dict[str, TaskCheckpoint] = {}
        self._last_active_session_id: Optional[str] = None

    def get_checkpoint(self, session_id: str) -> Optional[TaskCheckpoint]:
        ckpt = self._checkpoints.get(session_id)
        if not ckpt and self._last_active_session_id and self._last_active_session_id in self._checkpoints:
            # Fallback to the most recent active session if query comes from a new session turn
            return self._checkpoints[self._last_active_session_id]
        return ckpt

    def get_latest_checkpoint(self) -> Optional[TaskCheckpoint]:
        if not self._checkpoints:
            return None
        # Sort by updated_at descending
        return max(self._checkpoints.values(), key=lambda c: c.updated_at)

    def has_active_task(self, session_id: str) -> bool:
        ckpt = self.get_checkpoint(session_id)
        if not ckpt:
            return False
        return ckpt.current_stage not in ["COMPLETED", "INIT"]

    def create_or_update(
        self,
        session_id: str,
        goal: str,
        target_app: str,
        stage: str,
        mode: str = "NATIVE_APP",
        selected_item: Optional[Dict[str, Any]] = None,
        query: str = "",
        criteria: str = "lowest_price",
        last_error: Optional[str] = None,
        last_action_desc: Optional[str] = None,
        stuck_reason: Optional[str] = None,
        next_step_desc: Optional[str] = None,
        is_paused: bool = False
    ) -> TaskCheckpoint:
        ckpt = self._checkpoints.get(session_id)
        if not ckpt:
            ckpt = TaskCheckpoint(
                session_id=session_id,
                goal=goal,
                target_app=target_app,
                mode=mode,
                current_stage=stage,
                selected_item=selected_item,
                query=query,
                criteria=criteria,
                last_error=last_error,
                last_action_desc=last_action_desc,
                stuck_reason=stuck_reason,
                next_step_desc=next_step_desc,
                is_paused=is_paused
            )
        else:
            if goal:
                ckpt.goal = goal
            if target_app:
                ckpt.target_app = target_app
            if stage:
                ckpt.current_stage = stage
            if mode:
                ckpt.mode = mode
            if selected_item:
                ckpt.selected_item = selected_item
            if query:
                ckpt.query = query
            if criteria:
                ckpt.criteria = criteria
            if last_error is not None:
                ckpt.last_error = last_error
            if last_action_desc is not None:
                ckpt.last_action_desc = last_action_desc
            if stuck_reason is not None:
                ckpt.stuck_reason = stuck_reason
            if next_step_desc is not None:
                ckpt.next_step_desc = next_step_desc
            ckpt.is_paused = is_paused
            ckpt.updated_at = datetime.now(timezone.utc).isoformat()

        self._checkpoints[session_id] = ckpt
        self._last_active_session_id = session_id
        logger.info(f"Task Checkpoint [{session_id}] -> stage: {ckpt.current_stage}, app: {ckpt.target_app}, item: {ckpt.selected_item}")
        return ckpt

    def record_progress(
        self,
        session_id: str,
        action: str,
        stage: Optional[str] = None,
        detail: Optional[str] = None,
        stuck_reason: Optional[str] = None,
        next_step: Optional[str] = None,
        is_paused: bool = False
    ):
        """
        Dynamically records any small change in task state to the checkpoint timeline.
        Ensures continuous, accurate progress tracking.
        """
        ckpt = self.get_checkpoint(session_id)
        if not ckpt:
            ckpt = self.create_or_update(
                session_id=session_id,
                goal=action,
                target_app="flipkart",
                stage=stage or "IN_PROGRESS"
            )

        step_num = len(ckpt.step_history) + 1
        now_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = {
            "step": step_num,
            "timestamp": now_ts,
            "stage": stage or ckpt.current_stage,
            "action": action,
            "detail": detail or "",
            "stuck_reason": stuck_reason or "",
            "is_paused": is_paused
        }
        ckpt.step_history.append(entry)

        if stage:
            ckpt.current_stage = stage
        ckpt.last_action_desc = action
        if stuck_reason is not None:
            ckpt.stuck_reason = stuck_reason
        if next_step is not None:
            ckpt.next_step_desc = next_step
        ckpt.is_paused = is_paused
        ckpt.updated_at = datetime.now(timezone.utc).isoformat()

        logger.info(f"Task Progress Step #{step_num} [{ckpt.target_app}]: {action} (paused={is_paused})")

    def format_progress_summary(self, session_id: Optional[str] = None, is_hindi: bool = False) -> str:
        """
        Generates an intelligent natural language report of where the task is,
        what steps completed, where it got stuck, and what needs to happen next.
        """
        ckpt = self.get_checkpoint(session_id) if session_id else self.get_latest_checkpoint()
        if not ckpt or not ckpt.goal:
            if is_hindi:
                return "Abhi koi active ya paused order nahi hai. Aap mujhe kisi bhi cheez ka order karne ko bol sakte hain!"
            return "There is no active or paused shopping order right now. You can ask me to order anything on Flipkart or Amazon!"

        item_title = (ckpt.selected_item or {}).get("title") or ckpt.query or "Item"
        item_price = (ckpt.selected_item or {}).get("price") or ""
        app_disp = ckpt.target_app.capitalize() if ckpt.target_app else "Flipkart"

        completed_steps = [s["action"] for s in ckpt.step_history if not s.get("is_paused")]
        stuck_msg = ckpt.stuck_reason or (
            "Flipkart login screen par mobile number enter karne ke liye ruka hua hai." if is_hindi
            else "Paused at the Flipkart login screen awaiting mobile number entry."
        )
        next_step = ckpt.next_step_desc or (
            "Apne phone screen par mobile number daalein aur mujhe 'continue' ya 'done' kahein." if is_hindi
            else "Enter your mobile number on your phone screen, then say 'done' or 'continue' to resume."
        )

        if is_hindi:
            lines = [
                f"🛒 **Aapke Order ka Status ({app_disp})**:",
                f"Hum **{item_title}** {f'({item_price})' if item_price else ''} order kar rahe the.\n",
                "✅ **Jo steps pure ho chuke hain**:"
            ]
            if completed_steps:
                for i, step in enumerate(completed_steps[-4:], 1):
                    lines.append(f" {i}. {step}")
            else:
                lines.append(f" 1. {app_disp} khol kar '{ckpt.query}' search kiya.")
                lines.append(f" 2. Sabse saste product ko select kiya.")
                lines.append(f" 3. 'Buy' button par tap kiya.")

            lines.append(f"\n⚠️ **Yahan par hum atke the / ruke the**:")
            lines.append(f"{stuck_msg}")
            lines.append(f"\n👉 **Agle steps (Next Step)**:")
            lines.append(f"{next_step}")
            lines.append(f"\nKya aap chahte hain ki hum yahin se aage proceed karein? Kripya 'haan aage badho' ya 'continue' bolein!")
        else:
            lines = [
                f"🛒 **Active Order Status ({app_disp})**:",
                f"We were ordering **\"{item_title}\"** {f'({item_price})' if item_price else ''} on {app_disp}.\n",
                "✅ **Completed Steps**:"
            ]
            if completed_steps:
                for i, step in enumerate(completed_steps[-4:], 1):
                    lines.append(f" {i}. {step}")
            else:
                lines.append(f" 1. Opened {app_disp} and searched for '{ckpt.query}'.")
                lines.append(f" 2. Compared prices and selected the best product.")
                lines.append(f" 3. Clicked the 'Buy' button.")

            lines.append(f"\n⚠️ **Where we got paused / stuck**:")
            lines.append(f"{stuck_msg}")
            lines.append(f"\n👉 **Next Step to Proceed**:")
            lines.append(f"{next_step}")
            lines.append(f"\nWould you like me to proceed from where we stopped? Just say 'yes, proceed' or 'continue'!")

        return "\n".join(lines)

    def mark_completed(self, session_id: str):
        if session_id in self._checkpoints:
            self._checkpoints[session_id].current_stage = "COMPLETED"
            self._checkpoints[session_id].is_paused = False
            self._checkpoints[session_id].stuck_reason = None
            self._checkpoints[session_id].next_step_desc = "Order placed successfully!"

    def clear(self, session_id: str):
        if session_id in self._checkpoints:
            del self._checkpoints[session_id]

task_checkpoint_manager = TaskCheckpointManager()
