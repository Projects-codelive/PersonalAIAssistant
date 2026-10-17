# The backend/test_*.py files are live scenario scripts, not unit tests: with a phone attached they
# place real calls and send real WhatsApp/SMS/email to real numbers. Never let pytest collect them.
# Run one deliberately with: .venv/bin/python test_<name>.py
collect_ignore_glob = ["test_*.py"]
