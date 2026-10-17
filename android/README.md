# AgentTool - Android Native Integration Guide

This directory contains the native Android architecture that enables **Zero-Touch Phone Calling** and **Autonomous Screen Navigation & Tracking**.

---

## 1. Zero-Touch Phone Calling (`AgentCallService.kt`)

In Android, there are two distinct ways to trigger phone calls from code:

| Intent Action | Permission Required | User Interaction | Use Case |
|---|---|---|---|
| `Intent.ACTION_CALL` | `android.permission.CALL_PHONE` | **Zero-Touch (Automated)**: Phone places the call immediately without the user having to tap the green dial button. | Autonomous Agent |
| `Intent.ACTION_DIAL` | None | **Interactive**: Opens dialer with the number pre-filled, user must tap dial button. | Safe fallback / Web PWA |

### Implementation Snippet:
```kotlin
val callIntent = Intent(Intent.ACTION_CALL).apply {
    data = Uri.parse("tel:$phoneNumber")
    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
}
context.startActivity(callIntent)
```

---

## 2. Autonomous Screen Tracking & Navigation (`AgentAccessibilityService.kt`)

### How Android Screen Navigation Works:
1. **Accessibility Hierarchy Dump**:
   - The service inspects `rootInActiveWindow`.
   - It crawls `AccessibilityNodeInfo` tree to find all buttons, input fields, text labels, and their exact coordinates (`Rect` bounds).
   - This tree is converted to JSON and sent to the Python Gemma agent via WebSocket (`/ws/device`).
2. **Intent to Action Mapping**:
   - When Gemma decides to click a button (e.g., "Tap Call" or "Open Settings"), the agent executes `tap_element`.
   - The backend pushes `SCREEN_TAP` via WebSocket to the device.
3. **Action Execution**:
   - The service either executes:
     - `node.performAction(AccessibilityNodeInfo.ACTION_CLICK)` (Accessibility event click)
     - OR `dispatchGesture()` (dispatches a simulated physical touch at (X, Y) coordinates, works on canvas/games).
4. **Global Navigation**:
   - `performGlobalAction(GLOBAL_ACTION_HOME)` returns to home launcher.
   - `performGlobalAction(GLOBAL_ACTION_BACK)` goes back.
