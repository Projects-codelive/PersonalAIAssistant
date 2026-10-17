package com.agenttool.assistant

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * AgentAccessibilityService enables autonomous screen tracking and device navigation for Gemma.
 */
class AgentAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "AgentAccessibility"
        @Volatile
        private var instance: AgentAccessibilityService? = null

        fun getInstance(): AgentAccessibilityService? = instance
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "AgentAccessibilityService connected and ready for autonomous screen control.")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Active window changes trigger event logging
        event?.let {
            if (it.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                Log.d(TAG, "Window changed: ${it.packageName} / ${it.className}")
            }
        }
    }

    override fun onInterrupt() {
        Log.w(TAG, "AgentAccessibilityService interrupted.")
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
    }

    /**
     * Dumps the entire active screen hierarchy into a structured JSON for the Gemma agent.
     */
    fun dumpScreenHierarchy(): JSONObject {
        val root = rootInActiveWindow ?: return JSONObject().put("error", "No active window")
        val json = JSONObject()
        val elementsArray = JSONArray()

        json.put("package", root.packageName?.toString() ?: "unknown")

        var index = 0
        fun crawlNode(node: AccessibilityNodeInfo?) {
            if (node == null) return

            val bounds = Rect()
            node.getBoundsInScreen(bounds)

            val text = node.text?.toString()?.trim() ?: ""
            val desc = node.contentDescription?.toString()?.trim() ?: ""
            val viewId = node.viewIdResourceName ?: ""
            val isClickable = node.isClickable
            val isEditable = node.isEditable

            // Include all interactive or textual elements
            if (text.isNotEmpty() || desc.isNotEmpty() || isClickable || isEditable) {
                index++
                val element = JSONObject().apply {
                    put("index", index)
                    put("id", viewId)
                    put("class", node.className?.toString() ?: "")
                    put("text", text)
                    put("desc", desc)
                    put("clickable", isClickable)
                    put("editable", isEditable)
                    put("bounds", JSONArray(listOf(bounds.left, bounds.top, bounds.right, bounds.bottom)))
                    put("center_x", bounds.centerX())
                    put("center_y", bounds.centerY())
                }
                elementsArray.put(element)
            }

            for (i in 0 until node.childCount) {
                crawlNode(node.getChild(i))
            }
        }

        crawlNode(root)
        json.put("interactive_elements", elementsArray)
        return json
    }

    /**
     * Click an element by text or content description.
     */
    fun clickElementByText(targetText: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val nodes = root.findAccessibilityNodeInfosByText(targetText)
        if (!nodes.isNullOrEmpty()) {
            for (node in nodes) {
                if (node.isClickable && node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                    Log.i(TAG, "Successfully clicked node with text: $targetText")
                    return true
                }
                // Try parent if child is not directly clickable
                var parent = node.parent
                while (parent != null) {
                    if (parent.isClickable && parent.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                        Log.i(TAG, "Successfully clicked parent of text: $targetText")
                        return true
                    }
                    parent = parent.parent
                }
            }
        }
        return false
    }

    /**
     * Input text into currently focused or editable field.
     */
    fun inputText(text: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (focused != null && focused.isEditable) {
            val arguments = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            return focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
        }
        return false
    }

    /**
     * Tap anywhere on screen using exact (X, Y) coordinates.
     */
    fun tapCoordinates(x: Float, y: Float, callback: ((Boolean) -> Unit)? = null) {
        val path = Path().apply {
            moveTo(x, y)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, 60)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()

        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                Log.i(TAG, "Tap at ($x, $y) succeeded.")
                callback?.invoke(true)
            }

            override fun onCancelled(gestureDescription: GestureDescription?) {
                Log.w(TAG, "Tap at ($x, $y) cancelled.")
                callback?.invoke(false)
            }
        }, null)
    }

    fun navigateBack(): Boolean = performGlobalAction(GLOBAL_ACTION_BACK)
    fun navigateHome(): Boolean = performGlobalAction(GLOBAL_ACTION_HOME)
    fun openRecents(): Boolean = performGlobalAction(GLOBAL_ACTION_RECENTS)

    /**
     * Scroll screen up or down using accessibility gesture description.
     */
    fun scroll(direction: String = "down", callback: ((Boolean) -> Unit)? = null) {
        val displayMetrics = resources.displayMetrics
        val width = displayMetrics.widthPixels.toFloat()
        val height = displayMetrics.heightPixels.toFloat()
        val centerX = width / 2f
        val centerY = height / 2f
        val scrollDistance = height * 0.4f

        val startY: Float
        val endY: Float
        if (direction.lowercase() == "down") {
            startY = centerY + scrollDistance / 2f
            endY = centerY - scrollDistance / 2f
        } else {
            startY = centerY - scrollDistance / 2f
            endY = centerY + scrollDistance / 2f
        }

        val path = Path().apply {
            moveTo(centerX, startY)
            lineTo(centerX, endY)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, 300)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()

        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                Log.i(TAG, "Scroll $direction completed.")
                callback?.invoke(true)
            }
            override fun onCancelled(gestureDescription: GestureDescription?) {
                Log.w(TAG, "Scroll $direction cancelled.")
                callback?.invoke(false)
            }
        }, null)
    }

    /**
     * Press IME Search / Enter action on currently focused editable input.
     */
    fun pressEnterOrSearch(): Boolean {
        val root = rootInActiveWindow ?: return false
        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (focused != null) {
            val success = focused.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION)
            return success
        }
        return false
    }
}

