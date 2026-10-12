param([switch]$Enable, [string]$PythonPath, [string]$TaskPrefix = 'Longfor-UI')
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskName = $TaskPrefix + '-Android-Daily-Checkin'
$taskOwner = 'Longfor UI open-source Android check-in; root=' + $taskRoot
$taskConfig = Get-Content -LiteralPath (Join-Path $taskRoot 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Enable -and (-not $taskConfig.enabled -or -not $taskConfig.account_hash -or -not $taskConfig.success_markers -or $taskConfig.wake_mode -ne 'unprotected')) { throw 'The signing route is not verified; task cannot be enabled.' }
$taskPython = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { (Get-Command python.exe).Source }
$taskPythonWindowless = Join-Path (Split-Path $taskPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPythonWindowless)) { throw 'Verified pythonw.exe not found' }
$taskSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskBeijingNow = (Get-Date).ToUniversalTime().AddHours(8)
$taskBoundary = $taskBeijingNow.Date.AddDays(1).AddHours(6).ToString("yyyy-MM-dd'T'HH:mm:ss") + '+08:00'
$taskEscape = { param($text) [System.Security.SecurityElement]::Escape($text) }
$taskArguments = '-X utf8 "' + (Join-Path $taskRoot 'checkin.py') + '" scheduled'
$taskEnabled = if ($Enable) { 'true' } else { 'false' }
$taskOwnerXml = [System.Security.SecurityElement]::Escape($taskOwner)
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$taskOwnerXml</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>$taskBoundary</StartBoundary><Enabled>true</Enabled><RandomDelay>PT85M</RandomDelay><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$taskSid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>false</StartWhenAvailable><AllowHardTerminate>true</AllowHardTerminate><Enabled>$taskEnabled</Enabled><Hidden>true</Hidden><ExecutionTimeLimit>PT5M</ExecutionTimeLimit><WakeToRun>true</WakeToRun></Settings>
  <Actions Context="Author"><Exec><Command>$(& $taskEscape $taskPythonWindowless)</Command><Arguments>$(& $taskEscape $taskArguments)</Arguments><WorkingDirectory>$(& $taskEscape $taskRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@
$taskXml | Set-Content -LiteralPath (Join-Path $taskRoot 'task.xml') -Encoding Unicode
$taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($taskExisting -and $taskExisting.Description -ne $taskOwner) { throw 'An unrelated task has this name; refusing to replace it.' }
Register-ScheduledTask -TaskName $taskName -Xml $taskXml -Force | Select-Object TaskName,State
