$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$folder = Join-Path $PSScriptRoot ('PAD0_diagnostic_' + $stamp)
New-Item -ItemType Directory -Path $folder | Out-Null
$report = [ordered]@{ kind='independent_window_diagnostic'; diagnostic_version=2; powershell=$PSVersionTable.PSVersion.ToString(); started=(Get-Date).ToString('o'); samples=@(); errors=@() }
try {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public static class Pad0WindowDiagnostic {
    public delegate bool Callback(IntPtr hwnd, IntPtr parameter);
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int left,top,right,bottom; }
    [DllImport("user32.dll")] public static extern bool EnumWindows(Callback callback, IntPtr parameter);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint process);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int size);
    [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
    public static string Title(IntPtr hwnd) { var text=new StringBuilder(1024); GetWindowText(hwnd,text,text.Capacity); return text.ToString(); }
    public static long[] Windows() { var list=new List<long>(); EnumWindows((h,p)=> {list.Add(h.ToInt64());return true;},IntPtr.Zero); return list.ToArray(); }
    public static int[] Bounds(IntPtr hwnd) { Rect r; GetWindowRect(hwnd,out r); return new int[]{r.left,r.top,r.right-r.left,r.bottom-r.top}; }
    public static long ProcessId(IntPtr hwnd) { uint p; GetWindowThreadProcessId(hwnd,out p); return p; }
}
'@
    Write-Host 'Recording window state for 12 seconds. Keep PAD0 running.'
    Write-Host 'No game clicks or key presses are generated. No screenshots are taken.'
    for ($index=0; $index -lt 12; $index++) {
        $front = [Pad0WindowDiagnostic]::GetForegroundWindow()
        $rows = @()
        foreach ($value in [Pad0WindowDiagnostic]::Windows()) {
            $handle = [IntPtr]$value
            $title = [Pad0WindowDiagnostic]::Title($handle)
            if ($title -notmatch '(?i)PAD0|퍼드|올인원|MuMu|Android Device|Puzzle Dragons') { continue }
            $processId = [Pad0WindowDiagnostic]::ProcessId($handle)
            $processName = $null
            try { $processName = (Get-Process -Id $processId -ErrorAction Stop).ProcessName } catch {}
            $rows += [ordered]@{
                hwnd=$value; title=$title; process_id=$processId; process_name=$processName
                root=[Pad0WindowDiagnostic]::GetAncestor($handle,2).ToInt64()
                root_owner=[Pad0WindowDiagnostic]::GetAncestor($handle,3).ToInt64()
                visible=[Pad0WindowDiagnostic]::IsWindowVisible($handle)
                enabled=[Pad0WindowDiagnostic]::IsWindowEnabled($handle)
                minimized=[Pad0WindowDiagnostic]::IsIconic($handle)
                bounds=[Pad0WindowDiagnostic]::Bounds($handle)
            }
        }
        $report.samples += [ordered]@{
            time=(Get-Date).ToString('o'); foreground=$front.ToInt64()
            foreground_root=[Pad0WindowDiagnostic]::GetAncestor($front,2).ToInt64()
            foreground_root_owner=[Pad0WindowDiagnostic]::GetAncestor($front,3).ToInt64()
            left_mouse_down=([Pad0WindowDiagnostic]::GetAsyncKeyState(1) -lt 0)
            right_mouse_down=([Pad0WindowDiagnostic]::GetAsyncKeyState(2) -lt 0)
            windows=$rows
        }
        $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $folder 'report.json') -Encoding UTF8
        Start-Sleep -Milliseconds 1000
    }
} catch {
    $report.errors += $_.Exception.ToString()
} finally {
    $report.finished=(Get-Date).ToString('o')
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $folder 'report.json') -Encoding UTF8
    $zip = $folder + '.zip'
    try {
        Compress-Archive -LiteralPath (Join-Path $folder 'report.json') -DestinationPath $zip
        Write-Host ('Saved: ' + $zip)
    } catch { Write-Host ('ZIP failed; send report.json from: ' + $folder) }
}
