package com.agenttool.assistant

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.TextUtils
import android.view.accessibility.AccessibilityManager
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import android.view.View
import android.widget.ArrayAdapter
import android.widget.LinearLayout
import android.widget.Spinner
import androidx.core.content.ContextCompat
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity(), DeviceWebSocketClient.EventListener {

    private lateinit var tvConnectionStatus: TextView
    private lateinit var etServerUrl: EditText
    private lateinit var btnConnect: Button
    private lateinit var tvAccessibilityStatus: TextView
    private lateinit var btnEnableAccessibility: Button
    private lateinit var tvOverlayStatus: TextView
    private lateinit var btnEnableOverlay: Button
    private lateinit var tvPhoneStatus: TextView
    private lateinit var btnGrantPermissions: Button
    private lateinit var etTargetInput: EditText
    private lateinit var btnDialMobile: Button
    private lateinit var btnSearchContact: Button
    private lateinit var btnToggleFloatingBubble: Button
    private lateinit var btnTestDumpScreen: Button
    private lateinit var tvLogs: TextView

    // Knowledge Search & Gemma Reframing Views
    private lateinit var spnKnowledgeSource: Spinner
    private lateinit var etKnowledgeQuery: EditText
    private lateinit var btnSearchKnowledge: Button
    private lateinit var layoutSearchResult: LinearLayout
    private lateinit var tvToolSiteStatus: TextView
    private lateinit var tvInfoTakenPreview: TextView
    private lateinit var tvReframedAnswer: TextView

    private var wsClient: DeviceWebSocketClient? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        updateStatusBadges()
        val allGranted = permissions.values.all { it }
        log(if (allGranted) "✓ Call, Contacts & Audio permissions granted!" else "✗ Some permissions denied")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        initViews()
        setupListeners()
    }

    override fun onResume() {
        super.onResume()
        updateStatusBadges()
    }

    private fun initViews() {
        tvConnectionStatus = findViewById(R.id.tvConnectionStatus)
        etServerUrl = findViewById(R.id.etServerUrl)
        btnConnect = findViewById(R.id.btnConnect)
        tvAccessibilityStatus = findViewById(R.id.tvAccessibilityStatus)
        btnEnableAccessibility = findViewById(R.id.btnEnableAccessibility)
        tvOverlayStatus = findViewById(R.id.tvOverlayStatus)
        btnEnableOverlay = findViewById(R.id.btnEnableOverlay)
        tvPhoneStatus = findViewById(R.id.tvPhoneStatus)
        btnGrantPermissions = findViewById(R.id.btnGrantPermissions)
        etTargetInput = findViewById(R.id.etTargetInput)
        btnDialMobile = findViewById(R.id.btnDialMobile)
        btnSearchContact = findViewById(R.id.btnSearchContact)
        btnToggleFloatingBubble = findViewById(R.id.btnToggleFloatingBubble)
        btnTestDumpScreen = findViewById(R.id.btnTestDumpScreen)
        tvLogs = findViewById(R.id.tvLogs)

        // Knowledge Search Views
        spnKnowledgeSource = findViewById(R.id.spnKnowledgeSource)
        etKnowledgeQuery = findViewById(R.id.etKnowledgeQuery)
        btnSearchKnowledge = findViewById(R.id.btnSearchKnowledge)
        layoutSearchResult = findViewById(R.id.layoutSearchResult)
        tvToolSiteStatus = findViewById(R.id.tvToolSiteStatus)
        tvInfoTakenPreview = findViewById(R.id.tvInfoTakenPreview)
        tvReframedAnswer = findViewById(R.id.tvReframedAnswer)

        val sources = listOf(
            "Auto-Detect Knowledge Tool",
            "Wikipedia (en.wikipedia.org)",
            "DuckDuckGo Web Search (duckduckgo.com)",
            "arXiv Papers (arxiv.org)",
            "PubMed Medical (pubmed.ncbi.nlm.nih.gov)"
        )
        val spinnerAdapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, sources)
        spnKnowledgeSource.adapter = spinnerAdapter

        // Default to USB localhost address (works via adb reverse tcp:8000 tcp:8000)
        etServerUrl.setText("ws://127.0.0.1:8000/ws/device/agent-mobile")
    }

    private fun setupListeners() {
        btnConnect.setOnClickListener {
            val url = etServerUrl.text.toString().trim()
            if (url.isEmpty()) {
                Toast.makeText(this, "Please enter a valid WebSocket URL", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            if (wsClient != null) {
                wsClient?.disconnect()
                wsClient = null
                btnConnect.text = "Connect to Gemma Agent"
                btnConnect.setBackgroundColor(Color.parseColor("#6366F1"))
                tvConnectionStatus.text = "● Disconnected"
                tvConnectionStatus.setTextColor(Color.parseColor("#EF4444"))
            } else {
                wsClient = DeviceWebSocketClient(this, url, this)
                wsClient?.connect()
                btnConnect.text = "Disconnect"
                btnConnect.setBackgroundColor(Color.parseColor("#EF4444"))
            }
        }

        btnEnableAccessibility.setOnClickListener {
            val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            startActivity(intent)
            Toast.makeText(this, "Find 'AgentTool Screen Assistant' and turn it ON", Toast.LENGTH_LONG).show()
        }

        btnEnableOverlay.setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val intent = Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName")
                )
                startActivity(intent)
            }
        }

        btnGrantPermissions.setOnClickListener {
            val permissions = mutableListOf(
                Manifest.permission.CALL_PHONE,
                Manifest.permission.READ_PHONE_STATE,
                Manifest.permission.READ_CONTACTS,
                Manifest.permission.RECORD_AUDIO
            )
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                permissions.add(Manifest.permission.POST_NOTIFICATIONS)
            }
            permissionLauncher.launch(permissions.toTypedArray())
        }

        // Open Mobile Dialer for any phone number or contact
        btnDialMobile.setOnClickListener {
            val target = etTargetInput.text.toString().trim()
            if (target.isEmpty()) {
                Toast.makeText(this, "Please enter a number or contact name", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val digitsOnly = target.replace("[^0-9]".toRegex(), "")
            if (digitsOnly.length >= 3) {
                // Direct number -> open mobile dialer with this number
                log("📞 Opening mobile dialer for number: $target")
                val success = AgentCallService.openMobileDialer(this, target)
                if (success) {
                    log("✓ Mobile dialer opened with: $target")
                } else {
                    log("✗ Failed to open mobile dialer")
                }
            } else {
                // Contact name provided -> search contact list first
                log("🔍 Searching contact list for: '$target'...")
                val matched = ContactHelper.searchContactByName(this, target)
                if (matched != null) {
                    log("✓ Found contact: ${matched.name} (${matched.number})")
                    log("📞 Opening mobile dialer for ${matched.name} (${matched.number})...")
                    val success = AgentCallService.openMobileDialer(this, matched.number)
                    if (success) {
                        log("✓ Mobile dialer opened for ${matched.name}")
                    }
                } else {
                    log("✗ Could not find contact for '$target'")
                    Toast.makeText(this, "Could not find contact", Toast.LENGTH_SHORT).show()
                }
            }
        }

        // Search Contact in Phone Contact List
        btnSearchContact.setOnClickListener {
            val query = etTargetInput.text.toString().trim()
            if (query.isEmpty()) {
                Toast.makeText(this, "Please enter a contact name to search", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            if (!ContactHelper.hasContactsPermission(this)) {
                Toast.makeText(this, "Please grant Contacts permission first", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            log("🔍 Searching contacts for: '$query'...")
            val contact = ContactHelper.searchContactByName(this, query)
            if (contact != null) {
                log("✓ Found contact: ${contact.name} (${contact.number})")
                etTargetInput.setText(contact.number)
                Toast.makeText(this, "Found: ${contact.name} (${contact.number})", Toast.LENGTH_SHORT).show()
            } else {
                log("✗ Could not find contact for '$query'")
                Toast.makeText(this, "Could not find contact", Toast.LENGTH_SHORT).show()
            }
        }

        btnToggleFloatingBubble.setOnClickListener {
            if (FloatingAgentBubbleService.isRunning) {
                stopService(Intent(this, FloatingAgentBubbleService::class.java))
                btnToggleFloatingBubble.text = "🔮 Start Floating Agent Bubble"
                log("Stopped Floating Agent Bubble")
            } else {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
                    Toast.makeText(this, "Enable Overlay permission first", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                val intent = Intent(this, FloatingAgentBubbleService::class.java)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(intent)
                } else {
                    startService(intent)
                }
                btnToggleFloatingBubble.text = "⏹ Stop Floating Agent Bubble"
                log("✓ Floating Agent Bubble started! Floats on all apps.")
            }
        }

        btnTestDumpScreen.setOnClickListener {
            val service = AgentAccessibilityService.getInstance()
            if (service == null) {
                log("✗ Accessibility service is not running! Please enable it first.")
            } else {
                val data = service.dumpScreenHierarchy()
                log("=== Active Screen UI Dump ===")
                log(data.toString(2))
            }
        }

        // Knowledge Search Tool Trigger
        btnSearchKnowledge.setOnClickListener {
            val query = etKnowledgeQuery.text.toString().trim()
            if (query.isEmpty()) {
                Toast.makeText(this, "Please enter a topic or question to search", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val sourceKey = when (spnKnowledgeSource.selectedItemPosition) {
                1 -> "wikipedia"
                2 -> "duckduckgo"
                3 -> "arxiv"
                4 -> "pubmed"
                else -> "auto"
            }

            executeKnowledgeSearch(query, sourceKey)
        }
    }

    private fun executeKnowledgeSearch(query: String, sourceKey: String) {
        val wsUrl = etServerUrl.text.toString().trim()
        val httpBase = if (wsUrl.startsWith("ws://")) {
            wsUrl.replace("ws://", "http://").substringBefore("/ws/")
        } else if (wsUrl.startsWith("wss://")) {
            wsUrl.replace("wss://", "https://").substringBefore("/ws/")
        } else {
            "http://127.0.0.1:8000"
        }
        val endpoint = "$httpBase/api/search"
        // Wi-Fi connections authenticate with ?token=... on the WebSocket URL; reuse it for HTTP calls
        val apiToken = Uri.parse(wsUrl).getQueryParameter("token").orEmpty()

        layoutSearchResult.visibility = View.VISIBLE
        tvToolSiteStatus.text = "🛠️ Calling knowledge tool (Source: $sourceKey)..."
        tvInfoTakenPreview.text = "📄 Querying knowledge repository for '$query'..."
        tvReframedAnswer.text = "⏳ Local Gemma is analyzing and reframing the response..."
        btnSearchKnowledge.isEnabled = false

        log("🔎 [SEARCH START]: Query='$query', Source='$sourceKey' -> Calling $endpoint")

        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build()

        val jsonBody = JSONObject().apply {
            put("query", query)
            put("source", sourceKey)
        }

        val requestBody = jsonBody.toString().toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url(endpoint)
            .post(requestBody)
            .apply { if (apiToken.isNotEmpty()) header("X-AgentTool-Token", apiToken) }
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    btnSearchKnowledge.isEnabled = true
                    tvToolSiteStatus.text = "❌ Search Tool Call Failed"
                    tvReframedAnswer.text = "Error: ${e.localizedMessage}. Ensure backend is running."
                    log("✗ Knowledge search failed: ${e.localizedMessage}")
                }
            }

            override fun onResponse(call: Call, response: Response) {
                val respStr = response.body?.string() ?: "{}"
                runOnUiThread {
                    btnSearchKnowledge.isEnabled = true
                    try {
                        val resJson = JSONObject(respStr)
                        val site = resJson.optString("site", "External Knowledge Site")
                        val reply = resJson.optString("reply", "No reply generated.")
                        val toolCalls = resJson.optJSONArray("tool_calls")
                        val firstTool = if (toolCalls != null && toolCalls.length() > 0) toolCalls.getJSONObject(0) else null
                        val toolName = firstTool?.optString("tool") ?: "search_tool"
                        val infoTaken = firstTool?.optString("info_taken") ?: resJson.optString("info_taken", "")

                        tvToolSiteStatus.text = "⚡ Tool: $toolName  |  📍 Site: $site"
                        tvInfoTakenPreview.text = "📄 Info Taken (${infoTaken.length} chars):\n${infoTaken.take(300)}..."
                        tvReframedAnswer.text = reply

                        log("✓ [SEARCH SUCCESS]: Tool: $toolName on $site")
                        log("✓ [GEMMA REFRAMED]: ${reply.take(120)}...")
                    } catch (e: Exception) {
                        tvReframedAnswer.text = "Failed to parse response: ${e.message}\nRaw: $respStr"
                        log("✗ Parse error: ${e.message}")
                    }
                }
            }
        })
    }

    private fun updateStatusBadges() {
        val isA11y = isAccessibilityServiceEnabled(this, AgentAccessibilityService::class.java)
        tvAccessibilityStatus.text = if (isA11y) "1. Accessibility Service: ✓ Active" else "1. Accessibility Service: ✗ Disabled"
        tvAccessibilityStatus.setTextColor(if (isA11y) Color.parseColor("#10B981") else Color.parseColor("#EF4444"))
        btnEnableAccessibility.isEnabled = !isA11y

        val canDraw = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) Settings.canDrawOverlays(this) else true
        tvOverlayStatus.text = if (canDraw) "2. Floating Overlay: ✓ Granted" else "2. Floating Overlay: ✗ Missing"
        tvOverlayStatus.setTextColor(if (canDraw) Color.parseColor("#10B981") else Color.parseColor("#EF4444"))
        btnEnableOverlay.isEnabled = !canDraw

        val hasCall = ContextCompat.checkSelfPermission(this, Manifest.permission.CALL_PHONE) == PackageManager.PERMISSION_GRANTED
        val hasContacts = ContactHelper.hasContactsPermission(this)
        val hasMic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        val permsOk = hasCall && hasContacts && hasMic

        val missing = mutableListOf<String>()
        if (!hasCall) missing.add("Call")
        if (!hasContacts) missing.add("Contacts")
        if (!hasMic) missing.add("Mic")

        tvPhoneStatus.text = if (permsOk) "3. Phone, Contacts & Audio: ✓ Granted" else "3. Missing: ${missing.joinToString(", ")}"
        tvPhoneStatus.setTextColor(if (permsOk) Color.parseColor("#10B981") else Color.parseColor("#EF4444"))
        btnGrantPermissions.isEnabled = !permsOk
    }

    private fun isAccessibilityServiceEnabled(context: Context, service: Class<*>): Boolean {
        val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager ?: return false
        val enabledServices = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false

        val colonSplitter = TextUtils.SimpleStringSplitter(':')
        colonSplitter.setString(enabledServices)
        val target = "${context.packageName}/${service.name}"

        while (colonSplitter.hasNext()) {
            val componentName = colonSplitter.next()
            if (componentName.equals(target, ignoreCase = true) || componentName.contains(service.simpleName)) {
                return true
            }
        }
        return false
    }

    private fun log(message: String) {
        val current = tvLogs.text.toString()
        val updated = "$message\n$current".take(4000)
        tvLogs.text = updated
    }

    override fun onConnected() {
        tvConnectionStatus.text = "● Connected to Gemma"
        tvConnectionStatus.setTextColor(Color.parseColor("#10B981"))
        log("✓ WebSocket link active!")
    }

    override fun onDisconnected(reason: String) {
        tvConnectionStatus.text = "● Disconnected"
        tvConnectionStatus.setTextColor(Color.parseColor("#EF4444"))
        btnConnect.text = "Connect to Gemma Agent"
        btnConnect.setBackgroundColor(Color.parseColor("#6366F1"))
        log("Disconnected: $reason")
    }

    override fun onLog(message: String) {
        log(message)
    }
}
