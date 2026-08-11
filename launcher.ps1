$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Add-Candidate {
    param(
        [System.Collections.Generic.List[object]]$List,
        [string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    if ([string]::IsNullOrWhiteSpace($Executable)) { return }
    $key = "$Executable|$($PrefixArguments -join ' ')"
    if (-not ($List | Where-Object { $_.Key -eq $key })) {
        $List.Add([pscustomobject]@{
            Key = $key
            Executable = $Executable
            PrefixArguments = $PrefixArguments
        })
    }
}

function Test-PythonCandidate {
    param([object]$Candidate)

    try {
        $testArguments = @($Candidate.PrefixArguments) + @(
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
        )
        & $Candidate.Executable @testArguments *> $null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

$candidates = [System.Collections.Generic.List[object]]::new()

if ($env:PYTHON_EXE) {
    Add-Candidate -List $candidates -Executable $env:PYTHON_EXE
}

foreach ($commandName in @("python.exe", "python3.exe")) {
    $command = Get-Command $commandName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        Add-Candidate -List $candidates -Executable $command.Source
    }
}

$pyCommand = Get-Command "py.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pyCommand) {
    Add-Candidate -List $candidates -Executable $pyCommand.Source -PrefixArguments @("-3")
}

$patterns = @(
    "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
    "$env:ProgramFiles\Python*\python.exe",
    "${env:ProgramFiles(x86)}\Python*\python.exe",
    "$env:USERPROFILE\miniconda3\python.exe",
    "$env:USERPROFILE\anaconda3\python.exe",
    "$env:LOCALAPPDATA\miniconda3\python.exe",
    "$env:LOCALAPPDATA\anaconda3\python.exe"
)

foreach ($pattern in $patterns) {
    Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        ForEach-Object { Add-Candidate -List $candidates -Executable $_.FullName }
}

foreach ($root in @(
    "HKCU:\Software\Python\PythonCore",
    "HKLM:\Software\Python\PythonCore",
    "HKLM:\Software\WOW6432Node\Python\PythonCore"
)) {
    Get-ChildItem -Path $root -ErrorAction SilentlyContinue | ForEach-Object {
        $installKey = Join-Path $_.PSPath "InstallPath"
        try {
            $props = Get-ItemProperty -Path $installKey -ErrorAction Stop
            $defaultPath = (Get-Item -Path $installKey -ErrorAction Stop).GetValue("")
            if ($props.ExecutablePath) {
                Add-Candidate -List $candidates -Executable $props.ExecutablePath
            }
            elseif ($defaultPath) {
                Add-Candidate -List $candidates -Executable (Join-Path $defaultPath "python.exe")
            }
        }
        catch { }
    }
}

$selected = $null
foreach ($candidate in $candidates) {
    if (Test-PythonCandidate -Candidate $candidate) {
        $selected = $candidate
        break
    }
}

if (-not $selected) {
    Write-Host ""
    Write-Host "Python 3.9 or newer was not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Fix options:"
    Write-Host "  1. Reinstall Python and enable 'Add python.exe to PATH'."
    Write-Host "  2. Or set PYTHON_EXE to the full python.exe path, for example:"
    Write-Host '     set "PYTHON_EXE=C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe"'
    Write-Host ""
    exit 1
}

$versionArguments = @($selected.PrefixArguments) + @(
    "-c",
    "import sys; print(sys.version.split()[0])"
)
$version = & $selected.Executable @versionArguments
Write-Host "Using Python $version" -ForegroundColor Green
Write-Host "Executable: $($selected.Executable)"
Write-Host ""

$arguments = @($selected.PrefixArguments) + @("server.py", "--open-browser")

& $selected.Executable @arguments
exit $LASTEXITCODE
