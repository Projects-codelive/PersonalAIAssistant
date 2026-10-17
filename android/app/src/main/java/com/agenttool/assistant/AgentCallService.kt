package com.agenttool.assistant

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.util.Log
import androidx.core.content.ContextCompat

object AgentCallService {
    private const val TAG = "AgentCallService"

    /**
     * Opens the native mobile dialer with the phone number dialed so the user can make the call.
     * Uses Intent.ACTION_DIAL. Works on any valid phone number without requiring special CALL_PHONE permission.
     */
    fun openMobileDialer(context: Context, rawNumber: String): Boolean {
        val cleanNumber = rawNumber.replace("[^0-9+]".toRegex(), "")
        if (cleanNumber.length < 3) {
            Log.e(TAG, "Cannot open dialer: Invalid phone number '$rawNumber'")
            return false
        }

        return try {
            val intent = Intent(Intent.ACTION_DIAL, Uri.parse("tel:$cleanNumber")).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
            context.startActivity(intent)
            Log.i(TAG, "Opened mobile dialer for: $cleanNumber")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open mobile dialer for $cleanNumber: ${e.message}", e)
            false
        }
    }

    /**
     * Directly places a phone call using ACTION_CALL if CALL_PHONE permission is granted,
     * otherwise falls back to opening the mobile dialer via ACTION_DIAL.
     */
    fun makeDirectCall(context: Context, rawNumber: String): Boolean {
        val cleanNumber = rawNumber.replace("[^0-9+]".toRegex(), "")
        if (cleanNumber.length < 3) {
            Log.e(TAG, "Cannot place call: Invalid phone number '$rawNumber'")
            return false
        }

        return try {
            val hasPermission = ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.CALL_PHONE
            ) == PackageManager.PERMISSION_GRANTED

            val intent = if (hasPermission) {
                Intent(Intent.ACTION_CALL, Uri.parse("tel:$cleanNumber")).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                }
            } else {
                Intent(Intent.ACTION_DIAL, Uri.parse("tel:$cleanNumber")).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                }
            }

            context.startActivity(intent)
            Log.i(TAG, "Initiated call to $cleanNumber (direct call: $hasPermission)")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initiate call to $cleanNumber", e)
            false
        }
    }
}
