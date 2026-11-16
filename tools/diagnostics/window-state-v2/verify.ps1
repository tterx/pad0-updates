$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Android Device - PAD0 diagnostic CI fixture'
$form.Show()
try {
    & (Join-Path $PSScriptRoot 'collect.ps1')
    $latest = Get-ChildItem -LiteralPath $PSScriptRoot -Directory -Filter 'PAD0_diagnostic_*' | Sort-Object Name -Descending | Select-Object -First 1
    $data = Get-Content -Raw -LiteralPath (Join-Path $latest.FullName 'report.json') | ConvertFrom-Json
    if ($data.samples.Count -ne 12 -or $data.errors.Count -ne 0) { throw 'Incomplete diagnostic: expected 12 samples and no errors' }
    foreach ($sample in $data.samples) {
        $found = @($sample.windows | Where-Object { $_.title -eq $form.Text -and $_.process_id -eq $PID })
        if ($found.Count -ne 1) { throw 'Real Win32 fixture process/window missing' }
        if (-not $found[0].visible -or $found[0].bounds.Count -ne 4) { throw 'Real Win32 fixture geometry missing' }
    }
    if (-not (Test-Path -LiteralPath ($latest.FullName + '.zip'))) { throw 'ZIP missing' }
    Write-Host ('PASS: PowerShell ' + $PSVersionTable.PSVersion + '; 12 samples; native fixture process IDs, geometry and ZIP verified')
} finally { $form.Close(); $form.Dispose() }
