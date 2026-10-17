"""Personal contacts live in a git-ignored file, never in source."""
import json
import re
from pathlib import Path

import app.agent.contact_matcher as contact_matcher

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = [ROOT / "backend" / "app", ROOT / "client", ROOT / "android" / "app" / "src"]
SOURCE_SUFFIXES = {".py", ".js", ".html", ".kt", ".xml"}


def test_builtin_contacts_loaded_from_file(tmp_path, monkeypatch):
    contacts = [{"name": "Test Person", "phone": "+15550000000", "has_phone": True}]
    path = tmp_path / "builtin_contacts.json"
    path.write_text(json.dumps(contacts))
    monkeypatch.setattr(contact_matcher, "BUILTIN_CONTACTS_FILE", path)
    assert contact_matcher.load_builtin_contacts() == contacts


def test_missing_or_broken_contacts_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(contact_matcher, "BUILTIN_CONTACTS_FILE", tmp_path / "missing.json")
    assert contact_matcher.load_builtin_contacts() == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(contact_matcher, "BUILTIN_CONTACTS_FILE", broken)
    assert contact_matcher.load_builtin_contacts() == []


def test_no_real_phone_numbers_or_gmail_addresses_in_source():
    indian_mobile = re.compile(r"(?:\+91[\s-]?)?(?<!\d)[6-9]\d{9}(?!\d)")
    gmail = re.compile(r"[\w.+-]+@gmail\.com", re.I)
    offenders = []
    for base in SOURCE_DIRS:
        for f in base.rglob("*"):
            if f.suffix in SOURCE_SUFFIXES and f.is_file():
                text = f.read_text(errors="ignore")
                for m in list(indian_mobile.finditer(text)) + list(gmail.finditer(text)):
                    offenders.append(f"{f.relative_to(ROOT)}: {m.group(0)}")
    assert offenders == []
