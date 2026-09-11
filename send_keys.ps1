$wsh = New-Object -ComObject WScript.Shell
$wsh.SendKeys(']')
$wsh.SendKeys('=')
$wsh.SendKeys('z')
Start-Sleep -Seconds 2