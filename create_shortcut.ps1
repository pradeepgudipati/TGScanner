# Create Desktop Shortcut for TOI Telegram Downloader
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "TOI Telegram Downloader.lnk"
$TargetPath = "C:\Dev\Personal\TelegramTOI\launch_toi_gui.bat"
$IconPath = "C:\Windows\System32\shell32.dll"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.WorkingDirectory = "C:\Dev\Personal\TelegramTOI"
$Shortcut.Description = "Download today's TOI Hyderabad PDF from Telegram"
$Shortcut.IconLocation = "$IconPath,165"
$Shortcut.Save()

Write-Host "Desktop shortcut created successfully!"
Write-Host "Location: $ShortcutPath"
Write-Host ""
Write-Host "You can now double-click the shortcut on your desktop to launch the app!"

