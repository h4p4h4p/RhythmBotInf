$wsh = New-Object -ComObject WScript.Shell
1..4 | ForEach-Object { $wsh.SendKeys(']') }
Start-Sleep -Seconds 1
taskkill /IM python.exe /F | Out-Null
Start-Sleep -Milliseconds 400
if (Test-Path C:\Users\kalca\rhythm_bot.cfg) {
    Get-Content C:\Users\kalca\rhythm_bot.cfg
} else {
    Write-Output "no cfg"
}