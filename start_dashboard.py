"""
start_dashboard.py — Convenience script to start the FastAPI dashboard.

Usage:
    python start_dashboard.py
    python start_dashboard.py --port 8080 --reload

The dashboard will be available at http://localhost:8000
"""
import subprocess
import sys
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the Comatic Bridge dashboard")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload (development)")
    args = parser.parse_args()

    cmd = [
        sys.executable, "-m", "uvicorn",
        "web.app:app",
        "--host", args.host,
        "--port", str(args.port),
    ]
    if args.reload:
        cmd.append("--reload")

    print(f">> Starting Comatic Bridge dashboard at http://{args.host}:{args.port}")
    subprocess.run(cmd)
