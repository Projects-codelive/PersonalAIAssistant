package com.agenttool.assistant

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.IBinder
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat

class FloatingAgentBubbleService : Service() {

    private var windowManager: WindowManager? = null
    private var bubbleView: View? = null

    companion object {
        private const val CHANNEL_ID = "AgentTool_Floating_Bubble"
        private const val NOTIF_ID = 1001
        var isRunning = false
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        isRunning = true
        startForegroundServiceNotification()
        setupFloatingBubble()
    }

    private fun startForegroundServiceNotification() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "AgentTool Assistant",
                NotificationManager.IMPORTANCE_LOW
            )
            manager.createNotificationChannel(channel)
        }

        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AgentTool Active")
            .setContentText("Autonomous Screen Agent is listening & observing")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .build()

        startForeground(NOTIF_ID, notification)
    }

    private fun setupFloatingBubble() {
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        val layoutParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else
                WindowManager.LayoutParams.TYPE_PHONE,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 50
            y = 200
        }

        // Create sleek circular bubble view
        val frame = FrameLayout(this).apply {
            val bg = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.parseColor("#6366F1"))
                setStroke(4, Color.parseColor("#FFFFFF"))
            }
            background = bg
            setPadding(24, 24, 24, 24)
        }

        val text = TextView(this).apply {
            text = "🤖"
            textSize = 24f
            gravity = Gravity.CENTER
        }
        frame.addView(text)

        // Draggable touch listener
        frame.setOnTouchListener(object : View.OnTouchListener {
            private var initialX = 0
            private var initialY = 0
            private var initialTouchX = 0f
            private var initialTouchY = 0f
            private var isClick = false

            override fun onTouch(v: View?, event: MotionEvent?): Boolean {
                event ?: return false
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> {
                        initialX = layoutParams.x
                        initialY = layoutParams.y
                        initialTouchX = event.rawX
                        initialTouchY = event.rawY
                        isClick = true
                        return true
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = event.rawX - initialTouchX
                        val dy = event.rawY - initialTouchY
                        if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
                            isClick = false
                        }
                        layoutParams.x = initialX + dx.toInt()
                        layoutParams.y = initialY + dy.toInt()
                        windowManager?.updateViewLayout(frame, layoutParams)
                        return true
                    }
                    MotionEvent.ACTION_UP -> {
                        if (isClick) {
                            onBubbleClicked()
                        }
                        return true
                    }
                }
                return false
            }
        })

        bubbleView = frame
        try {
            windowManager?.addView(bubbleView, layoutParams)
        } catch (e: Exception) {
            Toast.makeText(this, "Cannot show bubble: Overlay permission needed", Toast.LENGTH_SHORT).show()
        }
    }

    private fun onBubbleClicked() {
        val service = AgentAccessibilityService.getInstance()
        if (service != null) {
            val hierarchy = service.dumpScreenHierarchy()
            val count = hierarchy.optJSONArray("interactive_elements")?.length() ?: 0
            Toast.makeText(this, "Gemma scanning: $count screen items detected", Toast.LENGTH_SHORT).show()

            // Transmit to active WebSocket if connected
            DeviceWebSocketClient.getActiveClient()?.let { client ->
                val packet = org.json.JSONObject().apply {
                    put("type", "screen_state")
                    put("hierarchy", hierarchy)
                    put("triggered_by", "floating_bubble")
                }
                client.sendMessage(packet)
            }
        } else {
            Toast.makeText(this, "Please enable AgentTool Accessibility Service first", Toast.LENGTH_LONG).show()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        isRunning = false
        bubbleView?.let {
            try {
                windowManager?.removeView(it)
            } catch (e: Exception) {}
        }
    }
}
