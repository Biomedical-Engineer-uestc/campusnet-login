$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$versionMatch = Select-String -Path "campusnet\__init__.py" -Pattern '__version__ = "([^"]+)"'
if (-not $versionMatch) { throw "Cannot read version from campusnet/__init__.py" }
$version = $versionMatch.Matches[0].Groups[1].Value

$buildDir = Join-Path $PSScriptRoot "build"
$distDir = Join-Path $PSScriptRoot "dist"
Remove-Item $buildDir, $distDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $buildDir, $distDir -Force | Out-Null

python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required. Install it with: python -m pip install pyinstaller"
}

$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--console",
    "--name", "CampusNet",
    "--paths", $root,
    "--hidden-import", "campusnet._dpapi",
    "--exclude-module", "keyring",
    "--distpath", (Join-Path $distDir "app"),
    "--workpath", (Join-Path $buildDir "pyinstaller"),
    "--specpath", (Join-Path $buildDir "spec"),
    (Join-Path $root "campus_net.py")
)
& python @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$exe = Join-Path $distDir "app\CampusNet.exe"
if (-not (Test-Path $exe)) { throw "CampusNet.exe not found after build" }

& $exe --version
if ($LASTEXITCODE -ne 0) { throw "Built executable failed to start" }

$stage = Join-Path $distDir "CampusNet-$version-win64"
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item $exe (Join-Path $stage "CampusNet.exe")
Copy-Item (Join-Path $PSScriptRoot "portable\*") $stage -Recurse -Force
Copy-Item (Join-Path $root "config.example.ini") $stage -Force
Copy-Item (Join-Path $root "LICENSE") $stage -Force

$zip = Join-Path $distDir "CampusNet-$version-win64.zip"
Compress-Archive -Path $stage -DestinationPath $zip -Force
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$hashFile = Join-Path $distDir "CampusNet-$version-win64.zip.sha256"
"$hash  CampusNet-$version-win64.zip" | Set-Content -Encoding ASCII $hashFile

Write-Host ""
Write-Host "ZIP:    $zip"
Write-Host "SHA256: $hash"
