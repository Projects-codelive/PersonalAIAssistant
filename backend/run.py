import sys
import os
import socket
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Ensure the backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

import uvicorn
from app.config import settings

def get_local_ip():
    """Get the local network IP for mobile device access"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def generate_self_signed_cert(cert_path: Path, key_path: Path):
    """Generate self-signed SSL certificate for local HTTPS"""
    if cert_path.exists() and key_path.exists():
        return
    try:
        import datetime
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, u"AgentTool Mobile")
        ])
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.datetime.now(datetime.timezone.utc)
        ).not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
        ).sign(key, hashes.SHA256())

        key_path.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    except Exception as e:
        print(f"Could not generate SSL cert: {e}")

if __name__ == "__main__":
    local_ip = get_local_ip()
    port = settings.PORT
    use_ssl = "--ssl" in sys.argv

    ssl_keyfile = None
    ssl_certfile = None
    protocol = "http"

    if use_ssl:
        cert_file = backend_dir / "cert.pem"
        key_file = backend_dir / "key.pem"
        generate_self_signed_cert(cert_file, key_file)
        if cert_file.exists() and key_file.exists():
            ssl_keyfile = str(key_file)
            ssl_certfile = str(cert_file)
            protocol = "https"

    print("\n" + "=" * 64)
    print("      🚀 AGENTTOOL AUTONOMOUS MOBILE ASSISTANT SERVER 🚀      ")
    print("=" * 64)
    print(f" • Local Web Access:     {protocol}://localhost:{port}")
    print(f" • Mobile Device Access:    {protocol}://{local_ip}:{port}")
    print(f" • MongoDB Connection:      {settings.MONGODB_URI}")
    print(f" • Gemma LLM Provider:      {settings.GEMMA_PROVIDER} ({settings.GEMMA_MODEL_NAME})")
    print(f" • ElevenLabs Voice:        {'Configured' if settings.ELEVENLABS_API_KEY else 'Browser Voice Fallback'}")
    print(f" • API Auth:                localhost & USB phone trusted; Wi-Fi/LAN clients need the token")
    print(f" • Wi-Fi Pairing Link:      {protocol}://{local_ip}:{port}/?token={settings.API_TOKEN}")
    print(f" • Wi-Fi Android App URL:   ws://{local_ip}:{port}/ws/device/agent-mobile?token={settings.API_TOKEN}")
    # Auto configure ADB reverse port for USB-connected Android phone
    try:
        import subprocess
        adb_path = r"C:\Users\Shyam\AppData\Local\Android\Sdk\platform-tools\adb.exe"
        if not Path(adb_path).exists():
            adb_path = "adb"
        subprocess.run([adb_path, "reverse", f"tcp:{port}", f"tcp:{port}"], capture_output=True)
        print(f" • USB Phone Forwarding:   adb reverse tcp:{port} tcp:{port} (Active)")
    except Exception as e:
        print(f" • USB Phone Forwarding:   Could not setup adb reverse: {e}")

    print("=" * 64)
    print(" Tip: Phone connects via USB directly using: ws://127.0.0.1:8000/ws/device/agent-mobile")
    print("      No contacts are copied to database — Gemma tracks screen & calls live!")
    print("=" * 64 + "\n")

    uvicorn_kwargs = {
        "app": "app.main:app",
        "host": settings.HOST,
        "port": settings.PORT,
        "reload": True,
        "reload_dirs": [str(backend_dir)]
    }
    if ssl_keyfile and ssl_certfile:
        uvicorn_kwargs["ssl_keyfile"] = ssl_keyfile
        uvicorn_kwargs["ssl_certfile"] = ssl_certfile

    uvicorn.run(**uvicorn_kwargs)
