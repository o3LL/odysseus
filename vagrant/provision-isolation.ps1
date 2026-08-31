<#
.SYNOPSIS
    Isolate the VM and create a standard user to run the launcher.

.DESCRIPTION
    - Firewall: block all inbound connections, allow outbound.
    - Create a standard local user (OdysseusUser) who is NOT an administrator.
    - Enable the local Guest account only if needed, but default is off.

.NOTES
    Run as Administrator (Vagrant handles this via privileged=true).
#>

$ErrorActionPreference = "Stop"

Write-Host "[*] Hardening VM isolation..." -ForegroundColor Cyan

# 1. Firewall - block inbound, log blocked packets
$logDir = "C:\Windows\system32\LogFiles\Firewall"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Set-NetFirewallProfile -Profile Domain,Public,Private `
    -DefaultInboundAction Block `
    -DefaultOutboundAction Allow `
    -LogFileName "$logDir\pfirewall.log" `
    -LogMaxSizeKilobytes 32768 `
    -LogBlocked True | Out-Null

Write-Host "[+] Firewall: inbound blocked, outbound allowed."

# 2. Create a standard user for interactive testing
$userName = "OdysseusUser"
$password = ConvertTo-SecureString "Odysseus123!" -AsPlainText -Force

if (-not (Get-LocalUser -Name $userName -ErrorAction SilentlyContinue)) {
    New-LocalUser -Name $userName -Password $password `
        -FullName "Odysseus Test User" `
        -Description "Standard user for running the Odysseus launcher" | Out-Null
    # Explicitly remove from Administrators in case defaults differ
    Remove-LocalGroupMember -Group "Administrators" -Member $userName -ErrorAction SilentlyContinue
    Write-Host "[+] Standard user '$userName' created."
} else {
    Write-Host "[*] User '$userName' already exists."
}

# Allow RDP for this user (the eval box usually has RDP on, but enforce)
Add-LocalGroupMember -Group "Remote Desktop Users" -Member $userName -ErrorAction SilentlyContinue | Out-Null

Write-Host "[*] Isolation complete." -ForegroundColor Green
