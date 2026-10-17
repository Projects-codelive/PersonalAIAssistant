package com.agenttool.assistant

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class DeviceWebSocketClient(
    private val context: Context,
    private val serverUrl: String,
    private val listener: EventListener
) {
    interface EventListener {
        fun onConnected()
        fun onDisconnected(reason: String)
        fun onLog(message: String)
    }

    companion object {
        private const val TAG = "DeviceWebSocket"
        @Volatile
        private var activeClient: DeviceWebSocketClient? = null

        fun getActiveClient(): DeviceWebSocketClient? = activeClient
    }

    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .connectTimeout(10, TimeUnit.SECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    fun connect() {
        activeClient = this
        listener.onLog("Connecting to: $serverUrl")

        val request = Request.Builder().url(serverUrl).build()
        this.webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                mainHandler.post {
                    listener.onConnected()
                    listener.onLog("✓ Connected to Gemma Backend!")
                }
                // Send registration packet
                val reg = JSONObject().apply {
                    put("type", "device_register")
                    put("device_id", "agent-mobile")
                    put("platform", "android")
                    put("has_accessibility", AgentAccessibilityService.getInstance() != null)
                    put("has_contacts_permission", ContactHelper.hasContactsPermission(context))
                }
                webSocket.send(reg.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                Log.d(TAG, "Incoming instruction: $text")
                mainHandler.post {
                    listener.onLog("Incoming: $text")
                    handleCommand(text)
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                mainHandler.post {
                    listener.onDisconnected("Closing: $reason")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                mainHandler.post {
                    listener.onDisconnected("Failed: ${t.localizedMessage}")
                    listener.onLog("Error: ${t.localizedMessage}")
                }
            }
        })
    }

    fun disconnect() {
        webSocket?.close(1000, "User disconnected")
        webSocket = null
        if (activeClient == this) activeClient = null
    }

    fun sendMessage(json: JSONObject) {
        webSocket?.send(json.toString())
    }

    private fun handleCommand(message: String) {
        try {
            val json = JSONObject(message)
            val action = json.optString("action", json.optString("type", ""))
            val service = AgentAccessibilityService.getInstance()

            when (action) {
                "read_screen", "dump_screen" -> {
                    if (service == null) {
                        sendError("Accessibility service is not enabled on phone.")
                        return
                    }
                    val hierarchy = service.dumpScreenHierarchy()
                    val response = JSONObject().apply {
                        put("type", "screen_state")
                        put("hierarchy", hierarchy)
                    }
                    sendMessage(response)
                    listener.onLog("✓ Sent screen hierarchy (${hierarchy.optJSONArray("interactive_elements")?.length() ?: 0} elements)")
                }

                "tap", "tap_coordinates" -> {
                    val x = json.optDouble("x", 0.0).toFloat()
                    val y = json.optDouble("y", 0.0).toFloat()
                    if (service != null && x > 0 && y > 0) {
                        service.tapCoordinates(x, y) { success ->
                            listener.onLog(if (success) "✓ Tapped ($x, $y)" else "✗ Tap failed")
                            sendActionResult("tap", success)
                        }
                    } else {
                        sendError("Invalid coordinates ($x, $y)")
                    }
                }

                "tap_text", "click_text" -> {
                    val text = json.optString("text", "")
                    if (service != null && text.isNotEmpty()) {
                        val ok = service.clickElementByText(text)
                        listener.onLog(if (ok) "✓ Clicked text: '$text'" else "✗ Text not found: '$text'")
                        sendActionResult("tap_text", ok)
                    } else {
                        sendError("Service unavailable or text empty")
                    }
                }

                "input_text", "type" -> {
                    val text = json.optString("text", "")
                    if (service != null) {
                        val ok = service.inputText(text)
                        listener.onLog(if (ok) "✓ Typed: '$text'" else "✗ Input text failed")
                        sendActionResult("input_text", ok)
                    }
                }

                "call", "make_call", "dial", "open_dialer", "TRIGGER_PHONE_CALL" -> {
                    val dataObj = json.optJSONObject("data")
                    val rawTarget = dataObj?.optString("phone_number")
                        ?: json.optString("phone", json.optString("number", json.optString("target", "")))
                    val contactName = dataObj?.optString("contact_name")
                        ?: json.optString("contact_name", json.optString("name", ""))

                    val target = rawTarget.trim()
                    val digitsOnly = target.replace("[^0-9]".toRegex(), "")

                    // If target is a contact name or digits are fewer than 3, search contact list
                    if (digitsOnly.length < 3) {
                        val query = if (contactName.isNotEmpty()) contactName else target
                        val matched = ContactHelper.searchContactByName(context, query)
                        if (matched != null) {
                            listener.onLog("✓ Found contact '${matched.name}': ${matched.number}")
                            // Use mobile dialer to dial number and make call
                            val ok = AgentCallService.openMobileDialer(context, matched.number)
                            listener.onLog(if (ok) "✓ Opened mobile dialer for ${matched.name} (${matched.number})" else "✗ Failed to open dialer")
                            sendActionResult("call", ok)
                        } else {
                            val msg = "Could not find contact"
                            listener.onLog("✗ $msg for '$query'")
                            sendError("$msg for '$query'")
                        }
                    } else {
                        // Phone number provided -> use mobile dialer to dial the number and make the call
                        val ok = AgentCallService.openMobileDialer(context, target)
                        listener.onLog(if (ok) "✓ Opened mobile dialer for: $target" else "✗ Failed to open dialer")
                        sendActionResult("call", ok)
                    }
                }

                "home", "go_home" -> {
                    val ok = service?.navigateHome() ?: false
                    sendActionResult("go_home", ok)
                }

                "back", "go_back" -> {
                    val ok = service?.navigateBack() ?: false
                    sendActionResult("go_back", ok)
                }

                else -> {
                    Log.w(TAG, "Unhandled action: $action")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to handle command", e)
            sendError(e.message ?: "Parsing error")
        }
    }

    private fun sendActionResult(action: String, success: Boolean) {
        val res = JSONObject().apply {
            put("type", "action_result")
            put("action", action)
            put("success", success)
        }
        sendMessage(res)
    }

    private fun sendError(error: String) {
        val err = JSONObject().apply {
            put("type", "error")
            put("message", error)
        }
        sendMessage(err)
    }
}
