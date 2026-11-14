#Requires -RunAsAdministrator
<#
  Lets the Calco app on the iPhone reach this computer over Wi-Fi.

  Run it yourself, once, in an administrator PowerShell:
      powershell -ExecutionPolicy Bypass -File tools\allow-wifi.ps1

  What it changes (and nothing else):
    1. Networks currently marked "Public" become "Private" (your home/office network).
       Public tells Windows to refuse every incoming connection.
    2. Removes the firewall rules that BLOCK this Python from receiving connections
       (Windows creates them when its firewall prompt is closed without allowing).
    3. Adds one rule "Calco" that ALLOWS this Python on Private networks only
       (port 7878 for the uploads, 5353 so the phone finds the computer by itself).

  Not needed for the USB cable: that path does not go through the firewall.
#>

$python = (& python -c "import sys; print(getattr(sys, '_base_executable', sys.executable))").Trim()
if (-not (Test-Path $python)) { throw "Python not found ($python). Install Python 3.12 first." }
Write-Host "Python used by Calco: $python"

# 1. Public -> Private
Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | ForEach-Object {
    Write-Host "Network '$($_.InterfaceAlias)': Public -> Private"
    Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
}

# 2. Remove block rules for this Python
Get-NetFirewallApplicationFilter | Where-Object { $_.Program -eq $python } | Get-NetFirewallRule |
    Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } | ForEach-Object {
        Write-Host "Removing blocking rule '$($_.DisplayName)'"
        Remove-NetFirewallRule -Name $_.Name
    }

# 3. One explicit allow rule, private networks only
Get-NetFirewallRule -DisplayName 'Calco' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Calco' -Description 'Calco engine: uploads from the iPhone (7878) and discovery (5353)' `
    -Direction Inbound -Program $python -Action Allow -Profile Private | Out-Null
Write-Host "Added rule 'Calco' (allow, private networks only)."
Write-Host "Done. In Calco on the computer, the network warning disappears within a minute."
