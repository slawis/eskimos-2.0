#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESKIMOS 2.0 - Instalator GUI
Double-click to install - designed for simplicity (even a 6-year-old can use it!)
"""

import os
import sys
import subprocess
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# Configuration
INSTALL_DIR = Path("C:/eskimos")
GITHUB_REPO = "https://github.com/slawis/eskimos-2.0.git"
DASHBOARD_URL = "http://localhost:8000"


class EskimosInstaller:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Eskimos 2.0 - Instalator")
        self.root.geometry("450x350")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (450 // 2)
        y = (self.root.winfo_screenheight() // 2) - (350 // 2)
        self.root.geometry(f"450x350+{x}+{y}")

        self.setup_ui()

    def setup_ui(self):
        # Title
        title = tk.Label(
            self.root,
            text="ESKIMOS 2.0",
            font=("Segoe UI", 28, "bold"),
            fg="#00d4ff",
            bg="#1a1a2e"
        )
        title.pack(pady=(30, 5))

        subtitle = tk.Label(
            self.root,
            text="SMS Gateway z AI",
            font=("Segoe UI", 12),
            fg="#888888",
            bg="#1a1a2e"
        )
        subtitle.pack(pady=(0, 30))

        # Main button
        self.btn_frame = tk.Frame(self.root, bg="#1a1a2e")
        self.btn_frame.pack(pady=20)

        self.main_button = tk.Button(
            self.btn_frame,
            text="INSTALUJ",
            font=("Segoe UI", 20, "bold"),
            fg="white",
            bg="#4CAF50",
            activebackground="#45a049",
            activeforeground="white",
            width=15,
            height=2,
            cursor="hand2",
            relief="flat",
            command=self.start_installation
        )
        self.main_button.pack()

        # Progress bar
        self.progress_frame = tk.Frame(self.root, bg="#1a1a2e")
        self.progress_frame.pack(pady=20, fill="x", padx=40)

        style = ttk.Style()
        style.theme_use('default')
        style.configure(
            "green.Horizontal.TProgressbar",
            troughcolor='#2d2d44',
            background='#4CAF50',
            thickness=25
        )

        self.progress = ttk.Progressbar(
            self.progress_frame,
            style="green.Horizontal.TProgressbar",
            length=370,
            mode='determinate'
        )
        self.progress.pack()

        # Status label
        self.status_label = tk.Label(
            self.root,
            text="Kliknij INSTALUJ aby rozpoczac",
            font=("Segoe UI", 11),
            fg="#cccccc",
            bg="#1a1a2e"
        )
        self.status_label.pack(pady=10)

        # Version info
        version_label = tk.Label(
            self.root,
            text="v2.0.0 | github.com/slawis/eskimos-2.0",
            font=("Segoe UI", 9),
            fg="#555555",
            bg="#1a1a2e"
        )
        version_label.pack(side="bottom", pady=10)

    def update_status(self, text, progress_value=None):
        self.status_label.config(text=text)
        if progress_value is not None:
            self.progress['value'] = progress_value
        self.root.update()

    def check_python(self):
        """Check for Python using multiple methods."""
        # Try different Python commands
        python_commands = ["py", "python", "python3"]

        for cmd in python_commands:
            try:
                result = subprocess.run(
                    [cmd, "--version"],
                    capture_output=True,
                    text=True,
                    shell=True
                )
                version = result.stdout.strip() or result.stderr.strip()
                if any(v in version for v in ["3.11", "3.12", "3.13", "3.14"]):
                    self.python_cmd = cmd
                    return True, version
            except Exception:
                continue

        # Try direct paths
        python_paths = [
            "C:/Python314/python.exe",
            "C:/Python313/python.exe",
            "C:/Python312/python.exe",
            "C:/Python311/python.exe",
            os.path.expanduser("~/AppData/Local/Programs/Python/Python314/python.exe"),
            os.path.expanduser("~/AppData/Local/Programs/Python/Python313/python.exe"),
            os.path.expanduser("~/AppData/Local/Programs/Python/Python312/python.exe"),
            os.path.expanduser("~/AppData/Local/Programs/Python/Python311/python.exe"),
        ]

        for path in python_paths:
            if os.path.exists(path):
                try:
                    result = subprocess.run(
                        [path, "--version"],
                        capture_output=True,
                        text=True
                    )
                    version = result.stdout.strip()
                    self.python_cmd = path
                    return True, version
                except Exception:
                    continue

        self.python_cmd = None
        return False, None

    def check_git(self):
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True
            )
            return True, result.stdout.strip()
        except Exception:
            return False, None

    def run_command(self, cmd, cwd=None):
        """Run command and return success status."""
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                shell=True
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Error: {e}")
            return False

    def install(self):
        try:
            # Step 1: Check Python
            self.update_status("Sprawdzam Python...", 10)
            python_ok, python_ver = self.check_python()
            if not python_ok:
                messagebox.showerror(
                    "Brak Python",
                    "Python 3.11+ nie jest zainstalowany!\n\n"
                    "Pobierz z: python.org/downloads\n"
                    "Zaznacz 'Add Python to PATH'!"
                )
                self.reset_ui()
                return

            # Step 2: Check Git
            self.update_status("Sprawdzam Git...", 20)
            git_ok, git_ver = self.check_git()
            if not git_ok:
                messagebox.showerror(
                    "Brak Git",
                    "Git nie jest zainstalowany!\n\n"
                    "Pobierz z: git-scm.com/download/win"
                )
                self.reset_ui()
                return

            # Step 3: Clone or update repo
            self.update_status("Pobieranie z GitHub...", 30)
            if (INSTALL_DIR / ".git").exists():
                # Update existing
                self.run_command("git pull origin master", cwd=INSTALL_DIR)
            else:
                # Fresh clone
                if INSTALL_DIR.exists():
                    import shutil
                    shutil.rmtree(INSTALL_DIR)
                self.run_command(f'git clone {GITHUB_REPO} "{INSTALL_DIR}"')

            self.update_status("Pobrano!", 40)

            # Step 4: Create venv
            self.update_status("Tworzenie srodowiska...", 50)
            venv_path = INSTALL_DIR / "venv"
            if not venv_path.exists():
                self.run_command(f'"{self.python_cmd}" -m venv "{venv_path}"')

            # Step 5: Install dependencies
            self.update_status("Instalowanie zaleznosci (to moze chwile potrwac)...", 60)
            pip_path = venv_path / "Scripts" / "pip.exe"
            self.run_command(f'"{pip_path}" install --upgrade pip')

            self.update_status("Instalowanie pakietow...", 70)
            self.run_command(f'"{pip_path}" install -e "{INSTALL_DIR}"')

            # Step 6: Create .env
            self.update_status("Konfiguracja...", 85)
            env_example = INSTALL_DIR / ".env.example"
            env_file = INSTALL_DIR / ".env"
            if env_example.exists() and not env_file.exists():
                import shutil
                shutil.copy(env_example, env_file)

            # Step 7: Create launcher
            self.update_status("Tworzenie launchera...", 90)
            self.create_launcher()

            # Done!
            self.update_status("GOTOWE!", 100)
            self.show_success()

        except Exception as e:
            messagebox.showerror("Blad", f"Wystapil blad:\n{str(e)}")
            self.reset_ui()

    def create_launcher(self):
        """Create the ESKIMOS_URUCHOM.pyw launcher file."""
        launcher_code = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ESKIMOS 2.0 - Launcher"""

import os
import sys
import subprocess
import webbrowser
import time
import tkinter as tk
from pathlib import Path

INSTALL_DIR = Path("C:/eskimos")
DASHBOARD_URL = "http://localhost:8000"

def main():
    root = tk.Tk()
    root.title("Eskimos 2.0")
    root.geometry("300x150")
    root.resizable(False, False)
    root.configure(bg="#1a1a2e")

    # Center
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (300 // 2)
    y = (root.winfo_screenheight() // 2) - (150 // 2)
    root.geometry(f"300x150+{x}+{y}")

    label = tk.Label(
        root,
        text="Uruchamianie Eskimos...",
        font=("Segoe UI", 14),
        fg="#00d4ff",
        bg="#1a1a2e"
    )
    label.pack(pady=50)

    root.update()

    # Start server
    venv_python = INSTALL_DIR / "venv" / "Scripts" / "python.exe"
    subprocess.Popen(
        [str(venv_python), "-m", "eskimos.cli.main", "serve"],
        cwd=INSTALL_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW
    )

    # Wait and open browser
    time.sleep(3)
    webbrowser.open(DASHBOARD_URL)

    label.config(text="Dashboard otwarty!\\nMozesz zamknac to okno.")

    root.after(5000, root.destroy)
    root.mainloop()

if __name__ == "__main__":
    main()
'''
        launcher_path = INSTALL_DIR / "ESKIMOS_URUCHOM.pyw"
        with open(launcher_path, "w", encoding="utf-8") as f:
            f.write(launcher_code)

        # Also create desktop shortcut
        desktop = Path.home() / "Desktop"
        shortcut_path = desktop / "Eskimos Dashboard.url"
        with open(shortcut_path, "w") as f:
            f.write("[InternetShortcut]\n")
            f.write(f"URL={DASHBOARD_URL}\n")

    def show_success(self):
        """Show success UI with launch button."""
        self.main_button.config(
            text="URUCHOM",
            bg="#2196F3",
            activebackground="#1976D2",
            command=self.launch_dashboard
        )
        self.status_label.config(
            text="Instalacja zakonczona! Kliknij URUCHOM",
            fg="#4CAF50"
        )

    def launch_dashboard(self):
        """Launch the dashboard."""
        launcher_path = INSTALL_DIR / "ESKIMOS_URUCHOM.pyw"
        if launcher_path.exists():
            subprocess.Popen(
                ["pythonw", str(launcher_path)],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        self.root.after(1000, self.root.destroy)

    def reset_ui(self):
        """Reset UI after error."""
        self.main_button.config(state="normal", text="INSTALUJ")
        self.progress['value'] = 0
        self.status_label.config(text="Kliknij INSTALUJ aby rozpoczac")

    def start_installation(self):
        """Start installation in background thread."""
        self.main_button.config(state="disabled")
        thread = threading.Thread(target=self.install, daemon=True)
        thread.start()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = EskimosInstaller()
    app.run()
