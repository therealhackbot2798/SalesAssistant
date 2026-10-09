"""
Build script to package the Sales Assist PySide6 app into a standalone executable.
Uses PyInstaller.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("Building Sales Assist executable...")
    
    # We use PyInstaller's CLI via subprocess
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",       # Don't open a console window
        "--onedir",         # Build a folder (faster startup than onefile)
        "--name=SalesAssist",
        # We need to include the sales_assist package
        "--paths=.", 
        # Entry point
        "sales_assist/main.py"
    ]
    
    # Run the build
    env = os.environ.copy()
    result = subprocess.run(cmd, env=env)
    
    if result.returncode == 0:
        dist_dir = Path("dist") / "SalesAssist"
        
        # Copy .env if available
        env_src = Path("sales_assist") / ".env"
        if not env_src.exists():
            env_src = Path(".env")
        if env_src.exists():
            shutil.copy2(env_src, dist_dir / ".env")
            print("[+] Copied .env into build folder.")
            
        # Copy knowledge_base if available
        kb_src = Path("knowledge_base")
        if kb_src.exists():
            kb_dest = dist_dir / "knowledge_base"
            if kb_dest.exists():
                shutil.rmtree(kb_dest)
            shutil.copytree(kb_src, kb_dest)
            print("[+] Copied knowledge_base folder into build folder.")

        try:
            print("\n✅ Build successful! Executable is in the 'dist/SalesAssist' folder.")
        except UnicodeEncodeError:
            print("\n[OK] Build successful! Executable is in the 'dist/SalesAssist' folder.")
    else:
        try:
            print("\n❌ Build failed.")
        except UnicodeEncodeError:
            print("\n[FAIL] Build failed.")
        sys.exit(result.returncode)

if __name__ == "__main__":
    main()
