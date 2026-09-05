param([string]$Executable = (Join-Path $PSScriptRoot '../dist/MediaCategorizer4_5.exe'))
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MediaCategorizerSmokeWindow {
    private delegate bool EnumCallback(IntPtr window, IntPtr state);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumCallback callback, IntPtr state);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    public static IntPtr Find(int processId, string caption) {
        IntPtr result = IntPtr.Zero;
        EnumWindows((window, state) => {
            uint owner; GetWindowThreadProcessId(window, out owner);
            if (owner != processId) return true;
            var title = new StringBuilder(1024);
            GetWindowText(window, title, title.Capacity);
            if (title.ToString() != caption) return true;
            result = window; return false;
        }, IntPtr.Zero);
        return result;
    }
}
'@

$Executable = (Resolve-Path -LiteralPath $Executable).Path
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$configRoot = [IO.Path]::GetFullPath((Join-Path $tempParent ('MediaCategorizer-smoke-' + [guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $configRoot | Out-Null
$originalAppData = $env:APPDATA
$originalQtPlatform = $env:QT_QPA_PLATFORM
$ownedIds = @()
$started = $null
try {
    $env:APPDATA = $configRoot
    $env:QT_QPA_PLATFORM = 'windows'
    $started = Start-Process -FilePath $Executable -WorkingDirectory (Split-Path $Executable) -WindowStyle Hidden -PassThru
    $ownedIds += $started.Id
    $foundWindow = $null
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($started.Id)")
        $ownedIds = @($ownedIds + @($children.ProcessId) | Where-Object { $_ } | Select-Object -Unique)
        foreach ($candidateId in $ownedIds) {
            $candidate = Get-Process -Id $candidateId -ErrorAction SilentlyContinue
            if (-not $candidate) { continue }
            $errorWindow = [MediaCategorizerSmokeWindow]::Find($candidateId, 'Unhandled exception in script')
            if ($errorWindow -ne [IntPtr]::Zero) {
                throw 'The packaged application displayed an exception dialog.'
            }
            $windowHandle = [MediaCategorizerSmokeWindow]::Find($candidateId, 'Media Categorizer 4.5')
            if ($windowHandle -ne [IntPtr]::Zero) {
                $foundWindow = $windowHandle
                break
            }
        }
        if ($foundWindow -or $started.HasExited) { break }
        Start-Sleep -Milliseconds 200
    }
    if (-not $foundWindow) { throw 'Application window was not found.' }
    $title = 'Media Categorizer 4.5'
    $closed = [MediaCategorizerSmokeWindow]::PostMessage($foundWindow, 16, [IntPtr]::Zero, [IntPtr]::Zero)
    if (-not $started.WaitForExit(10000)) { throw 'Application did not exit after normal close.' }
    if ($started.ExitCode -ne 0) { throw "Application exited with code $($started.ExitCode)." }
    $settingsCreated = Test-Path (Join-Path $configRoot 'MediaCategorizer/settings_v4.json')
    if (-not $settingsCreated) { throw 'Isolated settings were not saved.' }
    [pscustomobject]@{
        title = $title
        normalCloseRequested = $closed
        exitCode = $started.ExitCode
        isolatedSettingsCreated = $settingsCreated
    } | ConvertTo-Json -Compress
}
finally {
    foreach ($ownedId in ($ownedIds | Sort-Object -Descending)) {
        $owned = Get-Process -Id $ownedId -ErrorAction SilentlyContinue
        if ($owned) {
            foreach ($caption in @('Media Categorizer 4.5', 'Unhandled exception in script')) {
                $handle = [MediaCategorizerSmokeWindow]::Find($ownedId, $caption)
                if ($handle -ne [IntPtr]::Zero) {
                    $null = [MediaCategorizerSmokeWindow]::PostMessage($handle, 16, [IntPtr]::Zero, [IntPtr]::Zero)
                }
            }
            if (-not $owned.WaitForExit(2000)) { Stop-Process -Id $ownedId -Force }
        }
    }
    $env:APPDATA = $originalAppData
    $env:QT_QPA_PLATFORM = $originalQtPlatform
    # Delete only the unique test directory, after checking its resolved boundary.
    $resolvedConfig = (Resolve-Path -LiteralPath $configRoot).Path
    if (-not $resolvedConfig.StartsWith($tempParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Temporary directory resolved outside the intended temp folder.'
    }
    Remove-Item -LiteralPath $resolvedConfig -Recurse -Force
}
