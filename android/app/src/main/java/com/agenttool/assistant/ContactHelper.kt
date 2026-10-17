package com.agenttool.assistant

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import android.util.Log
import androidx.core.content.ContextCompat

data class ContactInfo(
    val name: String,
    val number: String
)

object ContactHelper {
    private const val TAG = "ContactHelper"

    fun hasContactsPermission(context: Context): Boolean {
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.READ_CONTACTS
        ) == PackageManager.PERMISSION_GRANTED
    }

    /**
     * Search the device's native contact list by name.
     * Returns the matched ContactInfo if found, or null if no match occurs.
     */
    fun searchContactByName(context: Context, query: String): ContactInfo? {
        val cleanQuery = query.trim()
        if (cleanQuery.isEmpty()) return null

        if (!hasContactsPermission(context)) {
            Log.w(TAG, "Cannot search contacts: READ_CONTACTS permission not granted")
            return null
        }

        val resolver = context.contentResolver
        val uri = ContactsContract.CommonDataKinds.Phone.CONTENT_URI
        val projection = arrayOf(
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER
        )

        val selection = "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ?"
        val selectionArgs = arrayOf("%$cleanQuery%")

        var bestMatch: ContactInfo? = null
        var exactMatch: ContactInfo? = null

        try {
            resolver.query(
                uri,
                projection,
                selection,
                selectionArgs,
                "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} ASC"
            )?.use { cursor ->
                val nameIndex = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                val numberIndex = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)

                while (cursor.moveToNext()) {
                    val name = cursor.getString(nameIndex) ?: ""
                    val number = cursor.getString(numberIndex) ?: ""
                    val cleanNumber = number.replace("[^0-9+]".toRegex(), "")

                    if (cleanNumber.length >= 3) {
                        val contact = ContactInfo(name, cleanNumber)

                        // 1. Exact case-insensitive match has highest priority
                        if (name.equals(cleanQuery, ignoreCase = true)) {
                            exactMatch = contact
                            break
                        }

                        // 2. Starts with query
                        if (bestMatch == null && name.startsWith(cleanQuery, ignoreCase = true)) {
                            bestMatch = contact
                        } else if (bestMatch == null) {
                            bestMatch = contact
                        }
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error querying contacts for '$cleanQuery': ${e.message}", e)
        }

        return exactMatch ?: bestMatch
    }

    /**
     * Retrieve all phone contacts from device storage.
     */
    fun getAllContacts(context: Context, limit: Int = 1000): List<ContactInfo> {
        val contacts = mutableListOf<ContactInfo>()
        if (!hasContactsPermission(context)) {
            Log.w(TAG, "Cannot read contacts: READ_CONTACTS permission not granted")
            return contacts
        }

        val resolver = context.contentResolver
        val uri = ContactsContract.CommonDataKinds.Phone.CONTENT_URI
        val projection = arrayOf(
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER
        )

        try {
            resolver.query(
                uri,
                projection,
                null,
                null,
                "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} ASC"
            )?.use { cursor ->
                val nameIndex = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                val numberIndex = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                val seenNumbers = mutableSetOf<String>()

                while (cursor.moveToNext() && contacts.size < limit) {
                    val name = cursor.getString(nameIndex) ?: "Unknown"
                    val number = cursor.getString(numberIndex) ?: ""
                    val cleanNumber = number.replace("[^0-9+]".toRegex(), "")

                    if (cleanNumber.length >= 3 && seenNumbers.add(cleanNumber)) {
                        contacts.add(ContactInfo(name, cleanNumber))
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error fetching all contacts: ${e.message}", e)
        }

        return contacts
    }
}
