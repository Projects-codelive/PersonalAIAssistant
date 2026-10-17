import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

logger = logging.getLogger(__name__)

class Database:
    client: Optional[AsyncIOMotorClient] = None
    is_connected: bool = False
    # Motor clients are bound to one event loop; the agent worker thread runs its own loop
    # (app/agent/agent_worker.py), so each loop lazily gets its own client to the same database.
    _loop_databases: Dict[Any, Any] = {}
    _loop_clients: List[AsyncIOMotorClient] = []

    @property
    def db(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        database = self._loop_databases.get(loop)
        if database is None and self.is_connected and loop is not None:
            client = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=2500, connectTimeoutMS=2500)
            self._loop_clients.append(client)
            database = self._loop_databases[loop] = client[settings.MONGODB_DB_NAME]
        return database

    @db.setter
    def db(self, database):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._loop_databases[loop] = database
    
    # Fallback in-memory storage if MongoDB is momentarily unreachable
    _memory_contacts: List[Dict[str, Any]] = [
        {"name": "Alice Johnson", "phone": "+1-555-019-2834", "notes": "Work Colleague", "created_at": datetime.now(timezone.utc)},
        {"name": "Mom", "phone": "+1-555-014-9988", "notes": "Family", "created_at": datetime.now(timezone.utc)},
        {"name": "Doctor Clinic", "phone": "+1-555-012-3456", "notes": "Healthcare", "created_at": datetime.now(timezone.utc)},
        {"name": "Emergency Services", "phone": "911", "notes": "Emergency", "created_at": datetime.now(timezone.utc)}
    ]
    _memory_calls: List[Dict[str, Any]] = []
    _memory_conversations: List[Dict[str, Any]] = []

db = Database()

async def connect_to_mongo():
    """Connect to MongoDB server using Motor"""
    try:
        logger.info(f"Connecting to MongoDB at {settings.MONGODB_URI}...")
        db.client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            serverSelectionTimeoutMS=2500,
            connectTimeoutMS=2500
        )
        # Test connection ping
        await db.client.admin.command('ping')
        db.db = db.client[settings.MONGODB_DB_NAME]
        db.is_connected = True
        logger.info(f"Connected to MongoDB database: {settings.MONGODB_DB_NAME}")

        # Clean up any legacy dummy hardcoded test numbers (+91 98765 43210)
        await db.db.contacts.delete_many({"phone": {"$regex": r"98765\s*43210"}})

        # Seed sample contacts only if empty (without dummy test numbers)
        contacts_count = await db.db.contacts.count_documents({})
        if contacts_count == 0:
            logger.info("Seeding initial contacts in MongoDB...")
            await db.db.contacts.insert_many([
                {"name": "Alice Johnson", "phone": "+1-555-019-2834", "notes": "Work Colleague", "created_at": datetime.now(timezone.utc)},
                {"name": "Mom", "phone": "+1-555-014-9988", "notes": "Family", "created_at": datetime.now(timezone.utc)},
                {"name": "Doctor Clinic", "phone": "+1-555-012-3456", "notes": "Healthcare", "created_at": datetime.now(timezone.utc)},
                {"name": "Emergency Services", "phone": "911", "notes": "Emergency", "created_at": datetime.now(timezone.utc)}
            ])
            logger.info("Sample contacts seeded successfully.")
    except Exception as e:
        logger.warning(f"MongoDB connection failed: {e}. Switching to active fallback in-memory store.")
        db.is_connected = False

async def close_mongo_connection():
    """Close MongoDB connection"""
    if db.client:
        logger.info("Closing MongoDB connection...")
        db.client.close()
        for client in db._loop_clients:
            client.close()
        db.is_connected = False

# Database helper functions

async def get_contacts(search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve contacts with optional text search"""
    if db.is_connected and db.db is not None:
        try:
            query = {}
            if search:
                query = {"$or": [
                    {"name": {"$regex": search, "$options": "i"}},
                    {"phone": {"$regex": search, "$options": "i"}},
                    {"notes": {"$regex": search, "$options": "i"}}
                ]}
            cursor = db.db.contacts.find(query).sort("name", 1)
            results = []
            async for doc in cursor:
                doc["_id"] = str(doc["_id"])
                results.append(doc)
            return results
        except Exception as e:
            logger.error(f"Error reading from MongoDB contacts: {e}")

    # Fallback search
    if search:
        s = search.lower()
        return [c for c in db._memory_contacts if s in c["name"].lower() or s in c["phone"]]
    return db._memory_contacts

async def find_contact_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Find contact matching exact name, prefix, or tokenized words in contact list.
    If no match is found in DB, also queries live connected phone via ADB.
    """
    clean_name = name.strip()
    if not clean_name:
        return None

    import re

    if db.is_connected and db.db is not None:
        try:
            # 1. Exact match (case insensitive)
            doc = await db.db.contacts.find_one({"name": {"$regex": f"^{re.escape(clean_name)}$", "$options": "i"}})
            if doc:
                doc["_id"] = str(doc["_id"])
                return doc

            # 2. Starts with query (e.g. 'Anmol' matches 'Anmol Apsit')
            doc = await db.db.contacts.find_one({"name": {"$regex": f"^{re.escape(clean_name)}", "$options": "i"}})
            if doc:
                doc["_id"] = str(doc["_id"])
                return doc

            # 3. Whole word boundary match
            doc = await db.db.contacts.find_one({"name": {"$regex": f"\\b{re.escape(clean_name)}\\b", "$options": "i"}})
            if doc:
                doc["_id"] = str(doc["_id"])
                return doc

            # 4. Query is a substring of contact name
            if len(clean_name) >= 3:
                doc = await db.db.contacts.find_one({"name": {"$regex": re.escape(clean_name), "$options": "i"}})
                if doc:
                    doc["_id"] = str(doc["_id"])
                    return doc
        except Exception as e:
            logger.error(f"MongoDB contact lookup error: {e}")

    # 5. Check connected phone via ADB if available
    try:
        from app.device.adb_controller import adb_controller
        if adb_controller.is_device_connected:
            phone_contacts = adb_controller.get_device_contacts()
            q_lower = clean_name.lower()
            for c in phone_contacts:
                c_name = c.get("name", "").lower()
                if c_name == q_lower or c_name.startswith(q_lower) or (len(q_lower) >= 3 and q_lower in c_name):
                    # Cache in DB
                    if db.is_connected and db.db is not None:
                        await db.db.contacts.update_one(
                            {"name": c["name"]},
                            {"$set": c},
                            upsert=True
                        )
                    return c
    except Exception as e:
        logger.debug(f"ADB contact check: {e}")

    # Fallback in-memory
    n = clean_name.lower()
    for c in db._memory_contacts:
        c_name = c["name"].lower()
        if c_name == n or c_name.startswith(n) or (len(n) >= 3 and n in c_name):
            return c
    return None


async def add_contact(name: str, phone: str, notes: str = "") -> Dict[str, Any]:
    """Add a new contact to database"""
    new_contact = {
        "name": name,
        "phone": phone,
        "notes": notes,
        "created_at": datetime.now(timezone.utc)
    }
    if db.is_connected and db.db is not None:
        try:
            result = await db.db.contacts.insert_one(new_contact)
            new_contact["_id"] = str(result.inserted_id)
            return new_contact
        except Exception as e:
            logger.error(f"Error inserting contact in MongoDB: {e}")

    new_contact["_id"] = f"mem_{len(db._memory_contacts) + 1}"
    db._memory_contacts.append(new_contact)
    return new_contact

async def log_call_event(
    phone_number: str,
    contact_name: Optional[str] = None,
    status: str = "initiated",
    initiated_by: str = "agent",
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """Log phone call in MongoDB call_logs collection"""
    call_entry = {
        "phone_number": phone_number,
        "contact_name": contact_name or "Unknown",
        "status": status,  # initiated, ringing, connected, completed, failed
        "initiated_by": initiated_by,
        "notes": notes or "",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if db.is_connected and db.db is not None:
        try:
            result = await db.db.call_logs.insert_one(call_entry)
            call_entry["_id"] = str(result.inserted_id)
            return call_entry
        except Exception as e:
            logger.error(f"Error logging call in MongoDB: {e}")

    call_entry["_id"] = f"call_{len(db._memory_calls) + 1}"
    db._memory_calls.append(call_entry)
    return call_entry

async def get_call_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve recent call history"""
    if db.is_connected and db.db is not None:
        try:
            cursor = db.db.call_logs.find().sort("timestamp", -1).limit(limit)
            logs = []
            async for doc in cursor:
                doc["_id"] = str(doc["_id"])
                logs.append(doc)
            return logs
        except Exception as e:
            logger.error(f"Error fetching call logs from MongoDB: {e}")

    return sorted(db._memory_calls, key=lambda x: x["timestamp"], reverse=True)[:limit]

async def save_conversation_turn(session_id: str, role: str, content: str, tool_calls: Optional[List[Dict[str, Any]]] = None):
    """Save user and agent turns with tool calls into MongoDB"""
    turn = {
        "session_id": session_id,
        "role": role,
        "content": content,
        "tool_calls": tool_calls or [],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if db.is_connected and db.db is not None:
        try:
            await db.db.conversations.insert_one(turn)
            return
        except Exception as e:
            logger.error(f"Error saving conversation turn: {e}")

    db._memory_conversations.append(turn)

async def get_recent_conversation_turns(session_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Retrieve recent conversation turns for a session from MongoDB"""
    if db.is_connected and db.db is not None:
        try:
            cursor = db.db.conversations.find({"session_id": session_id}).sort("timestamp", 1)
            turns = []
            async for doc in cursor:
                turns.append({
                    "role": doc.get("role", "user"),
                    "content": doc.get("content", ""),
                    "timestamp": doc.get("timestamp"),
                    "metadata": {"tool_calls": doc.get("tool_calls", [])}
                })
            return turns[-limit:]
        except Exception as e:
            logger.error(f"Error fetching conversation turns from MongoDB: {e}")
    # Fallback to in-memory conversations
    return [t for t in db._memory_conversations if t.get("session_id") == session_id][-limit:]
