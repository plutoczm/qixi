# Build the actual desktop "寻栖.exe" (not a Windows .lnk shortcut).
# The EXE is a local controller for the existing project + Python environment;
# it does not bundle Python/credentials, and it NEVER sets boot autostart.
param(
    [switch]$SkipDesktopExe,
    [switch]$SkipDesktopShortcut
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $root 'runtime\launcher'
$release = Join-Path $root 'dist\windows'
$source = Join-Path $root 'launcher\XunqiTray.cs'
$splash = Join-Path $root 'launcher\StartupWindow.cs'
$iconFile = Join-Path $root 'launcher\xunqi.ico'
$exe = Join-Path $runtime 'XunqiTray.exe'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$desktopExe = Join-Path $desktop '寻栖.exe'
$legacyShortcut = Join-Path $desktop '寻栖.lnk'
$releaseExe = Join-Path $release '寻栖.exe'
$csc = 'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe'

if (-not (Test-Path -LiteralPath $csc)) { throw 'The .NET Framework 4.x C# compiler is missing.' }
if (-not (Test-Path -LiteralPath $source)) { throw 'Launcher source not found.' }
if (-not (Test-Path -LiteralPath $splash)) { throw 'Startup window source not found.' }
if (-not (Test-Path -LiteralPath $desktop)) { throw 'Windows Desktop directory not found.' }
if (@(Get-Process -Name 'XunqiTray','寻栖' -ErrorAction SilentlyContinue).Count -gt 0) {
    throw '请先从系统托盘右键「退出寻栖」，再重新打包 EXE。'
}
New-Item -ItemType Directory -Path $runtime, $release -Force | Out-Null

# Warm application icon, generated locally without downloading or bundling a font.
if (-not (Test-Path -LiteralPath $iconFile)) {
    Add-Type -AssemblyName System.Drawing
    $bitmap = New-Object System.Drawing.Bitmap(64, 64)
    $drawing = [System.Drawing.Graphics]::FromImage($bitmap)
    $drawing.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $drawing.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $drawing.Clear([System.Drawing.Color]::Transparent)
    $background = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(175, 122, 88))
    $foreground = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 249, 236))
    $font = New-Object System.Drawing.Font('Microsoft YaHei UI', 34, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $format = New-Object System.Drawing.StringFormat
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $drawing.FillEllipse($background, 0, 0, 63, 63)
    $drawing.DrawString('栖', $font, $foreground, (New-Object System.Drawing.RectangleF(0, 0, 64, 64)), $format)
    $stream = New-Object System.IO.MemoryStream
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $stream.ToArray()
    $writer = New-Object System.IO.BinaryWriter([System.IO.File]::Open($iconFile, [System.IO.FileMode]::Create))
    try {
        $writer.Write([UInt16]0); $writer.Write([UInt16]1); $writer.Write([UInt16]1)
        $writer.Write([byte]64); $writer.Write([byte]64); $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([UInt16]1); $writer.Write([UInt16]32)
        $writer.Write([UInt32]$bytes.Length); $writer.Write([UInt32]22); $writer.Write($bytes)
    } finally {
        $writer.Dispose(); $stream.Dispose(); $format.Dispose()
        $font.Dispose(); $foreground.Dispose(); $background.Dispose()
        $drawing.Dispose(); $bitmap.Dispose()
    }
}
# The legacy Windows compiler needs UTF-8 BOM to preserve Chinese UI strings.
foreach ($file in @($source, $splash)) {
    $sourceText = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($file, $sourceText, (New-Object System.Text.UTF8Encoding($true)))
}

# Bind the installed project location INSIDE the EXE. A direct Desktop EXE can
# therefore locate the backend without a .lnk, cwd assumption or sidecar config.
$rootResource = Join-Path $runtime 'install-root.txt'
[System.IO.File]::WriteAllText($rootResource, $root, (New-Object System.Text.UTF8Encoding($false)))
$tempExe = Join-Path $runtime 'XunqiTray.build.exe'
$compilerArgs = @(
    '/nologo', '/target:winexe', '/platform:anycpu',
    "/out:$tempExe", "/win32icon:$iconFile",
    "/resource:$rootResource,XunqiInstallRoot",
    '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll',
    $source, $splash
)
& $csc @compilerArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $tempExe)) {
    throw 'Native EXE compilation failed.'
}
Move-Item -LiteralPath $tempExe -Destination $exe -Force

Add-Type -AssemblyName System.Drawing
$checkedIcon = New-Object System.Drawing.Icon($iconFile)
$checkedIcon.Dispose()
$version = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($exe)
if ($version.ProductName -ne '寻栖 · 个人版') {
    throw 'Built EXE identity check failed.'
}
Copy-Item -LiteralPath $exe -Destination $releaseExe -Force
if (-not ($SkipDesktopExe -or $SkipDesktopShortcut)) {
    if (Test-Path -LiteralPath $desktopExe) {
        $oldVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($desktopExe)
        if ($oldVersion.ProductName -ne '寻栖 · 个人版') {
            throw '桌面存在非本项目的同名 EXE，已停止覆盖。'
        }
    }
    Copy-Item -LiteralPath $exe -Destination $desktopExe -Force

    # Remove ONLY the old shortcut created by the previous R97 installer.
    if (Test-Path -LiteralPath $legacyShortcut) {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($legacyShortcut)
        if ([string]::Equals($link.TargetPath, $exe, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $legacyShortcut -Force
            Write-Output 'OLD_GENERATED_DESKTOP_SHORTCUT_REMOVED'
        }
    }
    Write-Output ('DESKTOP_REAL_EXE: ' + $desktopExe)
}
Write-Output ('LOCAL_APP_EXE: ' + $exe)
Write-Output ('PROJECT_RELEASE_EXE: ' + $releaseExe)
Write-Output ('FILE_VERSION: ' + $version.FileVersion)
Write-Output 'NO_AUTOSTART_REGISTERED'
