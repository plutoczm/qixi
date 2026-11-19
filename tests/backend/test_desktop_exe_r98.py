"""R98 offline guardrails for the actual Windows desktop executable.

The real GUI lifecycle is separately verified on Windows. These tests ensure
future refactors do not silently revert to an .lnk-only or auto-start install.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_actual_exe_installer_embeds_project_root_into_compiled_binary():
    source = (ROOT / "launcher" / "XunqiTray.cs").read_text(encoding="utf-8-sig")
    installer = (ROOT / "scripts" / "install_tray_launcher.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert 'GetManifestResourceStream("XunqiInstallRoot")' in source
    assert "/resource:$rootResource,XunqiInstallRoot" in installer
    assert "IsProjectRoot" in source


def test_desktop_target_is_a_real_exe_not_a_link():
    installer = (ROOT / "scripts" / "install_tray_launcher.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "Join-Path $desktop '寻栖.exe'" in installer
    assert "Copy-Item -LiteralPath $exe -Destination $desktopExe -Force" in installer
    assert "OLD_GENERATED_DESKTOP_SHORTCUT_REMOVED" in installer
    assert "CreateShortcut($shortcut)" not in installer


def test_installer_refuses_to_overwrite_foreign_desktop_exe():
    installer = (ROOT / "scripts" / "install_tray_launcher.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "$oldVersion.ProductName -ne '寻栖 · 个人版'" in installer
    assert "throw '桌面存在非本项目的同名 EXE，已停止覆盖。'" in installer


def test_native_exe_has_splash_and_explicit_exit_without_autostart():
    launcher = (ROOT / "launcher" / "XunqiTray.cs").read_text(encoding="utf-8-sig")
    splash = (ROOT / "launcher" / "StartupWindow.cs").read_text(encoding="utf-8-sig")
    installer = (ROOT / "scripts" / "install_tray_launcher.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "startup.Show();" in launcher
    assert "退出寻栖（关闭后台）" in launcher
    assert "正在准备本地服务" in splash
    assert "Application.Run(new TrayApp())" in launcher
    assert "RegistryKey" not in installer
    assert "schtasks" not in installer.lower()
    assert "NO_AUTOSTART_REGISTERED" in installer


def test_start_script_only_uses_launcher_exe():
    start = (ROOT / "start.cmd").read_text(encoding="utf-8")
    stop = (ROOT / "stop.cmd").read_text(encoding="utf-8")
    assert "XunqiTray.exe" in start
    assert "-SkipDesktopExe" in start
    assert "scripts\\dev.py" not in start
    assert "--quit" in stop
