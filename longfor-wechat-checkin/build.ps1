$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskFramework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$taskOutput = Join-Path $taskRoot 'DesktopBridge.exe'
& (Join-Path $taskFramework 'csc.exe') /nologo /codepage:65001 /target:exe /platform:x64 "/out:$taskOutput" /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/reference:$taskFramework\WPF\UIAutomationClient.dll" "/reference:$taskFramework\WPF\UIAutomationTypes.dll" "/reference:$taskFramework\WPF\WindowsBase.dll" (Join-Path $taskRoot 'DesktopBridge.cs')
if ($LASTEXITCODE -ne 0) { throw 'Desktop bridge compilation failed' }
