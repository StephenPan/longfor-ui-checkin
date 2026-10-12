param([string]$TaskPrefix = 'Longfor-UI')
$ErrorActionPreference = 'Stop'
Disable-ScheduledTask -TaskName ($TaskPrefix + '-WeChat-Daily-Checkin') | Select-Object TaskName,State
