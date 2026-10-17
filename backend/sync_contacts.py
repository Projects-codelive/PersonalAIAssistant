import asyncio
import subprocess
import re
from motor.motor_asyncio import AsyncIOMotorClient

async def sync_phone_contacts():
    adb_path = r"C:\Users\Shyam\AppData\Local\Android\Sdk\platform-tools\adb.exe"
    raw = subprocess.check_output(
        [adb_path, "shell", "content query --uri content://com.android.contacts/data/phones --projection display_name:data1"],
        text=True,
        errors="ignore"
    )

    contacts = []
    for line in raw.strip().splitlines():
        m = re.search(r"display_name=([^,]+),\s*data1=(.+)$", line)
        if m:
            name = m.group(1).strip()
            phone = m.group(2).strip()
            contacts.append({
                "name": name,
                "phone": phone,
                "phone_number": phone,
                "source": "phone_sync"
            })

    print(f"Parsed {len(contacts)} contacts from your phone.")

    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["agent_tool_db"]

    # Clear old dummy numbers
    await db.contacts.delete_many({"phone": "+91 98765 43210"})
    await db.contacts.delete_many({"name": "Anmol Apsit"})

    upserted = 0
    for c in contacts:
        await db.contacts.update_one(
            {"name": c["name"]},
            {"$set": c},
            upsert=True
        )
        upserted += 1

    print(f"Successfully synced {upserted} contacts into MongoDB agent_tool_db!")

    # Display first few synced contacts
    sample = await db.contacts.find({}).limit(5).to_list(5)
    print("Sample synced contacts in MongoDB:")
    for s in sample:
        print(f"  • {s.get('name')}: {s.get('phone')}")

if __name__ == "__main__":
    asyncio.run(sync_phone_contacts())
