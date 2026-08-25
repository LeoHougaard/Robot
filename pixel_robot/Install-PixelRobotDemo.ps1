[CmdletBinding()]
param(
    [string]$Serial,
    [switch]$SkipBuild,
    [switch]$BuildOnly,
    [switch]$AllowUsbAdb,
    [switch]$SkipDeviceTests,
    [switch]$NoBrowserForward
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$apkPath = Join-Path $projectRoot 'app\build\outputs\apk\debug\app-debug.apk'
$testApkPath = Join-Path $projectRoot 'app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk'

if (-not $SkipBuild) {
    $gradleTasks = @('test', 'assembleDebug')
    if (-not $SkipDeviceTests) {
        $gradleTasks += 'assembleAndroidTest'
    }
    & (Join-Path $projectRoot 'gradlew.bat') @gradleTasks
    if ($LASTEXITCODE -ne 0) {
        throw "Android build or unit tests failed with exit code $LASTEXITCODE."
    }

    & python (Join-Path $projectRoot 'tools\check_elf_alignment.py') $apkPath
    if ($LASTEXITCODE -ne 0) {
        throw "The APK contains a native library that does not support 16 KB pages."
    }
}

if (-not (Test-Path -LiteralPath $apkPath -PathType Leaf)) {
    throw "APK not found at $apkPath. Run without -SkipBuild first."
}

if ($BuildOnly) {
    Write-Host "Pixel demo APK is ready: $apkPath"
    if (-not $SkipDeviceTests -and (Test-Path -LiteralPath $testApkPath -PathType Leaf)) {
        Write-Host "Pixel instrumentation APK is ready: $testApkPath"
    }
    return
}

$adbCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    $(if ($env:ANDROID_HOME) { Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe' }),
    $(if ($env:ANDROID_SDK_ROOT) { Join-Path $env:ANDROID_SDK_ROOT 'platform-tools\adb.exe' })
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }

$adb = $adbCandidates | Select-Object -First 1
if (-not $adb) {
    $adbCommand = Get-Command adb -ErrorAction SilentlyContinue
    if ($adbCommand) {
        $adb = $adbCommand.Source
    } else {
        throw "ADB was not found. Install Android SDK Platform-Tools or set ANDROID_HOME."
    }
}

$deviceLines = & $adb devices -l | Where-Object { $_ -match '^\S+\s+device(?:\s|$)' }
if ($Serial) {
    $deviceLine = $deviceLines | Where-Object { ($_ -split '\s+')[0] -eq $Serial } | Select-Object -First 1
    if (-not $deviceLine) {
        throw "ADB device '$Serial' is not connected and authorized."
    }
    $selectedSerial = $Serial
} else {
    $serials = @($deviceLines | ForEach-Object { ($_ -split '\s+')[0] })
    if ($serials.Count -eq 0) {
        throw "No authorized Pixel is connected. Pair Wireless debugging on the Pixel, then run this script again."
    }
    if ($serials.Count -gt 1) {
        throw "More than one ADB device is connected. Run again with -Serial <device>."
    }
    $selectedSerial = $serials[0]
}

if (-not $AllowUsbAdb -and $selectedSerial -notmatch ':') {
    throw "The selected ADB connection does not look wireless. Pair Wireless debugging so the Pixel USB-C port stays free for the ESP32, or pass -AllowUsbAdb for installation only."
}

& $adb -s $selectedSerial install -r -t $apkPath
if ($LASTEXITCODE -ne 0) {
    throw "APK installation failed with exit code $LASTEXITCODE."
}

if (-not $SkipDeviceTests) {
    if (-not (Test-Path -LiteralPath $testApkPath -PathType Leaf)) {
        throw "Instrumentation APK not found at $testApkPath. Run without -SkipBuild or pass -SkipDeviceTests."
    }
    & $adb -s $selectedSerial install -r -t $testApkPath
    if ($LASTEXITCODE -ne 0) {
        throw "Instrumentation APK installation failed with exit code $LASTEXITCODE."
    }
    $instrumentationOutput = & $adb -s $selectedSerial shell am instrument -w `
        com.leo.pixelrobot.test/androidx.test.runner.AndroidJUnitRunner 2>&1
    $instrumentationText = $instrumentationOutput -join [Environment]::NewLine
    Write-Host $instrumentationText
    if ($LASTEXITCODE -ne 0 -or $instrumentationText -notmatch '(?m)^OK \(') {
        throw "Pixel instrumentation tests did not pass. The app was installed but was not launched as a demo."
    }
}

if (-not $NoBrowserForward) {
    & $adb -s $selectedSerial forward tcp:8767 tcp:8767
    if ($LASTEXITCODE -ne 0) {
        throw "ADB browser forwarding failed with exit code $LASTEXITCODE."
    }
}

& $adb -s $selectedSerial shell am start -n com.leo.pixelrobot/.MainActivity
if ($LASTEXITCODE -ne 0) {
    throw "Pixel Robot did not launch."
}

Write-Host "Pixel Robot is installed and open on $selectedSerial."
Write-Host "Connect the ESP32 to the Pixel, accept the USB permission prompt, and place the robot in a clear floor area."
if (-not $NoBrowserForward) {
    Write-Host "Optional mirrored control page: http://127.0.0.1:8767/"
}
