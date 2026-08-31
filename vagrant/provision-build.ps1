<#
.SYNOPSIS
    Build the Odysseus Tauri launcher from a pinned commit inside the VM.

.DESCRIPTION
    Every input this build depends on is pinned by the Vagrantfile and asserted
    here before it is used:

      1. Chocolatey client and package versions, installed with --require-checksums.
      2. The rustup installer, verified by SHA-256, and an exact Rust toolchain.
      3. The WebView2 bootstrapper, verified by Authenticode publisher.
      4. An exact tauri-cli version, installed with --locked.
      5. The source commit, fetched by object id and re-read from the checkout.
      6. The frontend entry that tauri.conf.json points at.
      7. The NSIS installer this harness advertises.

    Any mismatch throws and provisioning fails. On success the script writes
    C:\OdysseusBuild\build-receipt.json recording every identity above plus the
    SHA-256 of each artefact. No receipt means no trustworthy build.

    The build happens entirely inside the VM; nothing touches your host.

.NOTES
    Run as Administrator (Vagrant handles this via privileged=true).
    Requires ~15 GB free disk and outbound internet access.
#>

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------
function Get-PinnedInput {
    param([Parameter(Mandatory = $true)][string] $Name)

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Pinned input '$Name' was not supplied. Run this through 'vagrant up' - the Vagrantfile owns every pin."
    }
    return $value.Trim()
}

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $Expected,
        [Parameter(Mandatory = $true)][string] $What
    )

    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    $wanted = $Expected.Trim().ToLowerInvariant()
    if ($actual -ne $wanted) {
        throw ("{0} failed its integrity check.`n  expected SHA-256: {1}`n  actual   SHA-256: {2}`n  file: {3}" -f $What, $wanted, $actual, $Path)
    }
    Write-Host "[+] $What SHA-256 verified ($actual)."
    return $actual
}

function Assert-Signature {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $ExpectedSubject,
        [Parameter(Mandatory = $true)][string] $What
    )

    $signature = Get-AuthenticodeSignature -FilePath $Path
    if ($signature.Status -ne "Valid") {
        throw "$What is not validly signed (status: $($signature.Status)). Refusing to execute it."
    }

    $subject = $signature.SignerCertificate.Subject
    if ($subject -notlike $ExpectedSubject) {
        throw ("{0} is signed by an unexpected publisher.`n  expected: {1}`n  actual  : {2}" -f $What, $ExpectedSubject, $subject)
    }
    Write-Host "[+] $What signature verified ($subject)."
    return $subject
}

function Get-NormalisedVersion {
    param([string] $Version)

    # Chocolatey reports 1.0.0.0 where the pin says 1.0.0; trailing zero
    # components are not a version difference.
    return ($Version.Trim() -replace '(\.0)+$', '')
}

function Assert-VersionMatch {
    param(
        [Parameter(Mandatory = $true)][string] $Expected,
        [Parameter(Mandatory = $true)][string] $Actual,
        [Parameter(Mandatory = $true)][string] $What
    )

    if ((Get-NormalisedVersion $Expected) -ne (Get-NormalisedVersion $Actual)) {
        throw ("{0} does not match its pin.`n  pinned : {1}`n  present: {2}" -f $What, $Expected, $Actual)
    }
    Write-Host "[+] $What matches its pin ($Actual)."
}

# -----------------------------------------------------------------
# Pinned inputs, supplied by the Vagrantfile
# -----------------------------------------------------------------
$BoxName            = Get-PinnedInput "ODY_BOX_NAME"
$BoxVersion         = Get-PinnedInput "ODY_BOX_VERSION"
$BoxArchitecture    = Get-PinnedInput "ODY_BOX_ARCHITECTURE"
$BoxSha256          = Get-PinnedInput "ODY_BOX_SHA256"
$SourceRepo         = Get-PinnedInput "ODY_SOURCE_REPO"
$SourceCommit       = (Get-PinnedInput "ODY_SOURCE_COMMIT").ToLowerInvariant()
$RustupVersion      = Get-PinnedInput "ODY_RUSTUP_VERSION"
$RustupSha256       = Get-PinnedInput "ODY_RUSTUP_SHA256"
$RustToolchain      = Get-PinnedInput "ODY_RUST_TOOLCHAIN"
$TauriCliVersion    = Get-PinnedInput "ODY_TAURI_CLI_VERSION"
$ChocolateyVersion  = Get-PinnedInput "ODY_CHOCOLATEY_VERSION"
$ChocolateyPackages = Get-PinnedInput "ODY_CHOCOLATEY_PACKAGES"
$BundleTarget       = Get-PinnedInput "ODY_BUNDLE_TARGET"

if ($SourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw "ODY_SOURCE_COMMIT must be a full 40-character commit SHA, got '$SourceCommit'. A branch or tag name does not pin anything."
}

$RustVersion = ($RustToolchain -split '-')[0]

$CloneDir  = "C:\odysseus-build"
$DeployDir = "C:\OdysseusBuild"
$TestUser  = "OdysseusUser"

Write-Host "[*] Starting build provisioning..." -ForegroundColor Cyan
Write-Host "    source   : $SourceRepo @ $SourceCommit"
Write-Host "    box      : $BoxName $BoxVersion ($BoxArchitecture)"
Write-Host "    toolchain: rust $RustToolchain, tauri-cli $TauriCliVersion"

# -----------------------------------------------------------------
# 1. Chocolatey, pinned
# -----------------------------------------------------------------
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "[*] Installing Chocolatey $ChocolateyVersion..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

    $chocoInstaller = Join-Path $env:TEMP "chocolatey-install.ps1"
    Invoke-WebRequest -Uri "https://community.chocolatey.org/install.ps1" -OutFile $chocoInstaller -UseBasicParsing
    # The bootstrap script is served from a rolling URL, so its digest cannot be
    # pinned. Its Authenticode signature can be, and the version it installs is
    # asserted below.
    Assert-Signature -Path $chocoInstaller -ExpectedSubject "*O=Chocolatey Software, Inc*" -What "Chocolatey bootstrap script" | Out-Null

    $env:chocolateyVersion = $ChocolateyVersion
    & $chocoInstaller
    if ($LASTEXITCODE -ne 0) { throw "Chocolatey bootstrap failed with code $LASTEXITCODE." }
    Remove-Item $chocoInstaller -Force
    $env:PATH += ";$env:ALLUSERSPROFILE\chocolatey\bin"
} else {
    Write-Host "[*] Chocolatey already present."
}

$chocoActual = (& choco --version | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($chocoActual)) { throw "'choco --version' produced no output; the Chocolatey client is not usable." }
Assert-VersionMatch -Expected $ChocolateyVersion -Actual $chocoActual -What "Chocolatey client"

# -----------------------------------------------------------------
# 2. Core build tools, pinned
# -----------------------------------------------------------------
$packagePins = [ordered]@{}
foreach ($entry in ($ChocolateyPackages -split ",")) {
    $parts = $entry.Split("=", 2)
    if ($parts.Count -ne 2) { throw "Malformed package pin '$entry' in ODY_CHOCOLATEY_PACKAGES; expected name=version." }
    $packagePins[$parts[0].Trim()] = $parts[1].Trim()
}

foreach ($name in $packagePins.Keys) {
    $version = $packagePins[$name]
    Write-Host "[*] choco install $name $version"
    & choco install $name --version=$version --require-checksums -y --no-progress --limit-output
    if ($LASTEXITCODE -ne 0) { throw "choco install $name $version failed with code $LASTEXITCODE." }
}

# Refresh environment so new binaries are on PATH. refreshenv only exists once
# Chocolatey's profile module is loaded, which it is not in the session that
# just bootstrapped it.
$chocoRoot = if ($env:ChocolateyInstall) { $env:ChocolateyInstall } else { "$env:ALLUSERSPROFILE\chocolatey" }
$chocoProfile = Join-Path $chocoRoot "helpers\chocolateyProfile.psm1"
if (Test-Path -LiteralPath $chocoProfile) {
    Import-Module $chocoProfile -Force
    refreshenv
}
$env:PATH += ";$env:ProgramFiles\Git\cmd"

$installedPackages = @{}
foreach ($line in (& choco list --limit-output)) {
    $parts = $line.Split("|")
    if ($parts.Count -ge 2) { $installedPackages[$parts[0]] = $parts[1] }
}
foreach ($name in $packagePins.Keys) {
    if (-not $installedPackages.ContainsKey($name)) {
        throw "Package '$name' is not installed after a successful choco run; refusing to build against an unknown tool set."
    }
    Assert-VersionMatch -Expected $packagePins[$name] -Actual $installedPackages[$name] -What "Chocolatey package '$name'"
}

# -----------------------------------------------------------------
# 3. Rustup + exact toolchain
# -----------------------------------------------------------------
if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
    Write-Host "[*] Installing Rust $RustToolchain via rustup $RustupVersion..."
    $rustup = Join-Path $env:TEMP "rustup-init.exe"
    # Versioned archive URL rather than win.rustup.rs, so the digest below is
    # a pin and not a snapshot of whatever is current.
    Invoke-WebRequest -Uri "https://static.rust-lang.org/rustup/archive/$RustupVersion/x86_64-pc-windows-msvc/rustup-init.exe" `
        -OutFile $rustup -UseBasicParsing
    Assert-Sha256 -Path $rustup -Expected $RustupSha256 -What "rustup-init.exe $RustupVersion" | Out-Null

    Start-Process -FilePath $rustup -Wait -ArgumentList @(
        "-y",
        "--no-modify-path",
        "--profile", "minimal",
        "--default-toolchain", $RustToolchain
    )
    Remove-Item $rustup -Force
} else {
    Write-Host "[*] Rust already installed."
}
$env:PATH += ";$env:USERPROFILE\.cargo\bin"

& rustup target add x86_64-pc-windows-msvc | Out-Null
$rustcActual = (((& rustc --version) | Select-Object -First 1) -split ' ')[1]
$cargoActual = (((& cargo --version) | Select-Object -First 1) -split ' ')[1]
Assert-VersionMatch -Expected $RustVersion -Actual $rustcActual -What "rustc"

# -----------------------------------------------------------------
# 4. WebView2 Evergreen Runtime
# -----------------------------------------------------------------
$webview2Subject = "not installed by this run"
$wv2Reg = Get-ChildItem "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
if (-not $wv2Reg) {
    Write-Host "[*] Installing WebView2 Evergreen Runtime..."
    $wv2 = Join-Path $env:TEMP "MicrosoftEdgeWebview2Setup.exe"
    Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $wv2 -UseBasicParsing
    # The evergreen bootstrapper is deliberately a moving target, so it is
    # verified by publisher rather than by digest.
    $webview2Subject = Assert-Signature -Path $wv2 -ExpectedSubject "*O=Microsoft Corporation*" -What "WebView2 bootstrapper"
    Start-Process -FilePath $wv2 -ArgumentList "/silent /install" -Wait
    Remove-Item $wv2 -Force
} else {
    Write-Host "[*] WebView2 already present."
}

# -----------------------------------------------------------------
# 5. Tauri CLI, pinned
# -----------------------------------------------------------------
Write-Host "[*] Installing cargo-tauri CLI $TauriCliVersion..."
& cargo install tauri-cli --version $TauriCliVersion --locked --force | Out-Null
if ($LASTEXITCODE -ne 0) { throw "cargo install tauri-cli $TauriCliVersion failed with code $LASTEXITCODE." }
$tauriCliActual = (((& cargo tauri --version) | Select-Object -First 1) -split ' ')[-1]
Assert-VersionMatch -Expected $TauriCliVersion -Actual $tauriCliActual -What "tauri-cli"

# -----------------------------------------------------------------
# 6. Fetch the pinned commit
# -----------------------------------------------------------------
if (Test-Path $CloneDir) {
    Remove-Item -Recurse -Force $CloneDir
}
New-Item -ItemType Directory -Force -Path $CloneDir | Out-Null

Write-Host "[*] Fetching $SourceCommit from $SourceRepo..."
Push-Location $CloneDir
try {
    & git init --quiet
    & git remote add origin $SourceRepo
    # Fetch the commit by object id. A branch that has moved, been rewritten or
    # been force-pushed away from this commit fails here, by design.
    & git fetch --depth 1 origin $SourceCommit
    if ($LASTEXITCODE -ne 0) {
        throw "git fetch of $SourceCommit from $SourceRepo failed. The pinned commit must still exist and be reachable in that repository."
    }
    & git checkout --quiet --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw "git checkout of $SourceCommit failed with code $LASTEXITCODE." }
    $checkedOutCommit = (& git rev-parse HEAD).Trim().ToLowerInvariant()
} finally {
    Pop-Location
}

if ($checkedOutCommit -ne $SourceCommit) {
    throw ("The checkout is not the pinned commit.`n  pinned   : {0}`n  checked out: {1}" -f $SourceCommit, $checkedOutCommit)
}
Write-Host "[+] Source pinned to $checkedOutCommit."

# -----------------------------------------------------------------
# 7. Assert the release configuration is buildable as tracked
# -----------------------------------------------------------------
# Tauri only checks that the frontendDist directory exists, and this harness
# does not generate a placeholder to paper over a missing one: a release
# definition that is not complete in tracked source is a failure to report,
# not a gap to fill in locally.
$confPath = Join-Path $CloneDir "src-tauri\tauri.conf.json"
if (-not (Test-Path -LiteralPath $confPath -PathType Leaf)) {
    throw "src-tauri\tauri.conf.json is missing from the checkout ($confPath)."
}
$conf = Get-Content -LiteralPath $confPath -Raw | ConvertFrom-Json

$frontendDist = $conf.build.frontendDist
if ([string]::IsNullOrWhiteSpace($frontendDist)) {
    throw "tauri.conf.json does not set build.frontendDist, so a release build has no frontend entry to package."
}
if ($frontendDist -match '^https?://') {
    throw "build.frontendDist is a URL ('$frontendDist'). This harness builds release artefacts from tracked assets, not from a dev server."
}

$frontendPath = [System.IO.Path]::GetFullPath((Join-Path (Join-Path $CloneDir "src-tauri") $frontendDist))
if (-not (Test-Path -LiteralPath $frontendPath -PathType Container)) {
    throw ("build.frontendDist points at '{0}', which does not exist in the checkout.`n  resolved: {1}`nThe release configuration must reference assets that are tracked in the repository." -f $frontendDist, $frontendPath)
}
$frontendEntry = Join-Path $frontendPath "index.html"
if (-not (Test-Path -LiteralPath $frontendEntry -PathType Leaf)) {
    throw ("build.frontendDist ('{0}') exists but has no index.html entry point.`n  looked for: {1}`nTauri checks only that the directory exists, so the build would succeed and ship a window with nothing to load." -f $frontendDist, $frontendEntry)
}
Write-Host "[+] Release frontend entry present: $frontendEntry"

# -----------------------------------------------------------------
# 8. Build release binary + installer
# -----------------------------------------------------------------
Push-Location "$CloneDir\src-tauri"
try {
    Write-Host "[*] Building Odysseus Tauri launcher (release, bundle: $BundleTarget) ..." -ForegroundColor Cyan
    # --bundles is required, not cosmetic: tauri.conf.json declares no `bundle`
    # object and Tauri v2 defaults bundle.active to false, so without this the
    # CLI produces the portable executable and no installer at all.
    & cargo tauri build --target x86_64-pc-windows-msvc --bundles $BundleTarget
    if ($LASTEXITCODE -ne 0) { throw "cargo tauri build failed with code $LASTEXITCODE" }
} finally {
    Pop-Location
}

# -----------------------------------------------------------------
# 9. Stage artefacts, requiring both of them
# -----------------------------------------------------------------
$releaseDir = "$CloneDir\src-tauri\target\x86_64-pc-windows-msvc\release"
New-Item -ItemType Directory -Force -Path $DeployDir | Out-Null

$portable = Join-Path $releaseDir "odysseus.exe"
if (-not (Test-Path -LiteralPath $portable -PathType Leaf)) {
    throw "The release build produced no portable executable at $portable."
}
Copy-Item -LiteralPath $portable -Destination "$DeployDir\odysseus.exe" -Force
Write-Host "[+] Portable binary: $DeployDir\odysseus.exe"

$bundleDir = Join-Path $releaseDir "bundle\$BundleTarget"
$installers = @()
if (Test-Path -LiteralPath $bundleDir -PathType Container) {
    $installers = @(Get-ChildItem -LiteralPath $bundleDir -Filter "*.exe" -File)
}
if ($installers.Count -eq 0) {
    throw @"
The '$BundleTarget' bundle produced no installer executable.
  looked in: $bundleDir
This harness advertises a Windows installer, so a build without one is a
failure and not a partial success. Check the 'cargo tauri build' output above
for the bundling step; if it did not run at all, src-tauri/tauri.conf.json
needs an explicit 'bundle' object with the '$BundleTarget' target enabled.
"@
}
foreach ($installer in $installers) {
    Copy-Item -LiteralPath $installer.FullName -Destination (Join-Path $DeployDir $installer.Name) -Force
    Write-Host "[+] Installer: $DeployDir\$($installer.Name)"
}

# -----------------------------------------------------------------
# 10. Build receipt
# -----------------------------------------------------------------
$artefacts = @()
foreach ($file in (Get-ChildItem -LiteralPath $DeployDir -Filter "*.exe" -File)) {
    $artefacts += [ordered]@{
        name   = $file.Name
        role   = if ($file.Name -eq "odysseus.exe") { "portable" } else { "installer" }
        bytes  = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$packageReport = [ordered]@{}
foreach ($name in $packagePins.Keys) {
    $packageReport[$name] = [ordered]@{
        pinned    = $packagePins[$name]
        installed = $installedPackages[$name]
    }
}

$receipt = [ordered]@{
    schemaVersion = 1
    generatedUtc  = (Get-Date).ToUniversalTime().ToString("o")
    harness       = "vagrant/provision-build.ps1"
    supportLevel  = "smoke-test build; artefacts are unsigned and are not a supported release"
    vm = [ordered]@{
        box          = $BoxName
        boxVersion   = $BoxVersion
        architecture = $BoxArchitecture
        boxSha256    = $BoxSha256
        os           = (Get-CimInstance Win32_OperatingSystem).Caption
        osVersion    = (Get-CimInstance Win32_OperatingSystem).Version
    }
    source = [ordered]@{
        repository       = $SourceRepo
        pinnedCommit     = $SourceCommit
        checkedOutCommit = $checkedOutCommit
    }
    toolchain = [ordered]@{
        rustupVersion    = $RustupVersion
        rustupSha256     = $RustupSha256
        rustToolchain    = $RustToolchain
        rustc            = $rustcActual
        cargo            = $cargoActual
        tauriCli         = $tauriCliActual
        chocolatey       = $chocoActual
        webview2Publisher = $webview2Subject
    }
    packages = $packageReport
    release = [ordered]@{
        frontendDist  = $frontendDist
        frontendEntry = $frontendEntry
        bundleTarget  = $BundleTarget
        target        = "x86_64-pc-windows-msvc"
    }
    artifacts = $artefacts
}

$receiptPath = Join-Path $DeployDir "build-receipt.json"
$receipt | ConvertTo-Json -Depth 6 | Out-File -LiteralPath $receiptPath -Encoding utf8
Write-Host "[+] Build receipt: $receiptPath"

# Checksums, for a quick eyeball without parsing the receipt
Get-ChildItem -LiteralPath $DeployDir -File | Where-Object { $_.Name -ne "checksums.sha256" } |
    Get-FileHash -Algorithm SHA256 |
    Out-File -FilePath "$DeployDir\checksums.sha256"

# -----------------------------------------------------------------
# 11. Place shortcut on the test user's desktop
# -----------------------------------------------------------------
$desktop = "C:\Users\$TestUser\Desktop"
New-Item -ItemType Directory -Force -Path $desktop | Out-Null

# Copy the portable .exe directly to the desktop so the user can just double-click it
Copy-Item -Path "$DeployDir\odysseus.exe" -Destination "$desktop\odysseus.exe" -Force

# Also create a .lnk for convenience
$Wsh = New-Object -ComObject WScript.Shell
$lnk = $Wsh.CreateShortcut("$desktop\Odysseus.lnk")
$lnk.TargetPath = "$DeployDir\odysseus.exe"
$lnk.WorkingDirectory = $DeployDir
$lnk.IconLocation = "$DeployDir\odysseus.exe,0"
$lnk.Save()

# Grant the user read/execute access to the deploy directory
$acl = Get-Acl $DeployDir
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($TestUser, "ReadAndExecute", "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.SetAccessRule($rule)
Set-Acl $DeployDir $acl

Write-Host "`n[+] Build provisioning complete." -ForegroundColor Green
Write-Host "    Commit     : $checkedOutCommit"
Write-Host "    Executable : $DeployDir\odysseus.exe"
Write-Host "    SHA-256    : $( (Get-FileHash "$DeployDir\odysseus.exe" -Algorithm SHA256).Hash )"
Write-Host "    Receipt    : $receiptPath"
Write-Host ""
Write-Host "    To use it, log in to the VM as '$TestUser' and double-click"
Write-Host "    the Odysseus icon on the Desktop."
